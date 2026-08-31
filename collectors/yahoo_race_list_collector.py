"""
dsk_Project
Yahoo競馬（sports.yahoo.co.jp/keiba）: レース一覧・出馬表・払戻金 収集
Version 0.1 (TODO: 未実装 / 土台のみ)

対応範囲（開発指示書 2.1）:
  - 出走表（races, entries）
  - 結果（results）
  - 払戻金（payouts）

TODO:
  - Yahoo競馬のURL構造・HTML構造を調査し、パース処理を実装する
    （PROJECT_EVのcollectors/historical_race_collector.py・
    race_list_collector.pyがnetkeiba向けに実装している設計を参考にできる）
  - races / entries / results / payouts への保存処理（INSERT OR REPLACE）
  - リーケージ防止のため、取得日時（retrieved_at相当）の記録を検討する
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.base_collector import BaseCollector
from database.db_manager import DatabaseManager


class YahooRaceListCollector(BaseCollector):
    """Yahoo競馬からレース一覧・出馬表・結果・払戻金を取得するクラス"""

    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()

    def collect_race_list(self, date):
        """指定日のレースID一覧を取得する（TODO: 未実装）"""
        raise NotImplementedError("Yahoo競馬のレース一覧ページ構造を調査して実装する")

    def collect_entries(self, race_id):
        """出馬表（entries）を取得する（TODO: 未実装）"""
        raise NotImplementedError("Yahoo競馬の出馬表ページ構造を調査して実装する")

    def collect_results(self, race_id):
        """結果（results）・払戻金（payouts）を取得する（TODO: 未実装）"""
        raise NotImplementedError("Yahoo競馬の結果ページ構造を調査して実装する")


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("YahooRaceListCollector 読み込み成功（未実装）")
    print("=" * 40)
