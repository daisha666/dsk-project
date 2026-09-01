"""
dsk_Project
自動化: 「AI自己分析」シート
Version 0.1

PROJECT_EVのautomation/ai_self_report.pyと同じ位置づけ（結果取得→検証実行
ジョブの完了時に、検証結果から機械的に導ける傾向をまとめて履歴として
積み上げる）だが、dsk_Projectは単勝1券種・1モデルのみのシンプルな構成
なので、券種別集計・波乱度/信頼度分布は持たない。代わりに「直近N件」対
「全期間累計」の的中率・回収率を比較する、より単純な傾向分析にしている。

「見つかった問題・気づき」欄のうち、計算ロジックの不具合等の発見は自動検出
できないため、そうした発見があった回はappend_self_analysis()のissue_notes
引数に手動で渡す。渡さなければ機械的な傾向分析の文面のみになる。
"""

import sys
from datetime import datetime
from pathlib import Path

import gspread

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from analysis.prediction_verification import summarize
from automation.sheet_control_panel import get_sheet

REPORT_SHEET_NAME = "AI自己分析"
REPORT_HEADER = ["実行日時", "累計検証件数", "見つかった問題・気づき", "次に生かされること"]

RECENT_N = 20
TREND_THRESHOLD_PT = 10  # 直近と累計の差がこのpt以上なら「気づき」として拾う


def get_or_create_report_sheet(sh, log=print):
    try:
        return sh.worksheet(REPORT_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=REPORT_SHEET_NAME, rows=500, cols=len(REPORT_HEADER))
        ws.update([REPORT_HEADER], "A1", value_input_option="USER_ENTERED")
        ws.format("A1:D1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)
        log(f"「{REPORT_SHEET_NAME}」シートを新規作成")
        return ws


def compute_trend_insights(rows, recent_n=RECENT_N):
    """全期間累計 と 直近recent_n件 の的中率・回収率を比較し、
    (傾向テキスト, 気づきのリスト) を返す"""
    cumulative = summarize(rows)
    recent_rows = rows[-recent_n:]
    recent = summarize(recent_rows)

    lines = [
        f"累計: 買い目推奨{cumulative['買い目推奨数']}件 "
        f"的中率{cumulative['的中率(%)']:.1f}% 回収率{cumulative['回収率(%)']:.1f}%",
        f"直近{len(recent_rows)}件: 買い目推奨{recent['買い目推奨数']}件 "
        f"的中率{recent['的中率(%)']:.1f}% 回収率{recent['回収率(%)']:.1f}%",
    ]

    notable = []
    if recent["買い目推奨数"] > 0 and cumulative["買い目推奨数"] > recent["買い目推奨数"]:
        recovery_diff = recent["回収率(%)"] - cumulative["回収率(%)"]
        if abs(recovery_diff) >= TREND_THRESHOLD_PT:
            direction = "高め" if recovery_diff > 0 else "低め"
            notable.append(
                f"直近{len(recent_rows)}件の回収率が{recent['回収率(%)']:.1f}%と、"
                f"累計（{cumulative['回収率(%)']:.1f}%）より{direction}でした"
            )

    trend_text = "\n".join(lines)
    return trend_text, notable


def append_self_analysis(rows, sh=None, log=print, issue_notes=None, followup_notes=None):
    """結果確定済みの予測ログ全件（analysis/prediction_verification.py::
    fetch_resolved_predictionsの戻り値）を渡すと、傾向分析レポートを1件
    「AI自己分析」シートへ追記する"""
    if not rows:
        log("AI自己分析: 検証対象の予測ログが無いためレポート生成をスキップ")
        return None

    if sh is None:
        sh = get_sheet()

    trend_text, notable = compute_trend_insights(rows)

    if issue_notes:
        issue_text = f"{issue_notes.strip()}\n\n[傾向分析]\n{trend_text}"
    else:
        issue_text = f"（機械的な傾向分析のみ。ロジック上の不具合は見つかっていません）\n{trend_text}"

    if followup_notes:
        followup_text = followup_notes.strip()
    elif notable:
        followup_text = (
            "、".join(notable)
            + "。件数が少ないうちは偶然の振れの可能性もあるため、引き続きデータを蓄積し、"
              "同じ傾向が続くようであれば閾値・ロジックの見直しを検討します。"
        )
    else:
        followup_text = "直近と累計で大きな乖離は見られませんでした。引き続きデータを蓄積し、監視を続けます。"

    ws_report = get_or_create_report_sheet(sh, log=log)
    row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(rows), issue_text, followup_text]
    ws_report.append_row(row, value_input_option="USER_ENTERED")
    log(f"AI自己分析: レポートを1件追記しました（累計{len(rows)}件）")
    return row


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("ai_self_report 読み込み成功")
    print("=" * 40)
