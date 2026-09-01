"""
dsk_Project
Stage3: odds_adjusted_score追加の頑健性検証（EV1.20/1.50 × 全クラス/1勝以上）
Version 0.1

ai/model_a_odds_adjusted_ablation.pyでは、EV閾値1.50・オッズ上限30倍・
1勝クラス以上限定という単一の設定でのみ、モデルAへのodds_adjusted_score
追加がシャープレシオをマイナス（-0.149）からプラス（+0.136）へ転換させた
ことを確認した。この効果が、たまたまその1設定でだけ起きた偶然なのか、
設定によらず一貫してプラス方向に効く頑健な効果なのかを、
ai/stage3_stability_recheck.pyと同じ4パターン（EV閾値{1.20,1.50}×
クラスフィルタ{全クラス,1勝クラス以上}、オッズ上限30倍固定）で
モデルA（追加前）・モデルA'（追加後）を並べて再検証する。

学習はモデルA・モデルA'それぞれ1回のみ（8フォールドOOS）。EV閾値・
クラスフィルタの4通りはOOS予測に対する後段のフィルタとして
ai/backtest.py::simulate_per_foldで評価する（再学習不要）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import (
    CATEGORICAL_COLUMNS,
    compute_sharpe_ratio,
    fetch_win_payouts,
    run_model_backtest,
    simulate,
    simulate_per_fold,
)
from ai.build_dataset import FEATURE_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.walk_forward_backtest import generate_folds

ODDS_CAP = 30
EV_THRESHOLDS = [1.20, 1.50]
CLASS_FILTERS = [False, True]


def evaluate_config(df, folds, ev_threshold, class_filter):
    aggregate = simulate(df, ev_threshold, odds_cap=ODDS_CAP, class_filter=class_filter)

    fold_results = simulate_per_fold(df, folds, ev_threshold, odds_cap=ODDS_CAP, class_filter=class_filter)
    recoveries = [r["回収率(%)"] for r in fold_results]
    valid = [v for v in recoveries if v is not None and not np.isnan(v)]

    return {
        "合算買い件数": aggregate["買い件数"],
        "合算回収率(%)": aggregate["回収率(%)"],
        "平均回収率(%)": np.mean(valid) if valid else np.nan,
        "標準偏差(pt)": np.std(valid, ddof=1) if len(valid) >= 2 else np.nan,
        "最小(%)": min(valid) if valid else np.nan,
        "最大(%)": max(valid) if valid else np.nan,
        "シャープレシオ": compute_sharpe_ratio(recoveries),
    }


def main():
    print("=" * 70)
    print("dsk_Project")
    print("Stage3: odds_adjusted_score追加の頑健性検証（EV×クラスフィルタ 4パターン）")
    print("=" * 70)
    print(f"オッズ上限={ODDS_CAP}倍固定")

    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()

    print()
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    print()
    print("--- モデルA（追加前）のフォールド別学習・予測 ---")
    df_before = run_model_backtest(dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルA")

    print()
    print("--- モデルA'（odds_adjusted_score追加後）のフォールド別学習・予測 ---")
    df_after = run_model_backtest(
        dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED, CATEGORICAL_COLUMNS, win_payouts, "モデルA'"
    )

    rows = []
    detail = {}
    for model_label, df in [("モデルA（追加前）", df_before), ("モデルA'（追加後）", df_after)]:
        for ev_threshold in EV_THRESHOLDS:
            for class_filter in CLASS_FILTERS:
                result = evaluate_config(df, folds, ev_threshold, class_filter)
                key = (model_label, ev_threshold, class_filter)
                detail[key] = result
                rows.append({
                    "モデル": model_label,
                    "EV閾値": ev_threshold,
                    "クラスフィルタ": "1勝以上" if class_filter else "全クラス",
                    "合算買い件数": result["合算買い件数"],
                    "合算回収率(%)": round(result["合算回収率(%)"], 2),
                    "平均回収率(%)": round(result["平均回収率(%)"], 2),
                    "標準偏差(pt)": round(result["標準偏差(pt)"], 2),
                    "最小(%)": round(result["最小(%)"], 2),
                    "最大(%)": round(result["最大(%)"], 2),
                    "シャープレシオ": round(result["シャープレシオ"], 3),
                })

    pd.set_option("display.width", 220)
    full_df = pd.DataFrame(rows)

    print()
    print("=" * 70)
    print("=== 全設定 一覧 ===")
    print("=" * 70)
    print(full_df.to_string(index=False))

    print()
    print("=" * 70)
    print("=== モデルA（追加前） vs モデルA'（追加後）: 設定別シャープレシオ差 ===")
    print("=" * 70)
    for ev_threshold in EV_THRESHOLDS:
        for class_filter in CLASS_FILTERS:
            label = "1勝以上" if class_filter else "全クラス"
            before = detail[("モデルA（追加前）", ev_threshold, class_filter)]
            after = detail[("モデルA'（追加後）", ev_threshold, class_filter)]
            sharpe_diff = after["シャープレシオ"] - before["シャープレシオ"]
            recovery_diff = after["合算回収率(%)"] - before["合算回収率(%)"]
            sign_flip = "★プラス転換" if before["シャープレシオ"] < 0 <= after["シャープレシオ"] else ""
            print(f"\nEV>={ev_threshold} / {label}")
            print(f"  追加前: 合算={before['合算回収率(%)']:.2f}%  シャープ={before['シャープレシオ']:.3f}")
            print(f"  追加後: 合算={after['合算回収率(%)']:.2f}%  シャープ={after['シャープレシオ']:.3f}")
            print(f"  差分  : 合算{recovery_diff:+.2f}pt  シャープ{sharpe_diff:+.3f}  {sign_flip}")

    print()
    print("=" * 70)
    print("=== 判定: 4設定すべてでプラス方向に効いているか ===")
    print("=" * 70)
    n_improved = 0
    n_total = 0
    n_positive_after = 0
    for ev_threshold in EV_THRESHOLDS:
        for class_filter in CLASS_FILTERS:
            n_total += 1
            before = detail[("モデルA（追加前）", ev_threshold, class_filter)]
            after = detail[("モデルA'（追加後）", ev_threshold, class_filter)]
            if after["シャープレシオ"] > before["シャープレシオ"]:
                n_improved += 1
            if after["シャープレシオ"] > 0:
                n_positive_after += 1

    print(f"シャープレシオが改善した設定数: {n_improved} / {n_total}")
    print(f"追加後にシャープレシオがプラスになった設定数: {n_positive_after} / {n_total}")


if __name__ == "__main__":
    main()
