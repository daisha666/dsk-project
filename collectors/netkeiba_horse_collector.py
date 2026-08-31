"""
dsk_Project
netkeiba（db.netkeiba.com）: 血統・過去成績 収集
Version 0.1 (TODO: 未実装 / 土台のみ)

対応範囲（開発指示書 2.1）:
  - 血統（父・母父） -> horses.sire / horses.damsire
  - 過去成績（着順・上がり3F・通過順位） -> results.finish_position /
    results.last3f / results.passing の補完・突合

TODO:
  - netkeiba馬ページ・血統ページのHTML構造を調査して実装する
    （PROJECT_EVのcollectors/_run_pedigree_backfill.pyが同種の処理を
    netkeiba向けに行っている設計を参考にできる）
  - horses への保存処理（INSERT OR REPLACE）
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.base_collector import BaseCollector
from database.db_manager import DatabaseManager


class NetkeibaHorseCollector(BaseCollector):
    """netkeibaから馬の血統・過去成績を取得するクラス"""

    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()

    def collect_pedigree(self, horse_id):
        """父・母父を取得する（TODO: 未実装）"""
        raise NotImplementedError("netkeiba血統ページ構造を調査して実装する")

    def collect_race_history(self, horse_id):
        """着順・上がり3F・通過順位の過去成績を取得する（TODO: 未実装）"""
        raise NotImplementedError("netkeiba馬ページの成績表構造を調査して実装する")


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("NetkeibaHorseCollector 読み込み成功（未実装）")
    print("=" * 40)
