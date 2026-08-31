"""
dsk_Project
特徴量生成: 距離力 (distance_power)
Version 0.2

開発指示書 2.2「距離力」:
  元データ = 距離複勝率（対象レースと同じ距離区分における、その馬の過去の
  複勝率＝3着内率）。レース内Min-Max正規化してから
  condition_weights.weight_distance を掛ける。

距離区分の定義（PROJECT_EVのfeature_engineering/distance_aptitude.pyと同じ）:
  短距離: 1400m以下 / 中距離: 1401m〜2000m / 長距離: 2001m以上

distance_power に保存する値は「レース内Min-Max正規化後 ×
condition_weights.weight_distance」の最終値（重み適用済み）。
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

SHORT_MAX = 1400
MIDDLE_MAX = 2000


def classify_distance(distance):
    """距離(m)から距離区分（短距離/中距離/長距離）を判定する"""
    if distance is None:
        return None
    if distance <= SHORT_MAX:
        return "短距離"
    if distance <= MIDDLE_MAX:
        return "中距離"
    return "長距離"


class DistancePowerFeatureBuilder:
    """距離区分別の複勝率（距離複勝率）から距離力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_distance_place_rate(self, history, target_race_date, target_distance):
        """対象レースと同じ距離区分における通算複勝率（3着内率）を返す。
        戻り値: (複勝率 or None, 使用したレース数)"""
        target_category = classify_distance(target_distance)
        if target_category is None:
            return None, 0

        past_races = get_past_races(history, target_race_date, limit=None)
        matching = [
            p for p in past_races
            if p["finish_position"] is not None and classify_distance(p["distance"]) == target_category
        ]

        if not matching:
            return None, 0

        places = sum(1 for p in matching if p["finish_position"] <= 3)
        return places / len(matching), len(matching)

    def save_feature(self, race_id, horse_id, distance_power):
        sql = """
            INSERT INTO features (race_id, horse_id, distance_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                distance_power = excluded.distance_power
        """
        self.db.execute(sql, (race_id, horse_id, distance_power))

    def fetch_race_targets(self):
        return self.db.fetchall("""
            SELECT e.race_id, e.horse_id, r.race_date, r.course, r.surface, r.distance
            FROM entries e
            JOIN races r ON r.race_id = e.race_id
            ORDER BY r.race_date, e.race_id
        """)

    def build(self, log=print):
        """entriesテーブルにある全レースについて、レース内で距離力を
        Min-Max正規化し、condition_weights.weight_distanceを掛けて保存する"""
        histories = load_horse_histories(self.db)
        weights = load_condition_weights(self.db)
        race_groups = group_by_race(self.fetch_race_targets())

        stats = {"total": 0, "with_data": 0, "no_data": 0, "no_condition_weight": 0}

        for race_id, rows in race_groups.items():
            _, _, _, course, surface, distance = rows[0]
            weight_row = weights.get((course, surface, distance))

            raw_values = {}
            for r_id, horse_id, race_date, _course, _surface, target_distance in rows:
                history = histories.get(horse_id, [])
                rate, _n = self.compute_distance_place_rate(history, race_date, target_distance)
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

                self.save_feature(race_id, horse_id, norm_value * weight_row["distance"])
                stats["with_data"] += 1

        log(f"完了: 総件数={stats['total']} 算出={stats['with_data']} "
            f"距離区分データなし={stats['no_data']} 重みマスターなし={stats['no_condition_weight']}")

        return stats


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("DistancePowerFeatureBuilder 実行")
    print("=" * 40)

    builder = DistancePowerFeatureBuilder()
    builder.build()
