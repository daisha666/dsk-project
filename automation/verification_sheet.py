"""
dsk_Project
自動化: Googleスプレッドシート「検証結果」シート管理
Version 0.3

開発指示書の「シンプルな2タブ構成」の一方。実運用の予測ログ
（analysis/prediction_verification.py）を確定結果と突き合わせた成績を管理する。

PROJECT_EVの検証結果シート（②累積成績サマリー・③グラフ・④月別/年別成績）の
構成を参考にしつつ、dsk_Projectは単勝オンリーのシンプルな設計のため、券種別の
内訳・信頼度別/波乱度別/危険馬判定の答え合わせ等（PROJECT_EV固有の機能）は無く、
以下のブロックで構成する:
  - Stage3確定基準・買い目推奨ランク（S/A/B）の定義表示
  - ①回収率(%)の推移グラフ（S/A/Bランク別の3本の折れ線。下記実行履歴が元データ）
  - ②累積成績サマリー: 結果確定済みの全予測ログを対象に、ランク（S/A/B）別に
    的中率・回収率を分けて集計（ai/backtest.py::classify_recommendation_rank参照。
    S=現行確定基準、A/Bはユーザー確定事項によりグリッドサーチの数値をそのまま
    使わずキリの良い値で機械的に区切った参考範囲）
  - 今回（直近）の成績: 直近の検証ジョブで新たに結果が確定した開催日分だけの
    成績を、同じくランク別に集計（PROJECT_EVの「直近（最新処理分）」に相当。
    全rowsの中でrace_dateが最新のものを「直近の1回分」とみなす簡易な判定）
  - ④月別成績: race_dateの年月×ランクごとの的中率・回収率
  - ③実行履歴: 実行のたびに1行追記する時系列データ（S/A/Bランクそれぞれの
    買い目推奨数・的中数・回収率を持つ。上記グラフの元データ）
"""

import sys
from datetime import datetime
from pathlib import Path

import gspread

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from automation.sheet_config import SPREADSHEET_ID, VERIFICATION_SHEET_NAME, require_spreadsheet_id
from analysis.prediction_verification import RANKS, filter_most_recent_date, group_by_month, summarize
from prediction.predict_race import CLASS_FILTER
from prediction.sheets_report import get_client

SETTINGS_BLOCK = [
    ["Stage3確定基準・買い目推奨ランク（README「Stage3としての基準値」参照）"],
    ["モデル", "モデルA'（overall_score + odds_adjusted_score + 市場情報）"],
    ["ランクS（現行確定基準）", "EV>=1.4・オッズ上限30倍"],
    ["ランクA（やや広い参考範囲）", "EV>=1.2・オッズ上限35倍"],
    ["ランクB（さらに緩い参考範囲）", "EV>=1.0（単勝の理論上の損益分岐点）・オッズ上限35倍"],
    ["クラスフィルタ（S/A/B共通）", "1勝クラス以上限定" if CLASS_FILTER else "全クラス"],
    [""],
]

RANK_COLORS = {
    "S": {"red": 0.30, "green": 0.75, "blue": 0.50},  # docs/style.cssの--goodに合わせる
    "A": {"red": 0.35, "green": 0.66, "blue": 1.00},  # --accent
    "B": {"red": 0.58, "green": 0.63, "blue": 0.72},  # --text-dim
}

HEADER_FORMAT = {"textFormat": {"bold": True}}
TITLE_FORMAT = {"textFormat": {"bold": True, "fontSize": 12}}
PERCENT_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": '0.0"%"'}}
YEN_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": '#,##0"円"'}}

