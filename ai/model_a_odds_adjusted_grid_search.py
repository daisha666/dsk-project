"""
dsk_Project
Stage3: モデルA'（odds_adjusted_score追加）× 1勝クラス以上限定のEV閾値・
オッズ上限グリッドサーチ
Version 0.1

これまでの検証はEV閾値{1.20,1.50}×オッズ上限30倍固定でしか見ていなかった。
本スクリプトはモデルA'（odds_adjusted_score追加）＋1勝クラス以上限定を
固定し、EV閾値を0.1刻み（1.10〜1.70）、オッズ上限を{20,25,30,35}で
振った7×4=28通りの組み合わせについて、シャープレシオ・合算回収率・
買い件数を算出する。

小サンプルへの注意:
  依頼時の注意喚起の通り、EV閾値やオッズ上限を絞るほど買い件数は減り、
  数件の的中・不的中で回収率・シャープレシオが大きく動く。本スクリプトは
  各設定の「合算買い件数」に加え、8フォールド中実際に買いが発生した
  「有効フォールド数」も出力し、買い件数が少ない設定（目安: 合算300件未満、
  またはフォールド8件中5件未満でしか買いが発生していない設定）には
  「要注意」フラグを付ける。シャープレシオが最大の設定を機械的に「最良」と
  結論づけず、必ずこのフラグと買い件数を見て判断すること。

学習はモデルA'について8フォールドOOSで1回のみ。EV閾値・オッズ上限の
28通りはOOS予測に対する後段のフィルタ（ai/backtest.py::simulate_per_fold）
のため再学習不要。
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
from ai.build_dataset import FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.walk_forward_backtest import generate_folds

EV_THRESHOLDS = [1.10, 1.20, 1.30, 1.40, 1.50, 1.60, 1.70]
ODDS_CAPS = [20, 25, 30, 35]
CLASS_FILTER = True

# この件数・有効フォールド数を下回ったら「要注意」フラグを付ける
MIN_BUYS_FOR_CONFIDENCE = 300
MIN_ACTIVE_FOLDS_FOR_CONFIDENCE = 5


def evaluate_config(df, folds, ev_threshold, odds_cap):
    aggregate = simulate(df, ev_threshold, odds_cap=odds_cap, class_filter=CLASS_FILTER)

    fold_results = simulate_per_fold(df, folds, ev_threshold, odds_cap=odds_cap, class_filter=CLASS_FILTER)
    recoveries = [r["回収率(%)"] for r in fold_results]
    valid = [v for v in recoveries if v is not None and not np.isnan(v)]
    active_folds = sum(1 for r in fold_results if r["買い件数"] > 0)

    low_confidence = (
        aggregate["買い件数"] < MIN_BUYS_FOR_CONFIDENCE
        or active_folds < MIN_ACTIVE_FOLDS_FOR_CONFIDENCE
    )

    return {
        "EV閾値": ev_threshold,
        "オッズ上限": odds_cap,
        "合算買い件数": aggregate["買い件数"],
        "有効フォールド数": active_folds,
        "合算的中率(%)": aggregate["的中率(%)"],
        "合算回収率(%)": aggregate["回収率(%)"],
        "平均回収率(%)": np.mean(valid) if valid else np.nan,
        "標準偏差(pt)": np.std(valid, ddof=1) if len(valid) >= 2 else np.nan,
        "シャープレシオ": compute_sharpe_ratio(recoveries),
        "要注意": "★" if low_confidence else "",
    }


def main():
    print("=" * 70)
    print("dsk_Project")
    print("Stage3: モデルA'（odds_adjusted_score追加）× 1勝以上限定のグリッドサーチ")
    print("=" * 70)
    print(f"EV閾値: {EV_THRESHOLDS}")
    print(f"オッズ上限: {ODDS_CAPS}")
    print(f"要注意フラグ基準: 合算買い件数<{MIN_BUYS_FOR_CONFIDENCE} または 有効フォールド数<{MIN_ACTIVE_FOLDS_FOR_CONFIDENCE}")

    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()

    print()
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    print()
    print("--- モデルA'（odds_adjusted_score追加）のフォールド別学習・予測 ---")
    df = run_model_backtest(
        dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED, CATEGORICAL_COLUMNS, win_payouts, "モデルA'"
    )

    rows = []
    for ev_threshold in EV_THRESHOLDS:
        for odds_cap in ODDS_CAPS:
            rows.append(evaluate_config(df, folds, ev_threshold, odds_cap))

    grid_df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)

    print()
    print("=" * 70)
    print("=== 全28通り 一覧 ===")
    print("=" * 70)
    print(grid_df.to_string(index=False))

    print()
    print("=" * 70)
    print("=== オッズ上限30倍固定でのEV閾値スイープ（シャープレシオ順） ===")
    print("=" * 70)
    cap30 = grid_df[grid_df["オッズ上限"] == 30].sort_values("シャープレシオ", ascending=False)
    print(cap30.to_string(index=False))

    best_row = cap30.iloc[0]
    print()
    print(f"オッズ上限30倍固定での最良EV閾値: {best_row['EV閾値']} "
          f"（シャープ={best_row['シャープレシオ']:.3f}, 買い件数={best_row['合算買い件数']}, "
          f"要注意={'あり' if best_row['要注意'] else 'なし'}）")

    print()
    print("=" * 70)
    print("=== EV閾値1.50固定でのオッズ上限スイープ（大穴バイアス確認） ===")
    print("=" * 70)
    ev150 = grid_df[grid_df["EV閾値"] == 1.50].sort_values("オッズ上限")
    print(ev150.to_string(index=False))

    print()
    print("=" * 70)
    print("=== 全28通り中、シャープレシオ上位5件（要注意フラグ込みで確認） ===")
    print("=" * 70)
    top5 = grid_df.sort_values("シャープレシオ", ascending=False).head(5)
    print(top5.to_string(index=False))

    print()
    print("=" * 70)
    print("=== 参考: 要注意フラグなし（合算件数・有効フォールド数とも十分）の中での最良設定 ===")
    print("=" * 70)
    confident = grid_df[grid_df["要注意"] == ""].sort_values("シャープレシオ", ascending=False)
    if len(confident) > 0:
        print(confident.head(5).to_string(index=False))
    else:
        print("要注意フラグなしの設定が存在しない（全設定が小サンプル）")


if __name__ == "__main__":
    main()
