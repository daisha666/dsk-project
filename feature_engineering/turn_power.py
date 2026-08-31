"""
dsk_Project
特徴量生成: 回り力 (turn_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「回り力」:
  元データ = 回り複勝率（対象レースの回り[右/左]における、その馬の過去の連対率）。
  レース内Min-Max正規化してから condition_weights.weight_turn を掛ける。

  PROJECT_EVのfeature_engineering/course_aptitude.pyのclassify_turn()相当の
  ロジック（races.directionの先頭文字が"右"/"左"かで分類、"直線"等はNone）を
  そのまま踏襲できる。

TODO:
  - classify_turn() の実装（course_aptitude.py参照）
  - common.get_past_races で対象レースより前の同回りレースに絞り込み、連対率を算出
  - レース内Min-Max正規化＋重み適用
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import get_past_races, load_horse_histories


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
    """回り別の連対率（回り複勝率）から回り力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def save_feature(self, race_id, horse_id, turn_power):
        sql = """
            INSERT INTO features (race_id, horse_id, turn_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                turn_power = excluded.turn_power
        """
        self.db.execute(sql, (race_id, horse_id, turn_power))

    def build(self, log=print):
        raise NotImplementedError("正規化・重み付けロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("TurnPowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
