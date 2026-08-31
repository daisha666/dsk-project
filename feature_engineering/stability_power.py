"""
dsk_Project
特徴量生成: 安定力 (stability_power)
Version 0.2

開発指示書 2.2「安定力」:
  元データ = 全成績連対率（対象レースより前の全レースを対象にした通算連対率。
  他の項目が「複勝率＝3着内率」なのに対し、安定力だけは仕様上明示的に
  「連対率＝2着内率」。距離・回りなどの絞り込みをしない全体成績という点も
  distance_power/turn_powerと異なる）。レース内Min-Max正規化してから
  condition_weights.weight_stability を掛ける。

stability_power に保存する値は「レース内Min-Max正規化後 ×
condition_weights.weight_stability」の最終値（重み適用済み）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import (
    get_past_races, group_by_race, load_condition_weights,
    load_horse_histories, normalize_min_max,
)


class StabilityPowerFeatureBuilder:
    """通算連対率（2着内率）から安定力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_place_rate(self, history, target_race_date):
        """通算連対率（finish_position<=2の割合）を返す（過去走が無ければNone）"""
        past = get_past_races(history, target_race_date, limit=None)
        finished = [p for p in past if p["finish_position"] is not None]
        if not finished:
            return None
        places = sum(1 for p in finished if p["finish_position"] <= 2)
        return places / len(finished)

    def save_feature(self, race_id, horse_id, stability_power):
        sql = """
            INSERT INTO features (race_id, horse_id, stability_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                stability_power = excluded.stability_power
        """
        self.db.execute(sql, (race_id, horse_id, stability_power))

    def fetch_race_targets(self):
        return self.db.fetchall("""
            SELECT e.race_id, e.horse_id, r.race_date, r.course, r.surface, r.distance
            FROM entries e
            JOIN races r ON r.race_id = e.race_id
            ORDER BY r.race_date, e.race_id
        """)

    def build(self, log=print):
        """entriesテーブルにある全レースについて、レース内で安定力を
        Min-Max正規化し、condition_weights.weight_stabilityを掛けて保存する"""
        histories = load_horse_histories(self.db)
        weights = load_condition_weights(self.db)
        race_groups = group_by_race(self.fetch_race_targets())

        stats = {"total": 0, "with_data": 0, "no_data": 0, "no_condition_weight": 0}

        for race_id, rows in race_groups.items():
            _, _, _, course, surface, distance = rows[0]
            weight_row = weights.get((course, surface, distance))

            raw_values = {}
            for r_id, horse_id, race_date, _course, _surface, _distance in rows:
                history = histories.get(horse_id, [])
                raw_values[horse_id] = self.compute_place_rate(history, race_date)

            normalized = normalize_min_max(raw_values, invert=False)

            for horse_id, norm_value in normalized.items():
                stats["total"] += 1

                if norm_value is None:
                    self.save_feature(race_id, horse_id, None)
                    stats["no_data"] += 1
                    continue

                if weight_row is None:
                    self.save_feature(race_id, horse_id, None)
                    stats["no_condition_weight"] += 1
                    continue

                self.save_feature(race_id, horse_id, norm_value * weight_row["stability"])
                stats["with_data"] += 1

        log(f"完了: 総件数={stats['total']} 算出={stats['with_data']} "
            f"過去走なし={stats['no_data']} 重みマスターなし={stats['no_condition_weight']}")

        return stats


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("StabilityPowerFeatureBuilder 実行")
    print("=" * 40)

    builder = StabilityPowerFeatureBuilder()
    builder.build()
