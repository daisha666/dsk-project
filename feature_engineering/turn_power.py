"""
dsk_Project
特徴量生成: 回り力 (turn_power)
Version 0.2

開発指示書 2.2「回り力」:
  元データ = 回り複勝率（対象レースの回り[右/左]における、その馬の過去の
  複勝率＝3着内率）。レース内Min-Max正規化してから
  condition_weights.weight_turn を掛ける。

回り（direction）の分類はPROJECT_EVのfeature_engineering/course_aptitude.pyの
classify_turn()と同じ（races.directionの先頭文字が"右"/"左"かで分類、
"直線"のように該当しないものはNone＝集計対象外）。

turn_power に保存する値は「レース内Min-Max正規化後 × condition_weights.weight_turn」
の最終値（重み適用済み）。
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


def classify_turn(direction):
    """direction文字列から回り（右/左）を判定する。直線コース等はNone"""
    if not direction:
        return None
    if direction.startswith("右"):
        return "右"
    if direction.startswith("左"):
        return "左"
    return None


class TurnPowerFeatureBuilder:
    """回り別の複勝率（回り複勝率）から回り力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_turn_place_rate(self, history, target_race_date, target_direction):
        """対象レースと同じ回り（右/左）における通算複勝率（3着内率）を返す。
        戻り値: (複勝率 or None, 使用したレース数)"""
        target_turn = classify_turn(target_direction)
        if target_turn is None:
            return None, 0

        past_races = get_past_races(history, target_race_date, limit=None)
        matching = [
            p for p in past_races
            if p["finish_position"] is not None and classify_turn(p["direction"]) == target_turn
        ]

        if not matching:
            return None, 0

        places = sum(1 for p in matching if p["finish_position"] <= 3)
        return places / len(matching), len(matching)

    def save_feature(self, race_id, horse_id, turn_power):
        sql = """
            INSERT INTO features (race_id, horse_id, turn_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                turn_power = excluded.turn_power
        """
        self.db.execute(sql, (race_id, horse_id, turn_power))

    def fetch_race_targets(self):
        return self.db.fetchall("""
            SELECT e.race_id, e.horse_id, r.race_date, r.course, r.surface, r.distance, r.direction
            FROM entries e
            JOIN races r ON r.race_id = e.race_id
            ORDER BY r.race_date, e.race_id
        """)

    def build(self, log=print):
        """entriesテーブルにある全レースについて、レース内で回り力を
        Min-Max正規化し、condition_weights.weight_turnを掛けて保存する"""
        histories = load_horse_histories(self.db)
        weights = load_condition_weights(self.db)
        race_groups = group_by_race(self.fetch_race_targets())

        stats = {"total": 0, "with_data": 0, "no_data": 0, "no_condition_weight": 0}

        for race_id, rows in race_groups.items():
            _, _, _, course, surface, distance, _direction = rows[0]
            weight_row = weights.get((course, surface, distance))

            raw_values = {}
            for r_id, horse_id, race_date, _course, _surface, _distance, direction in rows:
                history = histories.get(horse_id, [])
                rate, _n = self.compute_turn_place_rate(history, race_date, direction)
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

                self.save_feature(race_id, horse_id, norm_value * weight_row["turn"])
                stats["with_data"] += 1

        log(f"完了: 総件数={stats['total']} 算出={stats['with_data']} "
            f"回りデータなし={stats['no_data']} 重みマスターなし={stats['no_condition_weight']}")

        return stats


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("TurnPowerFeatureBuilder 実行")
    print("=" * 40)

    builder = TurnPowerFeatureBuilder()
    builder.build()
