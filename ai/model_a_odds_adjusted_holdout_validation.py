"""
dsk_Project
Stage3: EV閾値・オッズ上限グリッドサーチのホールドアウト検証
Version 0.1

ai/model_a_odds_adjusted_grid_search.pyは28通りのEV閾値×オッズ上限を
「全8フォールドのシャープレシオ」で比較し、最良設定（EV1.30・上限25倍、
シャープ0.443）を選んだ。しかし、この選定自体が全期間のデータを見て
行われているため、「本当に優れた設定」なのか「28通り試した中でたまたま
良く見えただけ」（多重比較による見せかけの結果）なのかを区別できない。

本スクリプトは、直近2フォールド（2025-12-27〜2026-04-25、
2026-04-26〜2026-08-30）を完全に切り離し、以下の手順で検証する:

  1. チューニング用データ（最初の6フォールド）だけを使ってグリッドサーチを
     行い、最良のEV閾値・オッズ上限を選ぶ（ホールドアウト期間は一切見ない）
  2. その1設定だけを、切り離しておいたホールドアウト期間（直近2フォールド）
     で初めて評価する
  3. チューニング期間での成績とホールドアウト期間での成績を比較する。
     大きく崩れなければ多重比較による見せかけではない可能性が高く、
     大きく崩れれば見せかけだった可能性が高い

モデルA'自体の学習は8フォールド全てで行う（各フォールドの学習データは
そのフォールドのテスト期間より前の全データという、通常のウォークフォワード
設計のまま。個々のフォールドの予測はリーケージが無い）。ホールドアウトは
あくまで「グリッドサーチという選定プロセス」に対して行うものであり、
モデル学習に対してではない（モデル自体は既に全フォールドでOOSになっている）。
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
N_HOLDOUT_FOLDS = 2


def restrict_to_range(df, start, end):
    return df[(df["race_date"] >= start) & (df["race_date"] <= end)]


def evaluate_on_folds(df, folds, ev_threshold, odds_cap):
    """指定フォールド群だけを対象に、フォールド別シャープレシオと
    プール（合算）回収率を計算する"""
    start, end = folds[0][0], folds[-1][1]
    pooled = restrict_to_range(df, start, end)
    aggregate = simulate(pooled, ev_threshold, odds_cap=odds_cap, class_filter=CLASS_FILTER)

    fold_results = simulate_per_fold(df, folds, ev_threshold, odds_cap=odds_cap, class_filter=CLASS_FILTER)
    recoveries = [r["回収率(%)"] for r in fold_results]
    valid = [v for v in recoveries if v is not None and not np.isnan(v)]

    return {
        "買い件数": aggregate["買い件数"],
        "合算回収率(%)": aggregate["回収率(%)"],
        "平均回収率(%)": np.mean(valid) if valid else np.nan,
        "標準偏差(pt)": np.std(valid, ddof=1) if len(valid) >= 2 else np.nan,
        "シャープレシオ": compute_sharpe_ratio(recoveries),
        "fold_results": fold_results,
    }


def main():
    print("=" * 70)
    print("dsk_Project")
    print("Stage3: グリッドサーチのホールドアウト検証")
    print("=" * 70)

    dataset = build_dataset()
    all_folds = generate_folds(dataset)
    tuning_folds = all_folds[:-N_HOLDOUT_FOLDS]
    holdout_folds = all_folds[-N_HOLDOUT_FOLDS:]

    print()
    print(f"全フォールド数: {len(all_folds)}")
    print(f"チューニング用フォールド（グリッドサーチに使う）: {len(tuning_folds)}件")
    for s, e in tuning_folds:
        print(f"  {s} 〜 {e}")
    print(f"ホールドアウトフォールド（グリッドサーチでは一切使わない）: {len(holdout_folds)}件")
    for s, e in holdout_folds:
        print(f"  {s} 〜 {e}")

    win_payouts = fetch_win_payouts()

    print()
    print("--- モデルA'（odds_adjusted_score追加）のフォールド別学習・予測（全8フォールド） ---")
    df = run_model_backtest(
        dataset, all_folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED, CATEGORICAL_COLUMNS, win_payouts, "モデルA'"
    )

    print()
    print("=" * 70)
    print("=== ステップ1: チューニング用6フォールドだけでグリッドサーチ ===")
    print("=" * 70)

    rows = []
    for ev_threshold in EV_THRESHOLDS:
        for odds_cap in ODDS_CAPS:
            result = evaluate_on_folds(df, tuning_folds, ev_threshold, odds_cap)
            rows.append({
                "EV閾値": ev_threshold,
                "オッズ上限": odds_cap,
                "買い件数": result["買い件数"],
                "合算回収率(%)": round(result["合算回収率(%)"], 2),
                "標準偏差(pt)": round(result["標準偏差(pt)"], 2),
                "シャープレシオ": round(result["シャープレシオ"], 3),
            })

    grid_df = pd.DataFrame(rows).sort_values("シャープレシオ", ascending=False)
    pd.set_option("display.width", 200)

    print()
    print("チューニング期間（6フォールド）でのシャープレシオ上位10件:")
    print(grid_df.head(10).to_string(index=False))

    best = grid_df.iloc[0]
    best_ev = best["EV閾値"]
    best_cap = int(best["オッズ上限"])

    print()
    print(f"チューニング期間での最良設定: EV>={best_ev}・オッズ上限{best_cap}倍 "
          f"（シャープ={best['シャープレシオ']:.3f}, 買い件数={best['買い件数']}）")

    print()
    print("=" * 70)
    print(f"=== ステップ2: 最良設定（EV>={best_ev}・上限{best_cap}倍）をホールドアウト期間で初めて評価 ===")
    print("=" * 70)

    tuning_result = evaluate_on_folds(df, tuning_folds, best_ev, best_cap)
    holdout_result = evaluate_on_folds(df, holdout_folds, best_ev, best_cap)

    print()
    print("チューニング期間（6フォールド、選定に使用済み）:")
    for r in tuning_result["fold_results"]:
        print(f"  {r['テスト期間']}: 買い件数={r['買い件数']} 的中数={r['的中数']} 回収率={r['回収率(%)']:.2f}%")
    print(f"  → 合算回収率={tuning_result['合算回収率(%)']:.2f}%  "
          f"標準偏差={tuning_result['標準偏差(pt)']:.2f}pt  シャープ={tuning_result['シャープレシオ']:.3f}")

    print()
    print("ホールドアウト期間（直近2フォールド、初めて見るデータ）:")
    for r in holdout_result["fold_results"]:
        print(f"  {r['テスト期間']}: 買い件数={r['買い件数']} 的中数={r['的中数']} 回収率={r['回収率(%)']:.2f}%")
    print(f"  → 合算回収率={holdout_result['合算回収率(%)']:.2f}%  "
          f"標準偏差={holdout_result['標準偏差(pt)']:.2f}pt  シャープ={holdout_result['シャープレシオ']:.3f}")

    print()
    print("=" * 70)
    print("=== 判定 ===")
    print("=" * 70)
    print(f"{'':30s}{'買い件数':>10s}{'合算回収率(%)':>16s}{'シャープレシオ':>16s}")
    print(f"{'チューニング期間(6F)':30s}{tuning_result['買い件数']:10d}"
          f"{tuning_result['合算回収率(%)']:16.2f}{tuning_result['シャープレシオ']:16.3f}")
    print(f"{'ホールドアウト期間(2F)':30s}{holdout_result['買い件数']:10d}"
          f"{holdout_result['合算回収率(%)']:16.2f}{holdout_result['シャープレシオ']:16.3f}")

    recovery_gap = holdout_result["合算回収率(%)"] - tuning_result["合算回収率(%)"]
    print()
    print(f"合算回収率の差（ホールドアウト－チューニング） = {recovery_gap:+.2f}pt")
    print()
    print("参考: 全28通りのグリッドサーチ（ai/model_a_odds_adjusted_grid_search.py、全8フォールド使用）")
    print("では、EV>=1.30・上限25倍のシャープレシオ=0.443（買い件数1,521件）だった。")
    print("上記のチューニング期間（6フォールドのみ）での値と比較すること。")


if __name__ == "__main__":
    main()
