"""
dsk_Project
Stage3: 「改善」と結論づけた施策の再検証（フォールド別回収率・シャープレシオ）
Version 0.1

これまでStage3で「改善」と報告した2つの施策について、合算回収率だけでなく
フォールド別の回収率（平均・標準偏差・最小・最大）とシャープレシオで
再確認する。ai/model_a_odds_adjusted_ablation.pyで、追加前のモデルAでさえ
フォールド別に見ると44.49%〜167.50%（標準偏差42.94pt）と非常に不安定
だったことが分かったため、他の「改善」報告も同じ検証をしていなかった。

  ①1勝クラス以上限定（ai/class_and_top1_backtest.pyで報告）:
    モデルA/B × EV1.20/1.50 の4パターンで、新馬戦・未勝利戦を除外すると
    合算回収率が一貫して改善（+1.4〜+4.0pt）と報告した。
  ②EV閾値1.20→1.50への引き上げ:
    ai/backtest.pyのオッズ上限別グリッド（オッズ上限30倍の列）で、
    EV閾値を1.20から1.50に上げると合算回収率が上がる傾向を確認していた
    （個別の「施策」としては明示的に比較していなかったため、本スクリプトで
    改めて単体の効果として切り出す）。

モデルA・モデルBそれぞれについて、OOS予測（8フォールド、
ai/backtest.py::run_model_backtestで一度だけ学習）に対し、
EV閾値{1.20, 1.50} × クラスフィルタ{なし, 1勝クラス以上}の
2×2=4通りをai/backtest.py::simulate_per_foldで評価する
（学習はモデルごとに1回のみ。EV閾値・クラスフィルタの変更は
学習済みOOS予測に対する後段のフィルタ処理のため再学習不要）。
オッズ上限は既存の基準値30倍で固定する。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import (
    BASE_FEATURE_COLUMNS,
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    compute_sharpe_ratio,
    fetch_win_payouts,
    run_model_backtest,
    simulate,
    simulate_per_fold,
)
from ai.build_dataset import build_dataset
from ai.walk_forward_backtest import generate_folds

ODDS_CAP = 30
EV_THRESHOLDS = [1.20, 1.50]
CLASS_FILTERS = [False, True]


def evaluate_config(df, folds, ev_threshold, class_filter):
    """1つの(EV閾値, クラスフィルタ)設定について、合算回収率とフォールド別
    統計・シャープレシオを計算する"""
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
        "有効フォールド数": len(valid),
        "シャープレシオ": compute_sharpe_ratio(recoveries),
        "fold_results": fold_results,
    }


def main():
    print("=" * 70)
    print("dsk_Project")
    print("Stage3: 施策再検証（1勝クラス以上限定 / EV1.20→1.50、フォールド別・シャープレシオ）")
    print("=" * 70)
    print(f"オッズ上限={ODDS_CAP}倍固定")

    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()

    print()
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    print()
    print("--- モデルA（市場情報あり）のフォールド別学習・予測 ---")
    df_a = run_model_backtest(dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルA")

    print()
    print("--- モデルB（市場情報除外）のフォールド別学習・予測 ---")
    df_b = run_model_backtest(dataset, folds, BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルB")

    rows = []
    detail = {}
    for model_label, df in [("モデルA", df_a), ("モデルB", df_b)]:
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
    print("=== 全設定 一覧（EV閾値 × クラスフィルタ × モデル） ===")
    print("=" * 70)
    print(full_df.to_string(index=False))

    print()
    print("=" * 70)
    print("=== ①1勝クラス以上限定の効果（EV閾値を固定してクラスフィルタあり/なしを比較） ===")
    print("=" * 70)
    for model_label in ("モデルA", "モデルB"):
        for ev_threshold in EV_THRESHOLDS:
            off = detail[(model_label, ev_threshold, False)]
            on = detail[(model_label, ev_threshold, True)]
            print(f"\n{model_label} / EV>={ev_threshold}")
            print(f"  全クラス   : 合算={off['合算回収率(%)']:.2f}%  "
                  f"フォールド平均={off['平均回収率(%)']:.2f}%  標準偏差={off['標準偏差(pt)']:.2f}pt  "
                  f"シャープ={off['シャープレシオ']:.3f}")
            print(f"  1勝以上    : 合算={on['合算回収率(%)']:.2f}%  "
                  f"フォールド平均={on['平均回収率(%)']:.2f}%  標準偏差={on['標準偏差(pt)']:.2f}pt  "
                  f"シャープ={on['シャープレシオ']:.3f}")
            print(f"  合算差={on['合算回収率(%)'] - off['合算回収率(%)']:+.2f}pt  "
                  f"シャープ差={on['シャープレシオ'] - off['シャープレシオ']:+.3f}")

    print()
    print("=" * 70)
    print("=== ②EV閾値1.20→1.50の効果（クラスフィルタを固定してEV閾値を比較） ===")
    print("=" * 70)
    for model_label in ("モデルA", "モデルB"):
        for class_filter in CLASS_FILTERS:
            low = detail[(model_label, 1.20, class_filter)]
            high = detail[(model_label, 1.50, class_filter)]
            label = "1勝以上" if class_filter else "全クラス"
            print(f"\n{model_label} / {label}")
            print(f"  EV>=1.20   : 合算={low['合算回収率(%)']:.2f}%  "
                  f"フォールド平均={low['平均回収率(%)']:.2f}%  標準偏差={low['標準偏差(pt)']:.2f}pt  "
                  f"シャープ={low['シャープレシオ']:.3f}")
            print(f"  EV>=1.50   : 合算={high['合算回収率(%)']:.2f}%  "
                  f"フォールド平均={high['平均回収率(%)']:.2f}%  標準偏差={high['標準偏差(pt)']:.2f}pt  "
                  f"シャープ={high['シャープレシオ']:.3f}")
            print(f"  合算差={high['合算回収率(%)'] - low['合算回収率(%)']:+.2f}pt  "
                  f"シャープ差={high['シャープレシオ'] - low['シャープレシオ']:+.3f}")

    print()
    print("=" * 70)
    print("=== フォールド別回収率の詳細（参考） ===")
    print("=" * 70)
    for (model_label, ev_threshold, class_filter), result in detail.items():
        label = "1勝以上" if class_filter else "全クラス"
        print(f"\n{model_label} / EV>={ev_threshold} / {label}")
        for r in result["fold_results"]:
            print(f"  {r['テスト期間']}: 買い件数={r['買い件数']} 的中数={r['的中数']} 回収率={r['回収率(%)']:.2f}%")


if __name__ == "__main__":
    main()
