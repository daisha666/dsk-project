"""
dsk_Project
自動化: Googleスプレッドシート「操作パネル」シート管理
Version 0.2

PROJECT_EVのautomation/sheet_control_panel.pyと同じ設計（チェック行を
ポーリングし、チェックが入ったら該当処理を実行、完了後に自動でOFFへ戻す。
row5はオッズ自動更新の状態フラグで、ユーザー操作の対象ではない）を踏襲。

行構成:
  row2: ①データ取得・検証・予想生成（出馬表取得→predict_race.py）
  row3: ②オッズ取得・予想更新（tfwオッズ再取得→期待値再計算、軽量・モデル再学習なし）
  row4: ③結果取得・検証（結果取得→prediction_verification.py）
  row5: オッズ自動更新の状態フラグ（開催日9:30〜17:00・5分おき。
        automation/odds_auto_refresh_job.pyが自動でON/OFFする。ユーザーは
        変更しない。B5=状態〈TRUE/FALSE〉 C5=最終更新時刻）

列構成: A=処理名 B=実行チェック C=ステータス D=開始時刻 E=完了時刻 F=所要時間(秒) G=最新ログ
"""

import sys
from datetime import datetime
from pathlib import Path

import gspread

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from automation.sheet_config import CONTROL_PANEL_SHEET_NAME, SPREADSHEET_ID, require_spreadsheet_id
from prediction.sheets_report import get_client

HEADER = ["処理名", "実行", "ステータス", "開始時刻", "完了時刻", "所要時間(秒)", "最新ログ"]

JOBS = [
    {"key": "denma_predict", "row": 2, "label": "①データ取得・検証・予想生成"},
    {"key": "odds_refresh", "row": 3, "label": "②オッズ取得・予想更新"},
    {"key": "result_verify", "row": 4, "label": "③結果取得・検証"},
]

AUTO_REFRESH_ROW = 5
AUTO_REFRESH_LABEL = "オッズ自動更新（開催日9:30〜17:00・5分おき・自動ON/OFF）"
AUTO_REFRESH_SWITCH_CELL = f"B{AUTO_REFRESH_ROW}"
AUTO_REFRESH_LAST_UPDATED_CELL = f"C{AUTO_REFRESH_ROW}"


def get_sheet():
    require_spreadsheet_id()
    gc = get_client()
    return gc.open_by_key(SPREADSHEET_ID)


def ensure_control_panel(sh, log=print):
    """「操作パネル」シートが無ければ作成する。既にあれば、既存のチェック状態・
    ステータスは壊さず、ヘッダーと行ラベルだけ整合させる"""
    is_new = False
    try:
        ws = sh.worksheet(CONTROL_PANEL_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=CONTROL_PANEL_SHEET_NAME, rows=10, cols=10)
        is_new = True
        log(f"「{CONTROL_PANEL_SHEET_NAME}」シートを新規作成")

    if is_new:
        values = [HEADER] + [[j["label"], False, "待機中", "", "", "", ""] for j in JOBS]
        ws.update(values, "A1", value_input_option="USER_ENTERED")
        ws.format("A1:G1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)

        requests = [{
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1, "endRowIndex": 1 + len(JOBS),
                    "startColumnIndex": 1, "endColumnIndex": 2,
                },
                "rule": {"condition": {"type": "BOOLEAN"}, "strict": True},
            }
        }]
        sh.batch_update({"requests": requests})
    else:
        existing_values = ws.get_all_values()
        for j in JOBS:
            row_exists = len(existing_values) >= j["row"] and existing_values[j["row"] - 1]
            if not row_exists:
                ws.update([[j["label"], False, "待機中", "", "", "", ""]],
                          f"A{j['row']}", value_input_option="USER_ENTERED")

    _ensure_auto_refresh_row(sh, ws, log=log)

    return ws


def _ensure_auto_refresh_row(sh, ws, log=print):
    """row5（オッズ自動更新スイッチ）が無ければ追加する。既にあれば、
    現在のON/OFF状態・最終更新時刻は壊さずラベル（A列）だけ揃える"""
    existing_values = ws.get_all_values()
    row_exists = len(existing_values) >= AUTO_REFRESH_ROW and existing_values[AUTO_REFRESH_ROW - 1]
    if row_exists:
        return

    ws.update([[AUTO_REFRESH_LABEL, False, ""]], f"A{AUTO_REFRESH_ROW}", value_input_option="USER_ENTERED")
    requests = [{
        "setDataValidation": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": AUTO_REFRESH_ROW - 1, "endRowIndex": AUTO_REFRESH_ROW,
                "startColumnIndex": 1, "endColumnIndex": 2,
            },
            "rule": {"condition": {"type": "BOOLEAN"}, "strict": True},
        }
    }]
    sh.batch_update({"requests": requests})
    log(f"「{AUTO_REFRESH_LABEL}」行を追加")


def get_auto_refresh_switch(ws):
    value = ws.acell(AUTO_REFRESH_SWITCH_CELL).value
    return str(value).strip().upper() == "TRUE"


def set_auto_refresh_switch(ws, on, log=print):
    ws.update([[bool(on)]], AUTO_REFRESH_SWITCH_CELL, value_input_option="USER_ENTERED")
    log(f"オッズ自動更新スイッチを{'ON' if on else 'OFF'}にしました")


def set_auto_refresh_last_updated(ws, when=None):
    when = when or datetime.now()
    ws.update([[when.strftime("%Y-%m-%d %H:%M:%S")]], AUTO_REFRESH_LAST_UPDATED_CELL,
              value_input_option="USER_ENTERED")


def read_jobs(ws):
    """各ジョブ行のチェック状態・現在のステータスを読み込む"""
    values = ws.get_all_values()
    jobs = []
    for j in JOBS:
        row = values[j["row"] - 1] if len(values) >= j["row"] else []
        checked = len(row) > 1 and row[1].strip().upper() == "TRUE"
        status = row[2] if len(row) > 2 else ""
        jobs.append({**j, "checked": checked, "status": status})
    return jobs


def set_job_running(ws, job, log=print):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws.update([[True, "実行中", now, "", "", ""]], f"B{job['row']}:G{job['row']}",
              value_input_option="USER_ENTERED")
    log(f"[{job['label']}] 実行開始")


def update_job_progress(ws, job, message, log=print):
    """処理中の進捗を「最新ログ」列にだけ書き込む。書き込み失敗（ネットワーク瞬断・
    クォータ超過等）が本体の処理を止めないよう、失敗時は例外を投げずログに残すだけ"""
    try:
        ws.update([[message]], f"G{job['row']}", value_input_option="USER_ENTERED")
    except Exception as exc:
        log(f"[{job['label']}] 進捗書き込みに失敗（処理は継続）: {exc}")


def set_job_done(ws, job, start_time, message, success=True, log=print):
    end = datetime.now()
    duration = (end - start_time).total_seconds()
    status = "完了" if success else "エラー"
    ws.update(
        [[False, status, start_time.strftime("%Y-%m-%d %H:%M:%S"),
          end.strftime("%Y-%m-%d %H:%M:%S"), f"{duration:.0f}", message]],
        f"B{job['row']}:G{job['row']}",
        value_input_option="USER_ENTERED",
    )
    log(f"[{job['label']}] {status}（{duration:.0f}秒）: {message}")
    return duration


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("操作パネル セットアップ")
    print("=" * 40)

    sheet = get_sheet()
    ensure_control_panel(sheet)
    print("セットアップ完了")
