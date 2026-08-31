"""
dsk_Project
特徴量生成: 脚質力 (kyakushitsu_power)
Version 0.2

開発指示書 2.2「脚質力」:
  元データ = 脚質（逃げ/先行/差し/追込）。他の7項目と違い正規化はせず、
  脚質区分ごとの重み値（condition_weights.weight_nige/senko/sashi/oikomi）を
  そのまま代入する。

脚質区分の推定ロジック（Yahoo競馬の出馬表・結果ページには脚質そのものの
表記が無いため、過去走のresults.passing（通過順位）から推定する。新規実装）:
  1. 各過去走について、passing（例:"06-06"や"01-01-02-03"）をコーナーごとの
     通過順位のリストにパースし、その平均をそのレースの出走頭数
     （field_size）で割って「早い/遅いの相対位置（0=先頭付近, 1=最後方付近）」
     を求める。
  2. 直近5走（agari_power.pyと同じ近走件数）のその相対位置を平均する。
  3. 平均相対位置をしきい値で4区分に分類する:
       <= 0.15        : 逃げ（先頭に近い位置を維持）
       0.15 < x <= 0.40: 先行
       0.40 < x <= 0.70: 差し
       > 0.70          : 追込（最後方から追い込む）
     しきい値は「逃げ・追込は少数派、先行・差しが多数派」という一般的な
     脚質分布のイメージに基づく暫定値。実際の分布を見て調整の余地がある。
  4. 過去走が1件も無い（新馬等）場合はNone（分類不能）とし、kyakushitsu_power
     もNoneのまま保存する（開発指示書の方針通り、新馬戦は学習・バックテスト
     対象から除外するため、この場合の扱いが結果に与える影響は小さい）。

kyakushitsu_power に保存する値は、他の7項目と異なりMin-Max正規化を行わず
condition_weightsの該当区分の重み値をそのまま使う（重み適用済みの値という
意味では他の項目とoverall_score算出時の扱いは同じ）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import (
    get_past_races, group_by_race, load_condition_weights, load_horse_histories,
)

LOOKBACK = 5

NIGE_MAX = 0.15
SENKO_MAX = 0.40
SASHI_MAX = 0.70


def parse_passing(passing):
    """"06-06" -> [6, 6] / "01-01-02-03" -> [1, 1, 2, 3]。パース不能ならNone"""
    if not passing:
        return None
    try:
        return [int(p) for p in passing.split("-")]
    except ValueError:
        return None


def compute_relative_position(passing, field_size):
    """1走分の相対位置（0=先頭付近, 1=最後方付近）を返す。算出不能ならNone"""
    positions = parse_passing(passing)
    if not positions or not field_size or field_size <= 1:
        return None
    avg_position = sum(positions) / len(positions)
    # 頭数1頭（あり得ないが防御的に）の場合は上のfield_size<=1判定で弾く
    return (avg_position - 1) / (field_size - 1)


def classify_running_style(relative_position):
    """平均相対位置から脚質区分（逃げ/先行/差し/追込）を判定する。Noneならそのまま返す"""
    if relative_position is None:
        return None
    if relative_position <= NIGE_MAX:
        return "逃げ"
    if relative_position <= SENKO_MAX:
        return "先行"
    if relative_position <= SASHI_MAX:
        return "差し"
    return "追込"


STYLE_WEIGHT_KEY = {"逃げ": "nige", "先行": "senko", "差し": "sashi", "追込": "oikomi"}


class KyakushitsuPowerFeatureBuilder:
    """過去の通過順位から脚質区分を推定し、区分ごとの重み値をkyakushitsu_powerとして
    featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def estimate_running_style(self, history, target_race_date):
        """直近LOOKBACK走の相対位置の平均から脚質区分を推定する。
        戻り値: (脚質区分 or None, 使用したレース数)"""
        past = get_past_races(history, target_race_date, limit=LOOKBACK)

        relative_positions = []
        for p in past:
            rel = compute_relative_position(p["passing"], p["field_size"])
            if rel is not None:
                relative_positions.append(rel)

        if not relative_positions:
            return None, 0

        avg_relative_position = sum(relative_positions) / len(relative_positions)
        return classify_running_style(avg_relative_position), len(relative_positions)

    def save_feature(self, race_id, horse_id, kyakushitsu_power):
        sql = """
            INSERT INTO features (race_id, horse_id, kyakushitsu_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                kyakushitsu_power = excluded.kyakushitsu_power
        """
        self.db.execute(sql, (race_id, horse_id, kyakushitsu_power))

    def fetch_race_targets(self):
        return self.db.fetchall("""
            SELECT e.race_id, e.horse_id, r.race_date, r.course, r.surface, r.distance
            FROM entries e
            JOIN races r ON r.race_id = e.race_id
            ORDER BY r.race_date, e.race_id
        """)

    def build(self, log=print):
        """entriesテーブルにある全レースについて脚質区分を推定し、
        condition_weightsの該当区分の重み値をkyakushitsu_powerとして保存する
        （他の7項目と異なりレース内正規化は行わない）"""
        histories = load_horse_histories(self.db)
        weights = load_condition_weights(self.db)
        race_groups = group_by_race(self.fetch_race_targets())

        stats = {
            "total": 0, "with_data": 0, "no_style_data": 0, "no_condition_weight": 0,
            "style_counts": {"逃げ": 0, "先行": 0, "差し": 0, "追込": 0},
        }

        for race_id, rows in race_groups.items():
            _, _, _, course, surface, distance = rows[0]
            weight_row = weights.get((course, surface, distance))

            for r_id, horse_id, race_date, _course, _surface, _distance in rows:
                stats["total"] += 1
                history = histories.get(horse_id, [])
                style, n = self.estimate_running_style(history, race_date)

                if style is None:
                    self.save_feature(race_id, horse_id, None)
                    stats["no_style_data"] += 1
                    continue

                if weight_row is None:
                    self.save_feature(race_id, horse_id, None)
                    stats["no_condition_weight"] += 1
                    continue

                self.save_feature(race_id, horse_id, weight_row[STYLE_WEIGHT_KEY[style]])
                stats["with_data"] += 1
                stats["style_counts"][style] += 1

        log(f"完了: 総件数={stats['total']} 算出={stats['with_data']} "
            f"脚質推定不可={stats['no_style_data']} 重みマスターなし={stats['no_condition_weight']} "
            f"内訳={stats['style_counts']}")

        return stats


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("KyakushitsuPowerFeatureBuilder 実行")
    print("=" * 40)

    builder = KyakushitsuPowerFeatureBuilder()
    builder.build()
