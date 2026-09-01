"""
dsk_Project
自動化: Googleスプレッドシート「検証結果」シート管理
Version 0.2

開発指示書の「シンプルな2タブ構成」の一方。実運用の予測ログ
（analysis/prediction_verification.py）を確定結果と突き合わせた成績を管理する。

PROJECT_EVの検証結果シート（②累積成績サマリー・③グラフ・④月別/年別成績）の
構成を参考にしつつ、dsk_Projectは単勝オンリーのシンプルな設計のため、券種別の
内訳・信頼度別/波乱度別/危険馬判定の答え合わせ等（PROJECT_EV固有の機能）は無く、
以下の4ブロックのみで構成する:
  - Stage3確定基準の表示（既存）
  - ②累積成績サマリー: 結果確定済みの全予測ログを対象にした累積の的中率・回収率
  - 今回（直近）の成績: 直近の検証ジョブで新たに結果が確定した開催日分だけの成績
    （PROJECT_EVの「直近（最新処理分）」に相当。全rowsの中でrace_dateが最新の
    ものを「直近の1回分」とみなす簡易な判定。厳密な「前回実行時からの差分」を
    別途状態管理するより、運用上はこれで十分なため）
  - ④月別成績: race_dateの年月ごとの的中率・回収率
  - ③実行履歴＋グラフ: 実行のたびに1行追記する時系列データ（既存のHISTORY機能を
    継続）。回収率(%)の折れ線グラフの元データにする
"""

import sys
from datetime import datetime
from pathlib import Path

import gspread

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from automation.sheet_config import SPREADSHEET_ID, VERIFICATION_SHEET_NAME, require_spreadsheet_id
from analysis.prediction_verification import filter_most_recent_date, group_by_month, summarize
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

HEADER_FORMAT = {"textFormat": {"bold": True}}
TITLE_FORMAT = {"textFormat": {"bold": True, "fontSize": 12}}
PERCENT_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": '0.0"%"'}}
YEN_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": '#,##0"円"'}}

