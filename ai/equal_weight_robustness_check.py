"""
dsk_Project
均等配分(condition_weights無効化)の頑健性チェック
Version 0.1

ai/condition_weight_equal_ablation.pyはSランク基準（EV>=1.4・オッズ上限30倍・
1勝クラス以上限定）1点だけで比較していたが、1つの設定だけで良く見えても
他の設定では逆効果ということが過去にあったため、複数の購入判定基準
（S/A/Bランク相当）で一貫して均等配分が優位かを確認する。

モデルの学習・OOS予測は各バリアント（condition_weights版・均等配分版）
につき1回のウォークフォワード（8フォールド）だけ行い、そのOOS予測に対して
複数の購入判定基準（EV閾値・オッズ上限・クラスフィルタ）を適用して
simulate_per_fold()を使い分ける（再学習は発生しないため高速）。

あわせて、フォールド別の回収率を並べて表示し、「改善が特定1〜2フォールドの
好調に偏っていないか」を、上位フォールドを除外した場合の平均・シャープレシオ・
中央値で確認する（Sharpeは平均ベースの指標のため、外れ値に引っ張られやすい
弱点がある）。
"""

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import compute_sharpe_ratio, fetch_win_payouts, run_model_backtest, simulate_per_fold
from ai.condition_weight_equal_ablation import (
    CATEGORICAL_COLUMNS, FEATURE_COLUMNS_EQUAL, FEATURE_COLUMNS_WEIGHTED, build_equal_weight_dataset,
)
from ai.walk_forward_backtest import generate_folds

# S/A/Bランク相当の購入判定基準（README「買い目推奨ランク」参照）
SETTINGS = [
    ("S(EV1.4/上限30倍)", 1.4, 30, True),
    ("A(EV1.2/上限35倍)", 1.2, 35, True),
    ("B(EV1.0/上限35倍)", 1.0, 35, True),
]

VARIANTS = [
    ("condition_weights版", FEATURE_COLUMNS_WEIGHTED),
    ("均等配分版", FEATURE_COLUMNS_EQUAL),
]


def summarize_recoveries(recoveries):
    valid = [r for r in recoveries if r is not None and not np.isnan(r)]
    if not valid:
        return {"平均": float("nan"), "中央値": float("nan"), "標準偏差": float("nan"), "シャープレシオ": float("nan")}
    return {
        "平均": float(np.mean(valid)),
        "中央値": float(np.median(valid)),
        "標準偏差": float(np.std(valid, ddof=1)) if len(valid) >= 2 else float("nan"),
        "シャープレシオ": compute_sharpe_ratio(valid),
    }


def main():
    print("=" * 70)
    print("dsk_Project")
    print("均等配分(condition_weights無効化)の頑健性チェック")
    print("=" * 70)

    dataset = build_equal_weight_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    oos_results = {}
    for label, feature_columns in VARIANTS:
        print(f"\n--- {label}のウォークフォワード学習・OOS予測を作成中 ---")
        oos_df = run_model_backtest(dataset, folds, feature_columns, CATEGORICAL_COLUMNS, win_payouts, label)
        oos_results[label] = oos_df

    print()
    print("=" * 70)
    print("②複数設定（S/A/B相当）での一貫性チェック")
    print("=" * 70)
    for setting_label, ev_threshold, odds_cap, class_filter in SETTINGS:
        print(f"\n--- {setting_label} ---")
        for label, _ in VARIANTS:
            fold_results = simulate_per_fold(
                oos_results[label], folds, ev_threshold, odds_cap=odds_cap, class_filter=class_filter,
            )
            recoveries = [r["回収率(%)"] for r in fold_results]
            n_buys_total = sum(r["買い件数"] for r in fold_results)
            summary = summarize_recoveries(recoveries)
            print(f"  [{label}] 買い件数合計={n_buys_total:5d}  "
                  f"平均={summary['平均']:6.1f}%  中央値={summary['中央値']:6.1f}%  "
                  f"標準偏差={summary['標準偏差']:5.1f}  シャープレシオ={summary['シャープレシオ']:7.3f}")

    print()
    print("=" * 70)
    print("③S設定における、改善が特定フォールドに偏っていないかの深掘り")
    print("=" * 70)
    ev_threshold, odds_cap, class_filter = 1.4, 30, True
    fold_labels = [f"{s}〜{e}" for s, e in folds]

    per_variant_recoveries = {}
    for label, _ in VARIANTS:
        fold_results = simulate_per_fold(oos_results[label], folds, ev_threshold, odds_cap=odds_cap, class_filter=class_filter)
        per_variant_recoveries[label] = [r["回収率(%)"] for r in fold_results]

    print("\nフォールド別回収率(%)（S設定）:")
    header = "  フォールド" + "".join(f"{i+1:>10d}" for i in range(len(folds)))
    print(header)
    for label, _ in VARIANTS:
        vals = per_variant_recoveries[label]
        row = f"  {label:16s}" + "".join(f"{v:10.1f}" if v is not None and not np.isnan(v) else f"{'N/A':>10s}" for v in vals)
        print(row)
    print("\n  (参考) フォールド期間:")
    for i, fl in enumerate(fold_labels):
        print(f"    フォールド{i+1}: {fl}")

    print("\n--- 上位フォールドを除外した場合の平均・シャープレシオ（外れ値依存度チェック） ---")
    for label, _ in VARIANTS:
        vals = [v for v in per_variant_recoveries[label] if v is not None and not np.isnan(v)]
        sorted_idx = sorted(range(len(vals)), key=lambda i: vals[i], reverse=True)
        full_summary = summarize_recoveries(vals)
        print(f"\n  [{label}]")
        print(f"    全{len(vals)}フォールド: 平均={full_summary['平均']:.1f}%  中央値={full_summary['中央値']:.1f}%  シャープレシオ={full_summary['シャープレシオ']:.3f}")
        for n_exclude in (1, 2):
            excluded_idx = set(sorted_idx[:n_exclude])
            remaining = [v for i, v in enumerate(vals) if i not in excluded_idx]
            s = summarize_recoveries(remaining)
            excluded_vals = [round(vals[i], 1) for i in sorted_idx[:n_exclude]]
            print(f"    上位{n_exclude}フォールド除外後（除外した値: {excluded_vals}）: "
                  f"平均={s['平均']:.1f}%  中央値={s['中央値']:.1f}%  シャープレシオ={s['シャープレシオ']:.3f}")


if __name__ == "__main__":
    main()
