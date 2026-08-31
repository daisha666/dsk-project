"""
dsk_Project
特徴量生成: 血統力 (pedigree_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「血統力」:
  元データ = 種牡馬複勝率（対象馬の父（horses.sire）が持つ産駒全体の連対率）。
  レース内Min-Max正規化してから condition_weights.weight_pedigree を掛ける。

  PROJECT_EVのfeature_engineering/pedigree_aptitude.pyと同じ設計
  （common.load_pedigree_histories("sire")を使い、対象馬自身の過去走は
  集計から除外する）を踏襲できる。

TODO:
  - common.load_pedigree_histories("sire") + get_past_races で
    対象レースより前の産駒成績に絞り込み、連対率を算出
  - レース内Min-Max正規化＋重み適用
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import get_past_races, load_pedigree_histories


class PedigreePowerFeatureBuilder:
    """種牡馬（父）産駒の連対率から血統力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def save_feature(self, race_id, horse_id, pedigree_power):
        sql = """
            INSERT INTO features (race_id, horse_id, pedigree_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                pedigree_power = excluded.pedigree_power
        """
        self.db.execute(sql, (race_id, horse_id, pedigree_power))

    def build(self, log=print):
        raise NotImplementedError("netkeiba血統収集実装・正規化ロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("PedigreePowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
