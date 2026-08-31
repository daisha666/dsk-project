"""
dsk_Project
ittai.net: 騎手・調教師リーディング成績（複勝率） 収集
Version 0.1 (TODO: 未実装 / 土台のみ)

対応範囲（開発指示書 2.1・2.2）:
  - 騎手複勝率 -> jockey_trainer_leading (entity_type='jockey')
  - 調教師複勝率 -> jockey_trainer_leading (entity_type='trainer')

このデータは特徴量生成時の jockey_power / trainer_power の元データになる
（database/schema.sql の jockey_trainer_leading テーブル参照）。
リーケージ防止のため、特徴量生成時は対象レースのrace_date以前に
取得したスナップショット（retrieved_at）だけを使うこと。

TODO:
  - ittai.netのリーディングページのHTML構造を調査して実装する
  - jockey_trainer_leading への保存処理（INSERT）
  - 取得頻度（例: 週1回）の検討
"""

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.base_collector import BaseCollector
from database.db_manager import DatabaseManager


class IttaiLeadingCollector(BaseCollector):
    """ittai.netから騎手・調教師のリーディング成績を取得するクラス"""

    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()

    def collect_jockey_leading(self):
        """騎手複勝率一覧を取得する（TODO: 未実装）"""
        raise NotImplementedError("ittai.net騎手リーディングページ構造を調査して実装する")

    def collect_trainer_leading(self):
        """調教師複勝率一覧を取得する（TODO: 未実装）"""
        raise NotImplementedError("ittai.net調教師リーディングページ構造を調査して実装する")

    def save_leading(self, entity_type, entity_name, place_rate):
        """jockey_trainer_leadingへ保存する"""
        sql = """
            INSERT INTO jockey_trainer_leading (
                entity_type, entity_name, place_rate, retrieved_at
            ) VALUES (?, ?, ?, ?)
        """
        params = (entity_type, entity_name, place_rate, datetime.now().isoformat())
        self.db.execute(sql, params)


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("IttaiLeadingCollector 読み込み成功（未実装）")
    print("=" * 40)
