"""
dsk_Project
特徴量生成: 安定力 (stability_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「安定力」:
  元データ = 全成績連対率（対象レースより前の全レースを対象にした通算連対率。
  距離・回りなどの絞り込みをしない全体成績という点がdistance_power/turn_powerと異なる）。
  レース内Min-Max正規化してから condition_weights.weight_stability を掛ける。

TODO:
  - common.get_past_races（絞り込み無し・limit=None）で通算連対率を算出
  - レース内Min-Max正規化＋重み適用
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import get_past_races, load_horse_histories


class StabilityPowerFeatureBuilder:
    """通算連対率から安定力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_place_rate(self, history, target_race_date):
        """通算連対率を返す（過去走が無ければNone）"""
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

    def build(self, log=print):
        raise NotImplementedError("レース内正規化・重み付けロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("StabilityPowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
