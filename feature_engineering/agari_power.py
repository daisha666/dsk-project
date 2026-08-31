"""
dsk_Project
特徴量生成: 上がり力 (agari_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「上がり力」:
  元データ = 過去5戦平均3F。速いほど高得点になるよう反転して正規化してから
  condition_weights.weight_agari を掛ける。

TODO:
  - 過去5戦平均上がり3F（common.get_past_races limit=5）の算出
  - レース内Min-Max正規化＋反転（速い＝高得点）
  - condition_weights から該当 (course, surface, distance) の重みを取得して乗算
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import get_past_races, load_horse_histories


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

    def compute_features(self, history, target_race_date):
        """TODO: レース内正規化・重み適用は build() 側でレース単位にまとめて行う"""
        raise NotImplementedError("正規化・重み付けロジックを実装する")

    def save_feature(self, race_id, horse_id, agari_power):
        sql = """
            INSERT INTO features (race_id, horse_id, agari_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                agari_power = excluded.agari_power
        """
        self.db.execute(sql, (race_id, horse_id, agari_power))

    def build(self, log=print):
        raise NotImplementedError("土台のみ実装。正規化・重み付けロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("AgariPowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
