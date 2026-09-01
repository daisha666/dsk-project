"""
dsk_Project
自動化: Googleスプレッドシート「検証結果」シート管理
Version 0.1

開発指示書の「シンプルな2タブ構成」の一方。実運用の予測ログ
（analysis/prediction_verification.py）を確定結果と突き合わせた成績を、
実行のたびに1行ずつ追記していく（トレンドを目で追えるようにするため）。
上部にはStage3で確定した基準値（README「Stage3としての基準値」）を
静的に表示し、いつでも見返せるようにする。
"""

import sys
from datetime import datetime
from pathlib import Path

import gspread

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from automation.sheet_config import SPREADSHEET_ID, VERIFICATION_SHEET_NAME, require_spreadsheet_id
from prediction.predict_race import CLASS_FILTER, EV_THRESHOLD, ODDS_CAP
from prediction.sheets_report import get_client

SETTINGS_BLOCK = [
    ["Stage3確定基準（README「Stage3としての基準値」参照。ピンポイントではなく帯の代表値）"],
    ["モデル", "モデルA'（overall_score + odds_adjusted_score + 市場情報）"],
    ["EV閾値", str(EV_THRESHOLD)],
    ["オッズ上限", f"{ODDS_CAP}倍"],
    ["クラスフィルタ", "1勝クラス以上限定" if CLASS_FILTER else "全クラス"],
    [""],
]

HISTORY_HEADER = ["更新日時", "対象予測数(結果確定済み)", "買い目推奨数", "的中数",
                   "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]
HISTORY_START_ROW = len(SETTINGS_BLOCK) + 1


def get_sheet():
    require_spreadsheet_id()
    gc = get_client()
    return gc.open_by_key(SPREADSHEET_ID)


def ensure_verification_sheet(sh, log=print):
    """「検証結果」シートが無ければ作成し、設定ブロック・履歴ヘッダーを書き込む"""
    try:
        ws = sh.worksheet(VERIFICATION_SHEET_NAME)
        return ws
    except gspread.WorksheetNotFound:
        pass

    ws = sh.add_worksheet(title=VERIFICATION_SHEET_NAME, rows=1000, cols=10)
    log(f"「{VERIFICATION_SHEET_NAME}」シートを新規作成")

    values = SETTINGS_BLOCK + [HISTORY_HEADER]
    ws.update(values, "A1", value_input_option="USER_ENTERED")
    ws.format(f"A{HISTORY_START_ROW}:I{HISTORY_START_ROW}", {"textFormat": {"bold": True}})
    ws.freeze(rows=HISTORY_START_ROW)

    return ws


def append_verification_result(ws, result, log=print):
    """analysis/prediction_verification.py::summarize()の戻り値を1行追記する"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [
        now,
        result["対象予測数（結果確定済み）"],
        result["買い目推奨数"],
        result["的中数"],
        round(result["的中率(%)"], 2) if result["買い目推奨数"] else "",
        result["総購入額(円)"],
        round(result["総払戻額(円)"], 0),
        round(result["回収率(%)"], 2) if result["買い目推奨数"] else "",
        result["払戻欠損"],
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    log(f"検証結果シートへ1行追記: {row}")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("検証結果シート セットアップ")
    print("=" * 40)

    sheet = get_sheet()
    ensure_verification_sheet(sheet)
    print("セットアップ完了")
