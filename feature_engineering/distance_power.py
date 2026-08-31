"""
dsk_Project
特徴量生成: 距離力 (distance_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「距離力」:
  元データ = 距離複勝率（対象レースの距離区分における、その馬の過去の連対率）。
  レース内Min-Max正規化してから condition_weights.weight_distance を掛ける。

  PROJECT_EVのfeature_engineering/distance_aptitude.pyと同じ設計
  （リーケージ防止・「対象レースより前の全レースを対象にした距離区分別成績」）
  を踏襲できる。距離区分の定義（短距離/マイル/中距離/長距離 等）を決める必要がある。

TODO:
  - 距離区分の定義を決める
  - common.get_past_races で対象レースより前の同区分レースに絞り込み、連対率を算出
  - レース内Min-Max正規化＋重み適用
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import get_past_races, load_horse_histories


class DistancePowerFeatureBuilder:
    """距離区分別の連対率（距離複勝率）から距離力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def classify_distance(self, distance):
        """TODO: 距離区分の定義を決めて実装する（例: 短距離/マイル/中距離/長距離）"""
        raise NotImplementedError("距離区分の定義を決めて実装する")

    def save_feature(self, race_id, horse_id, distance_power):
        sql = """
            INSERT INTO features (race_id, horse_id, distance_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                distance_power = excluded.distance_power
        """
        self.db.execute(sql, (race_id, horse_id, distance_power))

    def build(self, log=print):
        raise NotImplementedError("距離区分定義・正規化ロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("DistancePowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
