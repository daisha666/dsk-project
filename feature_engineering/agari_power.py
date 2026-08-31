"""
dsk_Project
特徴量生成: 上がり力 (agari_power)
Version 0.2

開発指示書 2.2「上がり力」:
  元データ = 過去5戦平均3F。速いほど高得点になるよう反転して正規化してから
  condition_weights.weight_agari を掛ける。

上がり力（agari_power）に保存する値は「レース内Min-Max正規化（反転）後 ×
condition_weights.weight_agari」の最終値（＝overall_score算出時にそのまま
合算できる、重み適用済みの値）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import (
    fetch_targets, get_past_races, group_by_race,
    load_condition_weights, load_horse_histories, normalize_min_max,
)


class AgariPowerFeatureBuilder:
    """過去5戦平均上がり3Fから上がり力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_avg_last3f(self, history, target_race_date):
        """直近5走の平均上がり3Fを返す（データが無ければNone）"""
        past = get_past_races(history, target_race_date, limit=5)
        values = [p["last3f"] for p in past if p["last3f"] is not None]
        if not values:
            return None
        return sum(values) / len(values)

    def save_feature(self, race_id, horse_id, agari_power):
        sql = """
            INSERT INTO features (race_id, horse_id, agari_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                agari_power = excluded.agari_power
        """
        self.db.execute(sql, (race_id, horse_id, agari_power))

    def fetch_race_targets(self):
        """entriesにある全 (race_id, horse_id, race_date, course, surface, distance) を返す"""
        return self.db.fetchall("""
            SELECT e.race_id, e.horse_id, r.race_date, r.course, r.surface, r.distance
            FROM entries e
            JOIN races r ON r.race_id = e.race_id
            ORDER BY r.race_date, e.race_id
        """)

    def build(self, log=print):
        """entriesテーブルにある全レースについて、レース内で上がり力を
        Min-Max正規化（反転）し、condition_weights.weight_agariを掛けて保存する"""
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
                raw_values[horse_id] = self.compute_avg_last3f(history, race_date)

            normalized = normalize_min_max(raw_values, invert=True)

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

                self.save_feature(race_id, horse_id, norm_value * weight_row["agari"])
                stats["with_data"] += 1

        log(f"完了: 総件数={stats['total']} 算出={stats['with_data']} "
            f"上がりデータなし={stats['no_data']} 重みマスターなし={stats['no_condition_weight']}")

        return stats


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("AgariPowerFeatureBuilder 実行")
    print("=" * 40)

    builder = AgariPowerFeatureBuilder()
    builder.build()
