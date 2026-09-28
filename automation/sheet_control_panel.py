"""
dsk_Project
自動化: Googleスプレッドシート「操作パネル」シート管理
Version 0.2

PROJECT_EVのautomation/sheet_control_panel.pyと同じ設計（チェック行を
ポーリングし、チェックが入ったら該当処理を実行、完了後に自動でOFFへ戻す）を踏襲。

行構成:
  row2: ①データ取得・検証・予想生成（出馬表取得→predict_race.py）
  row3: ②オッズ取得・予想更新（tfwオッズ再取得→期待値再計算、軽量・モデル再学習なし）
  row4: ③結果取得・検証（結果取得→prediction_verification.py）
  row5: オッズ自動更新スイッチ（2026-09-08、時間指定トリガー方式からユーザー操作の
        スイッチ方式へ変更。B5をONにするとwatcher.pyが5分おきのポーリングのたびに
        ②オッズ取得・予想更新を実行、OFFで停止。停止し忘れ防止のため当日20時に
        なると自動でOFFに戻る。B5=ON/OFFスイッチ〈TRUE/FALSE、ONで背景が緑色に
        なる〉 C5=最終更新時刻。旧方式〈automation/odds_auto_refresh_job.py、
        Task Schedulerから開催日9:30〜17:00に5分おきで直接起動する独立ジョブが
        完全自動でON/OFFしていた〉は廃止した）

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
AUTO_REFRESH_LABEL = "オッズ自動更新（ONで5分おきに稼働・20時に自動OFF）"
AUTO_REFRESH_SWITCH_CELL = f"B{AUTO_REFRESH_ROW}"
AUTO_REFRESH_LAST_UPDATED_CELL = f"C{AUTO_REFRESH_ROW}"

# Watcher稼働監視（2026-09-11追加）: watcher.pyが毎回のポーリングでB6に
# 現在時刻を書き込む「ハートビート」。C6はNOW()との差分を見る数式で、
# WATCHER_STALE_THRESHOLD_MINUTES分を超えて更新が無ければ「⚠️」表示に切り替わる。
# Task Scheduler側が停止する（9/8に実際発生）と、このB6が古いまま止まるため、
# スプレッドシート・アプリ双方からWatcherの生死を確認できるようにするための仕組み
WATCHER_HEARTBEAT_ROW = 6
WATCHER_HEARTBEAT_LABEL = "Watcher稼働監視（最終ポーリング時刻）"
WATCHER_HEARTBEAT_CELL = f"B{WATCHER_HEARTBEAT_ROW}"
WATCHER_HEARTBEAT_STATUS_CELL = f"C{WATCHER_HEARTBEAT_ROW}"
WATCHER_STALE_THRESHOLD_MINUTES = 60


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
    _ensure_watcher_heartbeat_row(sh, ws, log=log)

    return ws


def _ensure_auto_refresh_row(sh, ws, log=print):
    """row5（オッズ自動更新スイッチ）が無ければ追加する。既にあれば、
    現在のON/OFF状態・最終更新時刻は壊さずラベル（A列）だけ揃える"""
    existing_values = ws.get_all_values()
    row_exists = len(existing_values) >= AUTO_REFRESH_ROW and existing_values[AUTO_REFRESH_ROW - 1]
    if not row_exists:
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

    _ensure_auto_refresh_conditional_format(sh, ws, log=log)


def _ensure_auto_refresh_conditional_format(sh, ws, log=print):
    """スイッチ（B5）がONのとき、A5:C5の背景を緑にする条件付き書式を設定する
    （視覚的にひと目でON/OFFが分かるようにするため。2026-09-08追加）。
    既存シートに対しても、まだ設定が無ければ後付けで追加する（row5自体は
    既に存在する既存シートでも通るよう、ensure_control_panel()から毎回呼ぶ）。
    既に同じ範囲に条件付き書式が設定済みなら何もしない（重複追加を防ぐ）"""
    row_index = AUTO_REFRESH_ROW - 1  # 0-indexed
    meta = sh.fetch_sheet_metadata()
    sheet_meta = next((s for s in meta["sheets"] if s["properties"]["sheetId"] == ws.id), None)
    existing_rules = sheet_meta.get("conditionalFormats", []) if sheet_meta else []
    for rule in existing_rules:
        for rng in rule.get("ranges", []):
            if rng.get("startRowIndex") == row_index:
                return

    request = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": ws.id,
                    "startRowIndex": row_index, "endRowIndex": row_index + 1,
                    "startColumnIndex": 0, "endColumnIndex": 3,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": f"=${AUTO_REFRESH_SWITCH_CELL}=TRUE"}],
                    },
                    "format": {"backgroundColor": {"red": 0.72, "green": 0.93, "blue": 0.75}},
                },
            },
            "index": 0,
        }
    }
    sh.batch_update({"requests": [request]})
    log("オッズ自動更新スイッチの条件付き書式（ON時に緑）を設定")


def _ensure_watcher_heartbeat_row(sh, ws, log=print):
    """row6（Watcher稼働監視）が無ければ追加する。C6には、B6（ハートビート時刻）と
    現在時刻の差がWATCHER_STALE_THRESHOLD_MINUTES分を超えたら⚠️表示に切り替わる
    数式を入れておく（NOW()はスプレッドシートを開いている間、自動再計算される）。
    既にあれば、ラベル・数式・条件付き書式は壊さずそのままにする"""
    existing_values = ws.get_all_values()
    row_exists = len(existing_values) >= WATCHER_HEARTBEAT_ROW and existing_values[WATCHER_HEARTBEAT_ROW - 1]
    if not row_exists:
        status_formula = (
            f'=IF({WATCHER_HEARTBEAT_CELL}="","未実行",'
            f'IF((NOW()-{WATCHER_HEARTBEAT_CELL})*1440>{WATCHER_STALE_THRESHOLD_MINUTES},'
            f'"⚠️ "&TEXT((NOW()-{WATCHER_HEARTBEAT_CELL})*1440,"0")&"分 更新なし","🟢 正常"))'
        )
        ws.update([[WATCHER_HEARTBEAT_LABEL, "", status_formula]],
                  f"A{WATCHER_HEARTBEAT_ROW}", value_input_option="USER_ENTERED")
        log(f"「{WATCHER_HEARTBEAT_LABEL}」行を追加")

    _ensure_watcher_heartbeat_conditional_format(sh, ws, log=log)


def _ensure_watcher_heartbeat_conditional_format(sh, ws, log=print):
    """ハートビートがWATCHER_STALE_THRESHOLD_MINUTES分を超えて更新されていないとき、
    A6:C6の背景を赤くする条件付き書式を設定する（2026-09-11追加）。
    既に同じ範囲に設定済みなら何もしない（重複追加を防ぐ）"""
    row_index = WATCHER_HEARTBEAT_ROW - 1  # 0-indexed
    meta = sh.fetch_sheet_metadata()
    sheet_meta = next((s for s in meta["sheets"] if s["properties"]["sheetId"] == ws.id), None)
    existing_rules = sheet_meta.get("conditionalFormats", []) if sheet_meta else []
    for rule in existing_rules:
        for rng in rule.get("ranges", []):
            if rng.get("startRowIndex") == row_index:
                return

    request = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": ws.id,
                    "startRowIndex": row_index, "endRowIndex": row_index + 1,
                    "startColumnIndex": 0, "endColumnIndex": 3,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{
                            "userEnteredValue":
                                f'=AND(${WATCHER_HEARTBEAT_CELL}<>"",'
                                f'(NOW()-${WATCHER_HEARTBEAT_CELL})*1440>{WATCHER_STALE_THRESHOLD_MINUTES})'
                        }],
                    },
                    "format": {"backgroundColor": {"red": 1.0, "green": 0.42, "blue": 0.42}},
                },
            },
            "index": 0,
        }
    }
    sh.batch_update({"requests": [request]})
    log("Watcher稼働監視の条件付き書式（停止時に赤）を設定")


def set_watcher_heartbeat(ws, when=None):
    """watcher.pyが毎回のポーリングで呼ぶ。ジョブの実行有無に関わらず、
    「今、正常にポーリングできた」ことの証跡としてB6に現在時刻を書き込む"""
    when = when or datetime.now()
    ws.update([[when.strftime("%Y-%m-%d %H:%M:%S")]], WATCHER_HEARTBEAT_CELL,
              value_input_option="USER_ENTERED")


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