SUMMARY_HEADER = ["対象予測数(結果確定済み)", "買い目推奨数", "的中数", "的中率(%)",
                   "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]

# ---- グラフ（回収率推移）。行方向は「下の表の下」（表が育つと追いつかれる
#      懸念）を避けるため、常に一定サイズの本グラフを先頭（Stage3確定基準の
#      すぐ下）に固定し、増減する各表はその下に置く。列方向は表（A〜I列）と
#      重ならないよう、実行履歴データが無い状態（Googleスプレッドシートが
#      「データの可視化を開始するには」という空グラフのプレースホルダーを
#      指定サイズと関係なく大きめに描画する）でも重ならないよう、J列より
#      明確に右（L列）にオフセットする ----
CHART_TITLE_ROW = len(SETTINGS_BLOCK) + 1  # 7
CHART_ANCHOR_ROW = CHART_TITLE_ROW  # 0-indexedのrowIndexとしてそのまま使う（1行分のズレでちょうど良い）
CHART_ANCHOR_COL_INDEX = 0  # A列（累積成績サマリーの真上に配置）
CHART_ROW_SPAN = 20  # 380pxのグラフ高さ（既定の行高21px換算で約18行）を収める余裕

# ---- 累積成績サマリー ----
CUMULATIVE_TITLE_ROW = CHART_TITLE_ROW + CHART_ROW_SPAN + 2  # 29
CUMULATIVE_HEADER_ROW = CUMULATIVE_TITLE_ROW + 1  # 30
CUMULATIVE_DATA_ROW = CUMULATIVE_HEADER_ROW + 1  # 31

# ---- 今回（直近）の成績 ----
RECENT_TITLE_ROW = CUMULATIVE_DATA_ROW + 2  # 33
RECENT_HEADER_ROW = RECENT_TITLE_ROW + 1  # 34
RECENT_DATA_ROW = RECENT_HEADER_ROW + 1  # 35

# ---- 月別成績 ----
MONTHLY_TITLE_ROW = RECENT_DATA_ROW + 2  # 37
MONTHLY_HEADER_ROW = MONTHLY_TITLE_ROW + 1  # 38
MONTHLY_DATA_START_ROW = MONTHLY_HEADER_ROW + 1  # 39
MONTHLY_HEADER = ["年月"] + SUMMARY_HEADER
MONTHLY_MAX_ROWS = 40  # 3年強分の余裕（運用しながら足りなくなれば拡張する）

# ---- 実行履歴（グラフ用データ） ----
HISTORY_TITLE_ROW = MONTHLY_DATA_START_ROW + MONTHLY_MAX_ROWS + 1  # 80
HISTORY_HEADER_ROW = HISTORY_TITLE_ROW + 1  # 80
HISTORY_START_ROW = HISTORY_HEADER_ROW + 1  # 81
HISTORY_HEADER = ["更新日時", "対象予測数(結果確定済み)", "買い目推奨数", "的中数",
                   "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]
MAX_CHART_ROWS = 2000


def get_sheet():
    require_spreadsheet_id()
    gc = get_client()
    return gc.open_by_key(SPREADSHEET_ID)


def _summary_row(result):
    return [
        result["対象予測数（結果確定済み）"],
        result["買い目推奨数"],
        result["的中数"],
        round(result["的中率(%)"], 2) if result["買い目推奨数"] else "",
        result["総購入額(円)"],
        round(result["総払戻額(円)"], 0),
        round(result["回収率(%)"], 2) if result["買い目推奨数"] else "",
        result["払戻欠損"],
    ]


def ensure_verification_sheet(sh, log=print):
    """「検証結果」シートが無ければ作成し、全ブロックの見出しを書き込む。
    既にあれば何もしない（レイアウトを変えた場合はrebuild_layout()を使う）"""
    try:
        ws = sh.worksheet(VERIFICATION_SHEET_NAME)
        return ws
    except gspread.WorksheetNotFound:
        pass

    # colsは表の列数（最大I列=9列）ぎりぎりにすると、グラフをそれより右へ
    # 配置しようとした際にAPIエラー（グリッド範囲外）になる・境界ぎりぎりの
    # 列だと描画がおかしくなることがあったため、余裕を持たせる
    ws = sh.add_worksheet(title=VERIFICATION_SHEET_NAME, rows=HISTORY_START_ROW + MAX_CHART_ROWS, cols=30)
    log(f"「{VERIFICATION_SHEET_NAME}」シートを新規作成")
    _write_layout_headers(ws)
    # 行固定（freeze）をグラフのすぐ上/重なる範囲まで効かせると、フローティング
    # オブジェクトであるグラフの描画位置が固定行の境界に引きずられて実際の
    # anchorCellと異なる位置にずれる事象が起きたため、固定は設定ブロックの
    # みにとどめる（グラフの手前で止め、グラフの領域には掛けない）
    ws.freeze(rows=len(SETTINGS_BLOCK))

    return ws


def _write_layout_headers(ws):
    ws.update(SETTINGS_BLOCK, "A1", value_input_option="USER_ENTERED")

    ws.update([["■ 回収率(%)の推移グラフ"]], f"A{CHART_TITLE_ROW}")
    ws.format(f"A{CHART_TITLE_ROW}", TITLE_FORMAT)

    ws.update([["■ 累積成績サマリー（結果確定済みの全予測ログが対象）"]], f"A{CUMULATIVE_TITLE_ROW}")
    ws.format(f"A{CUMULATIVE_TITLE_ROW}", TITLE_FORMAT)
    ws.update([SUMMARY_HEADER], f"A{CUMULATIVE_HEADER_ROW}", value_input_option="USER_ENTERED")
    ws.format(f"A{CUMULATIVE_HEADER_ROW}:H{CUMULATIVE_HEADER_ROW}", HEADER_FORMAT)

    ws.update([["■ 今回（直近）の成績（直近に結果が確定した開催日分のみ）"]], f"A{RECENT_TITLE_ROW}")
    ws.format(f"A{RECENT_TITLE_ROW}", TITLE_FORMAT)
    ws.update([SUMMARY_HEADER], f"A{RECENT_HEADER_ROW}", value_input_option="USER_ENTERED")
    ws.format(f"A{RECENT_HEADER_ROW}:H{RECENT_HEADER_ROW}", HEADER_FORMAT)

    ws.update([["■ 月別成績"]], f"A{MONTHLY_TITLE_ROW}")
    ws.format(f"A{MONTHLY_TITLE_ROW}", TITLE_FORMAT)
    ws.update([MONTHLY_HEADER], f"A{MONTHLY_HEADER_ROW}", value_input_option="USER_ENTERED")
    ws.format(f"A{MONTHLY_HEADER_ROW}:I{MONTHLY_HEADER_ROW}", HEADER_FORMAT)

    ws.update([["■ 実行履歴（上記グラフの元データ。実行のたびに1行追記）"]], f"A{HISTORY_TITLE_ROW}")
    ws.format(f"A{HISTORY_TITLE_ROW}", TITLE_FORMAT)
    ws.update([HISTORY_HEADER], f"A{HISTORY_HEADER_ROW}", value_input_option="USER_ENTERED")
    ws.format(f"A{HISTORY_HEADER_ROW}:I{HISTORY_HEADER_ROW}", HEADER_FORMAT)

    # 的中率・回収率(%)・金額列に表示用の数値書式を設定（累積/直近は1行のみ、
    # 月別/実行履歴は今後増える分の余裕を持たせて範囲指定）
    ws.format(f"D{CUMULATIVE_DATA_ROW}", PERCENT_FORMAT)
    ws.format(f"G{CUMULATIVE_DATA_ROW}", PERCENT_FORMAT)
    ws.format(f"E{CUMULATIVE_DATA_ROW}:F{CUMULATIVE_DATA_ROW}", YEN_FORMAT)
    ws.format(f"D{RECENT_DATA_ROW}", PERCENT_FORMAT)
    ws.format(f"G{RECENT_DATA_ROW}", PERCENT_FORMAT)
    ws.format(f"E{RECENT_DATA_ROW}:F{RECENT_DATA_ROW}", YEN_FORMAT)
    monthly_end = MONTHLY_DATA_START_ROW + MONTHLY_MAX_ROWS - 1
    ws.format(f"E{MONTHLY_DATA_START_ROW}:E{monthly_end}", PERCENT_FORMAT)
    ws.format(f"H{MONTHLY_DATA_START_ROW}:H{monthly_end}", PERCENT_FORMAT)
    ws.format(f"F{MONTHLY_DATA_START_ROW}:G{monthly_end}", YEN_FORMAT)
    history_end = HISTORY_START_ROW + MAX_CHART_ROWS - 1
    ws.format(f"E{HISTORY_START_ROW}:E{history_end}", PERCENT_FORMAT)
    ws.format(f"H{HISTORY_START_ROW}:H{history_end}", PERCENT_FORMAT)
    ws.format(f"F{HISTORY_START_ROW}:G{history_end}", YEN_FORMAT)


def rebuild_layout(sh, log=print):
    """レイアウトを新構成へ作り直す（既存シートを一旦削除して作り直す。
    実行履歴の蓄積データが既にある場合は事前にバックアップを取ってから使うこと）"""
    try:
        ws = sh.worksheet(VERIFICATION_SHEET_NAME)
        sh.del_worksheet(ws)
        log(f"既存の「{VERIFICATION_SHEET_NAME}」シートを削除")
    except gspread.WorksheetNotFound:
        pass

    return ensure_verification_sheet(sh, log=log)


def write_cumulative_summary(ws, rows, log=print):
    result = summarize(rows)
    ws.update([_summary_row(result)], f"A{CUMULATIVE_DATA_ROW}", value_input_option="USER_ENTERED")
    log(f"累積成績サマリー更新: 買い目推奨{result['買い目推奨数']}件 回収率{result['回収率(%)']:.1f}%"
        if result["買い目推奨数"] else "累積成績サマリー更新: 買い目推奨0件")


def write_recent_summary(ws, rows, log=print):
    recent_rows = filter_most_recent_date(rows)
    result = summarize(recent_rows)
    ws.update([_summary_row(result)], f"A{RECENT_DATA_ROW}", value_input_option="USER_ENTERED")
    recent_date = recent_rows[0][3] if recent_rows else "-"
    log(f"直近の成績更新（{recent_date}）: 買い目推奨{result['買い目推奨数']}件")


def write_monthly_table(ws, rows, log=print):
    monthly = group_by_month(rows)
    if len(monthly) > MONTHLY_MAX_ROWS:
        log(f"月別成績: {len(monthly)}か月分だがシート確保分{MONTHLY_MAX_ROWS}行を超えるため直近{MONTHLY_MAX_ROWS}か月のみ表示")
        monthly = monthly[-MONTHLY_MAX_ROWS:]

    table = [[year_month] + _summary_row(summarize(month_rows)) for year_month, month_rows in monthly]
    if table:
        ws.update(table, f"A{MONTHLY_DATA_START_ROW}", value_input_option="USER_ENTERED")
    log(f"月別成績更新: {len(table)}か月分")


def append_verification_result(ws, result, log=print):
    """実行履歴に1行追記する。ws.append_row()は使わず、月別成績の予約領域
    （空セルの範囲）を誤って「最終行」と誤認しないよう、既存の履歴行数を
    数えて明示的な行番号へ書き込む"""
    existing = ws.get(f"A{HISTORY_START_ROW}:A{HISTORY_START_ROW + MAX_CHART_ROWS}")
    next_row = HISTORY_START_ROW + len([r for r in existing if r and r[0]])

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [now] + _summary_row(result)
    ws.update([row], f"A{next_row}", value_input_option="USER_ENTERED")
    log(f"検証結果シートへ1行追記（{next_row}行目）: {row}")


def ensure_chart(sh, ws, log=print):
    """回収率(%)の推移（実行履歴が元データ）の折れ線グラフを、まだ無ければ作成する"""
    meta = sh.fetch_sheet_metadata()
    sheet_meta = next((s for s in meta["sheets"] if s["properties"]["sheetId"] == ws.id), None)
    if sheet_meta and sheet_meta.get("charts"):
        return

    chart_end_row = HISTORY_START_ROW - 1 + MAX_CHART_ROWS
    request = {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "回収率(%)の推移（実運用の実データ検証）",
                    "basicChart": {
                        "chartType": "LINE",
                        "legendPosition": "BOTTOM_LEGEND",
                        "axis": [{"position": "LEFT_AXIS", "title": "回収率(%)"}],
                        "domains": [{
                            "domain": {"sourceRange": {"sources": [{
                                "sheetId": ws.id,
                                "startRowIndex": HISTORY_HEADER_ROW - 1, "endRowIndex": chart_end_row,
                                "startColumnIndex": 0, "endColumnIndex": 1,
                            }]}}
                        }],
                        "series": [{
                            "series": {"sourceRange": {"sources": [{
                                "sheetId": ws.id,
                                "startRowIndex": HISTORY_HEADER_ROW - 1, "endRowIndex": chart_end_row,
                                "startColumnIndex": 7, "endColumnIndex": 8,
                            }]}},
                            "color": {"red": 0.85, "green": 0.2, "blue": 0.2},
                            "targetAxis": "LEFT_AXIS",
                        }],
                        "headerCount": 1,
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {"sheetId": ws.id, "rowIndex": CHART_ANCHOR_ROW, "columnIndex": CHART_ANCHOR_COL_INDEX},
                        "widthPixels": 700, "heightPixels": 380,
                    }
                },
            }
        }
    }
    sh.batch_update({"requests": [request]})
    log("検証結果シート: 回収率推移グラフを作成")


def update_verification_sheet(rows, result, log=print):
    """result_verify_job.pyから呼ぶ一括更新: 履歴追記＋累積/直近/月別の再計算＋グラフ確保。
    (spreadsheet, worksheet) を返す（呼び出し側がsh=を他の関数へ渡し回せるように）"""
    sh = get_sheet()
    ws = ensure_verification_sheet(sh, log=log)
    append_verification_result(ws, result, log=log)
    write_cumulative_summary(ws, rows, log=log)
    write_recent_summary(ws, rows, log=log)
    write_monthly_table(ws, rows, log=log)
    ensure_chart(sh, ws, log=log)
    return sh, ws


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("検証結果シート セットアップ")
    print("=" * 40)

    sheet = get_sheet()
    ensure_verification_sheet(sheet)
    print("セットアップ完了")
