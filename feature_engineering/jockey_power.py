"""
dsk_Project
特徴量生成: 騎手力 (jockey_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「騎手力」:
  元データ = 騎手複勝率（ittai.netリーディング、jockey_trainer_leadingテーブル）。
  レース内Min-Max正規化してから condition_weights.weight_jockey を掛ける。

TODO:
  - jockey_trainer_leading (entity_type='jockey') から、対象レースの
    race_date以前に取得した最新スナップショットを引く（リーケージ防止）
  - レース内Min-Max正規化＋重み適用
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


class JockeyPowerFeatureBuilder:
    """騎手複勝率（ittai.netリーディング）から騎手力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def fetch_latest_place_rate(self, jockey_name, as_of_date):
        """jockey_trainer_leadingから、as_of_date以前で最新のplace_rateを取得する"""
        sql = """
            SELECT place_rate FROM jockey_trainer_leading
            WHERE entity_type = 'jockey' AND entity_name = ? AND retrieved_at <= ?
            ORDER BY retrieved_at DESC
            LIMIT 1
        """
        row = self.db.fetchone(sql, (jockey_name, as_of_date))
        return row[0] if row else None

    def save_feature(self, race_id, horse_id, jockey_power):
        sql = """
            INSERT INTO features (race_id, horse_id, jockey_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                jockey_power = excluded.jockey_power
        """
        self.db.execute(sql, (race_id, horse_id, jockey_power))

    def build(self, log=print):
        raise NotImplementedError("ittai_leading_collector実装・正規化ロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("JockeyPowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
