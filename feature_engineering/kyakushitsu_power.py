"""
dsk_Project
特徴量生成: 脚質力 (kyakushitsu_power)
Version 0.1 (TODO: 未実装 / 土台のみ)

開発指示書 2.2「脚質力」:
  元データ = 脚質（逃げ/先行/差し/追込）。他の7項目と違い正規化はせず、
  脚質区分ごとの重み値をそのまま代入する。

TODO:
  - entries.running_style（逃げ/先行/差し/追込）の取得元を確定する
    （Yahoo競馬の出馬表に脚質表記がない場合、過去走のpassing（通過順位）
    から脚質を推定するロジックが必要になる可能性がある）
  - 脚質区分ごとの重み値テーブル（4区分）の設計
  - pace_bias.py（ペースバイアス補正）との連携方法の整理
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


class KyakushitsuPowerFeatureBuilder:
    """脚質区分から脚質力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def save_feature(self, race_id, horse_id, kyakushitsu_power):
        sql = """
            INSERT INTO features (race_id, horse_id, kyakushitsu_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                kyakushitsu_power = excluded.kyakushitsu_power
        """
        self.db.execute(sql, (race_id, horse_id, kyakushitsu_power))

    def build(self, log=print):
        raise NotImplementedError("脚質判定ロジック・重み値テーブルの設計後に実装する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("KyakushitsuPowerFeatureBuilder 読み込み成功（未実装）")
    print("=" * 40)
