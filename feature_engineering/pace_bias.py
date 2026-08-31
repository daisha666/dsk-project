"""
dsk_Project
特徴量生成: ペースバイアス補正 (pace_bias_adjustment)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2:
  総合力 = 8項目の合計 ×（1 + 脚質別ペースバイアス補正）
  ペースバイアス補正はレースごとのペース予想（スローペース想定なら
  差し・追込に加点、など）。

TODO:
  - レースのペース予想ロジックを設計する（出走馬の脚質構成から
    ハイペース/平均/スローペースを推定するなど）
  - 脚質区分（逃げ/先行/差し/追込）ごとの補正値を決める
  - kyakushitsu_power.py（脚質判定）が先に必要
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


class PaceBiasFeatureBuilder:
    """レースごとのペース予想から脚質別バイアス補正を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def predict_pace(self, race_id):
        """TODO: 出走馬の脚質構成からペース（ハイ/平均/スロー）を予想する"""
        raise NotImplementedError("ペース予想ロジックを設計する")

    def save_feature(self, race_id, horse_id, pace_bias_adjustment):
        sql = """
            INSERT INTO features (race_id, horse_id, pace_bias_adjustment)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                pace_bias_adjustment = excluded.pace_bias_adjustment
        """
        self.db.execute(sql, (race_id, horse_id, pace_bias_adjustment))

    def build(self, log=print):
        raise NotImplementedError("脚質判定・ペース予想ロジック実装後に有効化する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("PaceBiasFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
