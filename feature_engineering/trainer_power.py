"""
dsk_Project
特徴量生成: 調教師力 (trainer_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「調教師力」:
  元データ = 調教師複勝率（ittai.netリーディング、jockey_trainer_leadingテーブル）。
  レース内Min-Max正規化してから condition_weights.weight_trainer を掛ける。
  ロジックはjockey_power.pyと対称（entity_type='trainer'）。

TODO:
  - jockey_trainer_leading (entity_type='trainer') から、対象レースの
    race_date以前に取得した最新スナップショットを引く（リーケージ防止）
  - レース内Min-Max正規化＋重み適用
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


class TrainerPowerFeatureBuilder:
    """調教師複勝率（ittai.netリーディング）から調教師力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def fetch_latest_place_rate(self, trainer_name, as_of_date):
        """jockey_trainer_leadingから、as_of_date以前で最新のplace_rateを取得する"""
        sql = """
            SELECT place_rate FROM jockey_trainer_leading
            WHERE entity_type = 'trainer' AND entity_name = ? AND retrieved_at <= ?
            ORDER BY retrieved_at DESC
            LIMIT 1
        """
        row = self.db.fetchone(sql, (trainer_name, as_of_date))
        return row[0] if row else None

    def save_feature(self, race_id, horse_id, trainer_power):
        sql = """
            INSERT INTO features (race_id, horse_id, trainer_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                trainer_power = excluded.trainer_power
        """
        self.db.execute(sql, (race_id, horse_id, trainer_power))

    def build(self, log=print):
        raise NotImplementedError("ittai_leading_collector実装・正規化ロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("TrainerPowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