SUMMARY_HEADER = ["ランク", "対象予測数(結果確定済み)", "買い目推奨数", "的中数", "的中率(%)",
                   "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]
RANKS_WITH_TOTAL = ["合計"] + RANKS  # 合計行（S+A+B、rank=Noneの集計）を先頭に置く

# ---- グラフ（回収率推移。ランク別3本の折れ線）。行方向は「下の表の下」
#      （表が育つと追いつかれる懸念）を避けるため、常に一定サイズの本グラフを
#      先頭（Stage3確定基準のすぐ下）に固定し、増減する各表はその下に置く。
#      列方向はA列（累積成績サマリーの真上）。以前、行固定(freeze)がグラフの
#      領域まで掛かっていた際にフローティングオブジェクトの描画位置が固定行
#      境界にずれる事象があったため、freezeは設定ブロックのみにとどめている ----
CHART_TITLE_ROW = len(SETTINGS_BLOCK) + 1
CHART_ANCHOR_ROW = CHART_TITLE_ROW  # 0-indexedのrowIndexとしてそのまま使う（1行分のズレでちょうど良い）
CHART_ANCHOR_COL_INDEX = 0  # A列
CHART_ROW_SPAN = 20  # 380pxのグラフ高さ（既定の行高21px換算で約18行）を収める余裕

# ---- 累積成績サマリー（ランク別、S/A/Bの3行） ----
CUMULATIVE_TITLE_ROW = CHART_TITLE_ROW + CHART_ROW_SPAN + 2
CUMULATIVE_HEADER_ROW = CUMULATIVE_TITLE_ROW + 1
CUMULATIVE_DATA_START_ROW = CUMULATIVE_HEADER_ROW + 1

# ---- 今回（直近）の成績（ランク別、S/A/Bの3行） ----
RECENT_TITLE_ROW = CUMULATIVE_DATA_START_ROW + len(RANKS) + 1
RECENT_HEADER_ROW = RECENT_TITLE_ROW + 1
RECENT_DATA_START_ROW = RECENT_HEADER_ROW + 1

# ---- 月別成績（年月×ランク） ----
MONTHLY_TITLE_ROW = RECENT_DATA_START_ROW + len(RANKS) + 1
MONTHLY_HEADER_ROW = MONTHLY_TITLE_ROW + 1
MONTHLY_DATA_START_ROW = MONTHLY_HEADER_ROW + 1
MONTHLY_HEADER = ["年月"] + SUMMARY_HEADER
MONTHLY_MAX_MONTHS = 20  # 1か月あたりS/A/Bの3行使うため、3年半分弱の余裕（運用しながら足りなくなれば拡張する）
MONTHLY_MAX_ROWS = MONTHLY_MAX_MONTHS * len(RANKS)

# ---- 実行履歴（グラフ用データ。ランクごとに買い目推奨数・的中数・回収率(%)を持つ） ----
HISTORY_TITLE_ROW = MONTHLY_DATA_START_ROW + MONTHLY_MAX_ROWS + 1
HISTORY_HEADER_ROW = HISTORY_TITLE_ROW + 1
HISTORY_START_ROW = HISTORY_HEADER_ROW + 1
HISTORY_HEADER = ["更新日時"]
for _rank in RANKS:
    HISTORY_HEADER += [f"{_rank}_買い目推奨数", f"{_rank}_的中数", f"{_rank}_回収率(%)"]
# 各ランクの回収率(%)列の0-indexed列番号（グラフのseries用）
HISTORY_RECOVERY_COL_INDEX = {rank: 1 + i * 3 + 2 for i, rank in enumerate(RANKS)}
MAX_CHART_ROWS = 2000


def get_sheet():
    require_spreadsheet_id()
    gc = get_client()
    return gc.open_by_key(SPREADSHEET_ID)


def _summary_row(rank_label, result):
    return [
        rank_label,
        result["対象予測数（結果確定済み）"],
        result["買い目推奨数"],
        result["的中数"],
        round(result["的中率(%)"], 2) if result["買い目推奨数"] else "",
        result["総購入額(円)"],
        round(result["総払戻額(円)"], 0),
        round(result["回収率(%)"], 2) if result["買い目推奨数"] else "",
        result["払戻欠損"],
    ]


def _rank_summary_rows(rows):
    """S/A/Bそれぞれのsummarize()結果を [(rank, result), ...] で返す
    （合計行は含まない。累積/直近/月別の各データ行に使う）"""
    return [(rank, summarize(rows, rank=rank)) for rank in RANKS]


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

    ws.update([["■ 回収率(%)の推移グラフ（ランク別）"]], f"A{CHART_TITLE_ROW}")
    ws.format(f"A{CHART_TITLE_ROW}", TITLE_FORMAT)

    ws.update([["■ 累積成績サマリー（結果確定済みの全予測ログが対象。ランク別）"]], f"A{CUMULATIVE_TITLE_ROW}")
    ws.format(f"A{CUMULATIVE_TITLE_ROW}", TITLE_FORMAT)
    ws.update([SUMMARY_HEADER], f"A{CUMULATIVE_HEADER_ROW}", value_input_option="USER_ENTERED")
    ws.format(f"A{CUMULATIVE_HEADER_ROW}:I{CUMULATIVE_HEADER_ROW}", HEADER_FORMAT)

    ws.update([["■ 今回（直近）の成績（直近に結果が確定した開催日分のみ。ランク別）"]], f"A{RECENT_TITLE_ROW}")
    ws.format(f"A{RECENT_TITLE_ROW}", TITLE_FORMAT)
    ws.update([SUMMARY_HEADER], f"A{RECENT_HEADER_ROW}", value_input_option="USER_ENTERED")
    ws.format(f"A{RECENT_HEADER_ROW}:I{RECENT_HEADER_ROW}", HEADER_FORMAT)

    ws.update([["■ 月別成績（ランク別）"]], f"A{MONTHLY_TITLE_ROW}")
    ws.format(f"A{MONTHLY_TITLE_ROW}", TITLE_FORMAT)
    ws.update([MONTHLY_HEADER], f"A{MONTHLY_HEADER_ROW}", value_input_option="USER_ENTERED")
    ws.format(f"A{MONTHLY_HEADER_ROW}:J{MONTHLY_HEADER_ROW}", HEADER_FORMAT)

    ws.update([["■ 実行履歴（上記グラフの元データ。実行のたびに1行追記）"]], f"A{HISTORY_TITLE_ROW}")
    ws.format(f"A{HISTORY_TITLE_ROW}", TITLE_FORMAT)
    ws.update([HISTORY_HEADER], f"A{HISTORY_HEADER_ROW}", value_input_option="USER_ENTERED")
    last_col = gspread.utils.rowcol_to_a1(1, len(HISTORY_HEADER)).rstrip("1")
    ws.format(f"A{HISTORY_HEADER_ROW}:{last_col}{HISTORY_HEADER_ROW}", HEADER_FORMAT)

    # 的中率・回収率(%)・金額列に表示用の数値書式を設定
    cumulative_end = CUMULATIVE_DATA_START_ROW + len(RANKS) - 1
    ws.format(f"E{CUMULATIVE_DATA_START_ROW}:E{cumulative_end}", PERCENT_FORMAT)
    ws.format(f"H{CUMULATIVE_DATA_START_ROW}:H{cumulative_end}", PERCENT_FORMAT)
    ws.format(f"F{CUMULATIVE_DATA_START_ROW}:G{cumulative_end}", YEN_FORMAT)
    recent_end = RECENT_DATA_START_ROW + len(RANKS) - 1
    ws.format(f"E{RECENT_DATA_START_ROW}:E{recent_end}", PERCENT_FORMAT)
    ws.format(f"H{RECENT_DATA_START_ROW}:H{recent_end}", PERCENT_FORMAT)
    ws.format(f"F{RECENT_DATA_START_ROW}:G{recent_end}", YEN_FORMAT)
    monthly_end = MONTHLY_DATA_START_ROW + MONTHLY_MAX_ROWS - 1
    ws.format(f"F{MONTHLY_DATA_START_ROW}:F{monthly_end}", PERCENT_FORMAT)
    ws.format(f"I{MONTHLY_DATA_START_ROW}:I{monthly_end}", PERCENT_FORMAT)
    ws.format(f"G{MONTHLY_DATA_START_ROW}:H{monthly_end}", YEN_FORMAT)
    history_end = HISTORY_START_ROW + MAX_CHART_ROWS - 1
    for rank in RANKS:
        col_letter = gspread.utils.rowcol_to_a1(1, HISTORY_RECOVERY_COL_INDEX[rank] + 1).rstrip("1")
        ws.format(f"{col_letter}{HISTORY_START_ROW}:{col_letter}{history_end}", PERCENT_FORMAT)


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
    table = [_summary_row(rank, result) for rank, result in _rank_summary_rows(rows)]
    ws.update(table, f"A{CUMULATIVE_DATA_START_ROW}", value_input_option="USER_ENTERED")
    parts = [f"{rank}:{result['買い目推奨数']}件" for rank, result in _rank_summary_rows(rows)]
    log(f"累積成績サマリー更新（ランク別）: {' '.join(parts)}")


def write_recent_summary(ws, rows, log=print):
    recent_rows = filter_most_recent_date(rows)
    table = [_summary_row(rank, result) for rank, result in _rank_summary_rows(recent_rows)]
    ws.update(table, f"A{RECENT_DATA_START_ROW}", value_input_option="USER_ENTERED")
    recent_date = recent_rows[0][3] if recent_rows else "-"
    log(f"直近の成績更新（{recent_date}、ランク別）")


def write_monthly_table(ws, rows, log=print):
    monthly = group_by_month(rows)
    if len(monthly) > MONTHLY_MAX_MONTHS:
        log(f"月別成績: {len(monthly)}か月分だがシート確保分{MONTHLY_MAX_MONTHS}か月を超えるため直近{MONTHLY_MAX_MONTHS}か月のみ表示")
        monthly = monthly[-MONTHLY_MAX_MONTHS:]

    table = []
    for year_month, month_rows in monthly:
        for rank, result in _rank_summary_rows(month_rows):
            table.append([year_month] + _summary_row(rank, result))
    if table:
        ws.update(table, f"A{MONTHLY_DATA_START_ROW}", value_input_option="USER_ENTERED")
    log(f"月別成績更新: {len(monthly)}か月分×{len(RANKS)}ランク")


def append_verification_result(ws, rows, log=print):
    """実行履歴に1行追記する（S/A/Bそれぞれの買い目推奨数・的中数・回収率）。
    ws.append_row()は使わず、月別成績の予約領域（空セルの範囲）を誤って
    「最終行」と誤認しないよう、既存の履歴行数を数えて明示的な行番号へ書き込む"""
    existing = ws.get(f"A{HISTORY_START_ROW}:A{HISTORY_START_ROW + MAX_CHART_ROWS}")
    next_row = HISTORY_START_ROW + len([r for r in existing if r and r[0]])

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [now]
    for rank, result in _rank_summary_rows(rows):
        row += [
            result["買い目推奨数"],
            result["的中数"],
            round(result["回収率(%)"], 2) if result["買い目推奨数"] else "",
        ]
    ws.update([row], f"A{next_row}", value_input_option="USER_ENTERED")
    log(f"検証結果シートへ1行追記（{next_row}行目）: {row}")


def ensure_chart(sh, ws, log=print):
    """回収率(%)の推移（実行履歴が元データ）の折れ線グラフを、S/A/Bランク別の
    3本の系列で、まだ無ければ作成する。

    Googleスプレッドシートは、元データが1件も無い状態でグラフを作成すると
    seriesの色設定（colorStyle）等を保存せずに簡略化してしまう挙動がある
    （実際に確認済み）。ensure_chart()は本来「実行履歴に1行追記した直後」に
    呼ばれる設計だが、rebuild_layout()直後にensure_chart()だけを単独で
    呼んだ場合（動作確認・シートリセット時等）は元データが無い状態で
    グラフが作られてしまう。一度色無しで作られたグラフは「既にグラフが
    ある」判定でスキップされ続け、後から実データが増えても自然には直らない
    ため、既存グラフのseriesが空（＝壊れた状態）なら削除して作り直す"""
    meta = sh.fetch_sheet_metadata()
    sheet_meta = next((s for s in meta["sheets"] if s["properties"]["sheetId"] == ws.id), None)
    existing_charts = sheet_meta.get("charts", []) if sheet_meta else []
    if existing_charts:
        series = existing_charts[0].get("spec", {}).get("basicChart", {}).get("series", [])
        if series:
            return
        log("既存の回収率推移グラフのseriesが空（データ無しで作成された状態）のため作り直す")
        sh.batch_update({"requests": [
            {"deleteEmbeddedObject": {"objectId": c["chartId"]}} for c in existing_charts
        ]})

    chart_end_row = HISTORY_START_ROW - 1 + MAX_CHART_ROWS
    series = []
    for rank in RANKS:
        col = HISTORY_RECOVERY_COL_INDEX[rank]
        series.append({
            "series": {"sourceRange": {"sources": [{
                "sheetId": ws.id,
                "startRowIndex": HISTORY_HEADER_ROW - 1, "endRowIndex": chart_end_row,
                "startColumnIndex": col, "endColumnIndex": col + 1,
            }]}},
            "color": RANK_COLORS[rank],
            "targetAxis": "LEFT_AXIS",
        })

    request = {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "回収率(%)の推移（実運用の実データ検証・ランク別）",
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
                        "series": series,
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
    log("検証結果シート: 回収率推移グラフ（ランク別3系列）を作成")


def update_verification_sheet(rows, result, log=print):
    """result_verify_job.pyから呼ぶ一括更新: 履歴追記＋累積/直近/月別の再計算＋グラフ確保。
    result引数はresult_verify_job.py側の従来インターフェースとの互換のため残しているが
    ここでは使わない（ランク別集計はすべてrowsから計算し直す）。
    (spreadsheet, worksheet) を返す（呼び出し側がsh=を他の関数へ渡し回せるように）"""
    sh = get_sheet()
    ws = ensure_verification_sheet(sh, log=log)
    append_verification_result(ws, rows, log=log)
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
