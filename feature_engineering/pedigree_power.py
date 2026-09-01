"""
dsk_Project
特徴量生成: 血統力 (pedigree_power)
Version 0.2

開発指示書 2.2「血統力」:
  元データ = 種牡馬複勝率（対象馬の父（horses.sire）が持つ産駒全体の、対象レース
  より前の通算複勝率＝3着内率）。レース内Min-Max正規化してから
  condition_weights.weight_pedigree を掛ける。母父（damsire）は開発指示書の
  8項目に含まれていないため対象外。

対象馬自身を集計対象から除外する理由（leave-one-out。PROJECT_EVの
feature_engineering/pedigree_aptitude.pyと同じ方針）:
  sire_place_rateは「血統そのものの傾向」を捉えるための指標であり、対象馬
  自身の実績を混ぜると他の特徴量（安定力等）と情報が重複し、特に産駒数が
  少ない血統では「その馬個体の実績の言い換え」になってしまうため。

同日複数レースのリーケージ防止:
  同じ父を持つ産駒が同日に「異なる競馬場」で出走することもあり得るため、
  common.get_past_races()にtarget_round（race_id末尾2桁のR番号）を渡し、
  (race_date, round)の複合キーで対象レースより前かどうかを判定する
  （PROJECT_EVのpedigree_aptitude.pyと同じ設計・同じ既知の制約を持つ）。

pedigree_power に保存する値は「レース内Min-Max正規化後 ×
condition_weights.weight_pedigree」の最終値（重み適用済み）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import (
    get_past_races, group_by_race, load_condition_weights,
    load_pedigree_histories, normalize_min_max,
)


class PedigreePowerFeatureBuilder:
    """種牡馬（父）産駒の複勝率から血統力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_sire_place_rate(self, histories, sire, horse_id, target_race_date, target_round):
        """
        histories: load_pedigree_histories("sire")の戻り値
        sire: 対象馬の父馬名
        horse_id: 対象馬自身のhorse_id（集計から除外するため）
        戻り値: (複勝率 or None, 使用したレース数)
        """
        if not sire:
            return None, 0

        history = histories.get(sire, [])
        past_races = get_past_races(history, target_race_date, target_round=target_round)

        matching = [
            p for p in past_races
            if p["horse_id"] != horse_id and p["finish_position"] is not None
        ]

        if not matching:
            return None, 0

        places = sum(1 for p in matching if p["finish_position"] <= 3)
        return places / len(matching), len(matching)

    def save_feature(self, race_id, horse_id, pedigree_power):
        sql = """
            INSERT INTO features (race_id, horse_id, pedigree_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                pedigree_power = excluded.pedigree_power
        """
        self.db.execute(sql, (race_id, horse_id, pedigree_power))

    def fetch_race_targets(self):
        return self.db.fetchall("""
            SELECT e.race_id, e.horse_id, r.race_date, r.round, r.course, r.surface, r.distance, h.sire
            FROM entries e
            JOIN races r ON r.race_id = e.race_id
            JOIN horses h ON h.horse_id = e.horse_id
            ORDER BY r.race_date, r.round
        """)

    def build_for_races(self, race_ids, log=print):
        """指定したrace_idだけについて、血統力を再計算・保存する
        （automation/denma_predict_job.pyのような新規レース向けの差分計算用）"""
        sire_histories = load_pedigree_histories("sire", self.db)
        weights = load_condition_weights(self.db)
        target_ids = set(race_ids)
        race_groups = {
            race_id: rows for race_id, rows in group_by_race(self.fetch_race_targets()).items()
            if race_id in target_ids
        }

        stats = {"total": 0, "with_data": 0, "no_data": 0, "no_condition_weight": 0}

        for race_id, rows in race_groups.items():
            _, _, _, _round, course, surface, distance, _sire = rows[0]
            weight_row = weights.get((course, surface, distance))

            raw_values = {}
            for r_id, horse_id, race_date, round_no, _course, _surface, _distance, sire in rows:
                rate, _n = self.compute_sire_place_rate(
                    sire_histories, sire, horse_id, race_date, round_no
                )
                raw_values[horse_id] = rate

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

                self.save_feature(race_id, horse_id, norm_value * weight_row["pedigree"])
                stats["with_data"] += 1

        log(f"完了: 対象レース数={len(target_ids)} 総件数={stats['total']} 算出={stats['with_data']} "
            f"血統データなし={stats['no_data']} 重みマスターなし={stats['no_condition_weight']}")

        return stats

    def build(self, log=print):
        """entriesテーブルにある全レースについて、レース内で血統力を
        Min-Max正規化し、condition_weights.weight_pedigreeを掛けて保存する"""
        all_race_ids = list(group_by_race(self.fetch_race_targets()).keys())
        return self.build_for_races(all_race_ids, log=log)


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("PedigreePowerFeatureBuilder 実行")
    print("=" * 40)

    builder = PedigreePowerFeatureBuilder()
    builder.build()
