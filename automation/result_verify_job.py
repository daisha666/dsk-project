"""
dsk_Project
自動化ジョブ: 結果取得→検証実行
Version 0.1

collectors/yahoo_result_collector.pyで直近数日分の結果・払戻金を取得し、
analysis/prediction_verification.pyで実運用の予測ログを確定結果と突き合わせる。
"""

import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.yahoo_result_collector import YahooResultCollector

# 直近何日分を毎回取得し直すか（取消・確定遅れ等を拾うための保守的な余裕）
LOOKBACK_DAYS = 7


def run_result_verify_job(log=print):
    log("結果取得を開始")
    collector = YahooResultCollector()
    stats = collector.collect(
        start_date=date.today() - timedelta(days=LOOKBACK_DAYS),
        end_date=date.today() - timedelta(days=1),
        log=log,
    )
    log(f"結果取得完了: レース={stats['races']} エントリ={stats['entries']}")

    log("血統backfillを実行")
    race_ids = collector.fetch_race_ids_missing_pedigree()
    if race_ids:
        pedigree_stats = collector.backfill_pedigrees(race_ids, log=log)
        log(f"血統backfill完了: {pedigree_stats}")

    log("実運用予測ログの検証を実行")
    from analysis.prediction_verification import fetch_resolved_predictions, summarize

    rows = fetch_resolved_predictions()
    if not rows:
        return f"結果{stats['races']}レース保存。結果確定済みの予測ログはまだ無し"

    result = summarize(rows)

    try:
        from automation.verification_sheet import append_verification_result, ensure_verification_sheet, get_sheet
        sh = get_sheet()
        ws = ensure_verification_sheet(sh, log=log)
        append_verification_result(ws, result, log=log)
    except Exception as exc:
        log(f"検証結果シートへの書き込みに失敗（DB上の検証結果自体は算出済み）: {exc}")
        sh = None

    try:
        from automation.ai_self_report import append_self_analysis
        append_self_analysis(rows, sh=sh, log=log)
    except Exception as exc:
        log(f"AI自己分析シートへの書き込みに失敗（本体の検証結果には影響なし）: {exc}")

    return (
        f"結果{stats['races']}レース保存。実運用検証: "
        f"買い目推奨{result['買い目推奨数']}件 的中{result['的中数']}件 "
        f"回収率{result['回収率(%)']:.1f}%"
    )


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("result_verify_job 実行")
    print("=" * 40)
    print(run_result_verify_job())
