"""
dsk_Project
自動化: オッズ自動更新（開催日限定）ジョブ
Version 0.1

PROJECT_EVのautomation/odds_auto_refresh_job.pyと同じ設計。「操作パネル」の
row2〜4のチェック（watcher.pyがポーリング）とは独立して、Windowsタスク
スケジューラから直接、開催日の9:30〜17:00の間に5分おきに起動される専用ジョブ。
トリガー自体は毎日固定（平日・休日問わず9:30〜17:00に5分おき）だが、
このスクリプト自身が以下でガードするため、非開催日には実質何もしない。

  1. 本日（実行時点のシステム日付）に開催レースが1件も無ければ、
     何もせず終了する。
  2. 本日開催があり、まだ全レース終了していなければ、B5（オッズ自動更新
     スイッチ）を人の操作なしで自動的にONにする（既にONならそのまま）。
     このスイッチは「現在オッズ自動更新が稼働中かどうか」をアプリ側に
     表示するための状態フラグ（automation/gas/odds_refresh_webapp.gs経由）。

最終レース終了後の自動OFF:
  本日の全レースについてresultsテーブルに結果が確定していれば「本日の
  レースは終了した」とみなし、スイッチを自動でOFFに戻して終了する
  （オッズ更新は行わない）。これにより翌日に持ち越さない。

実際のオッズ更新処理はautomation/odds_refresh_job.py::run_odds_refresh_job()を
そのまま呼び出す（重複実装を避けるため）。
"""

import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from automation.odds_refresh_job import run_odds_refresh_job
from automation.sheet_control_panel import (
    ensure_control_panel,
    get_auto_refresh_switch,
    get_sheet,
    set_auto_refresh_last_updated,
    set_auto_refresh_switch,
)
from database.db_manager import DatabaseManager


def has_races_today(db, today_str):
    return bool(db.fetchone("SELECT 1 FROM races WHERE race_date = ? LIMIT 1", (today_str,)))


def all_todays_races_finished(db, today_str):
    """本日開催の全レースについて、結果が1件でも未確定のレースが無ければTrue
    （＝本日の開催は終了したとみなす）"""
    unfinished = db.fetchone("""
        SELECT 1 FROM races r
        WHERE r.race_date = ?
          AND NOT EXISTS (SELECT 1 FROM results res WHERE res.race_id = r.race_id AND res.finish_position IS NOT NULL)
        LIMIT 1
    """, (today_str,))
    return unfinished is None


def run_odds_auto_refresh_job(log=print, today=None):
    """today: 省略時は実行時点のシステム日付（本番運用はこちら）。
    テスト時に過去/架空の開催日を指定して動作確認するために引数化している"""
    db = DatabaseManager()
    if today is None:
        today = date.today()
    today_str = today.isoformat()

    if not has_races_today(db, today_str):
        return f"本日（{today_str}）は開催レースが無いため、何もせず終了しました"

    sh = get_sheet()
    ws = ensure_control_panel(sh, log=log)

    if all_todays_races_finished(db, today_str):
        if get_auto_refresh_switch(ws):
            set_auto_refresh_switch(ws, False, log=log)
            return f"本日（{today_str}）の全レースが終了したため、オッズ自動更新スイッチをOFFに戻しました"
        return f"本日（{today_str}）の全レースは既に終了しています（スイッチは既にOFF）"

    if not get_auto_refresh_switch(ws):
        set_auto_refresh_switch(ws, True, log=log)
        log(f"本日（{today_str}）は開催日で未終了のため、オッズ自動更新スイッチを自動でONにしました")

    log(f"本日（{today_str}）開催中 -> オッズ更新を実行します")
    message = run_odds_refresh_job(log=log)
    set_auto_refresh_last_updated(ws)
    return f"[オッズ自動更新] {message}"


if __name__ == "__main__":
    print(run_odds_auto_refresh_job())
