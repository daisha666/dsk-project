"""
dsk_Project
Stage3: モデルAへのodds_adjusted_score追加検証
Version 0.1

モデルA（市場情報あり）に、8項目合成スコアのオッズ反映後版
odds_adjusted_score（overall_score×(1/オッズ)×係数、開発指示書2.3の
「AK列」相当）を新しい特徴量として追加し、これまでの最良設定
（EV閾値1.50・オッズ上限30倍・1勝クラス以上限定。
ai/class_and_top1_backtest.pyで確認した回収率87.49%）の回収率が
どう変化するかを検証する。

odds_adjusted_scoreはoverall_scoreとmarket_odds（いずれも既にモデルAの
特徴量）から決定的に導出される値であり、モデルAにとっては新しい情報源の
追加ではなく「既存2特徴量の組み合わせ方をあらかじめ計算して渡す」操作に
近い（ai/build_dataset.pyのFEATURE_COLUMNS_A_ODDS_ADJUSTED参照）。
LightGBM（決定木）は特徴量同士の比を直接分割に使えないため、比を明示的に
渡すことで学習効率が上がるかどうかを確認する。

学習・OOS予測・EV計算はai/backtest.pyと同じ8フォールドのウォークフォワード。
比較する2モデル:
  モデルA（現行）    : ai.build_dataset.FEATURE_COLUMNS
  モデルA'（追加後） : ai.build_dataset.FEATURE_COLUMNS_A_ODDS_ADJUSTED

初回検証（合算回収率87.49% → 108.53%）はフォールド別のばらつきが大きく
（8フォールド中3フォールドが100%割れ、うち直近フォールドは23.64%）、
合算回収率だけでは「本当に優れているか」を判断できなかった。そのため
本スクリプトではフォールド別回収率からシャープレシオ
（(平均回収率-100)÷標準偏差、ai/backtest.py::compute_sharpe_ratio。
PROJECT_EVのai/backtest.pyと同じ定義）を計算し、リスク調整後でも
優位と言えるかを判定する。
"""

import sys
from pathlib import Path

import numpy as np

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

EV_THRESHOLD = 1.50
ODDS_CAP = 30
CLASS_FILTER = True


def main():
    print("=" * 60)
    print("dsk_Project")
    print("Stage3: モデルAへのodds_adjusted_score追加検証")
    print("=" * 60)
    print(f"比較設定: EV>={EV_THRESHOLD}・オッズ上限{ODDS_CAP}倍・"
          f"{'1勝クラス以上限定' if CLASS_FILTER else '全クラス'}")

    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()

    print()
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    print()
    print("--- モデルA（現行、odds_adjusted_scoreなし）のフォールド別学習・予測 ---")
    df_before = run_model_backtest(dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルA")

    print()
    print("--- モデルA'（odds_adjusted_score追加後）のフォールド別学習・予測 ---")
    df_after = run_model_backtest(
        dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED, CATEGORICAL_COLUMNS, win_payouts, "モデルA'"
    )

    result_before = simulate(df_before, EV_THRESHOLD, odds_cap=ODDS_CAP, class_filter=CLASS_FILTER)
    result_after = simulate(df_after, EV_THRESHOLD, odds_cap=ODDS_CAP, class_filter=CLASS_FILTER)

    print()
    print("=" * 60)
    print("=== 合算回収率の比較 ===")
    print("=" * 60)
    print(f"{'':40s}{'買い件数':>10s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    print(f"{'モデルA（追加前）':40s}{result_before['買い件数']:10d}"
          f"{result_before['的中率(%)']:12.2f}{result_before['回収率(%)']:12.2f}")
    print(f"{'モデルA\'（odds_adjusted_score追加後）':40s}{result_after['買い件数']:10d}"
          f"{result_after['的中率(%)']:12.2f}{result_after['回収率(%)']:12.2f}")
    print()
    diff = result_after["回収率(%)"] - result_before["回収率(%)"]
    print(f"回収率差（追加後－追加前） = {diff:+.2f}pt")

    print()
    print("=" * 60)
    print("=== フォールド別回収率とシャープレシオ ===")
    print("=" * 60)

    fold_results_before = simulate_per_fold(
        df_before, folds, EV_THRESHOLD, odds_cap=ODDS_CAP, class_filter=CLASS_FILTER
    )
    fold_results_after = simulate_per_fold(
        df_after, folds, EV_THRESHOLD, odds_cap=ODDS_CAP, class_filter=CLASS_FILTER
    )

    print()
    print("モデルA（追加前）:")
    for r in fold_results_before:
        print(f"  {r['テスト期間']}: 買い件数={r['買い件数']} 的中数={r['的中数']} 回収率={r['回収率(%)']:.2f}%")

    print()
    print("モデルA'（odds_adjusted_score追加後）:")
    for r in fold_results_after:
        print(f"  {r['テスト期間']}: 買い件数={r['買い件数']} 的中数={r['的中数']} 回収率={r['回収率(%)']:.2f}%")

    recoveries_before = [r["回収率(%)"] for r in fold_results_before]
    recoveries_after = [r["回収率(%)"] for r in fold_results_after]

    valid_before = [v for v in recoveries_before if v is not None and not np.isnan(v)]
    valid_after = [v for v in recoveries_after if v is not None and not np.isnan(v)]

    sharpe_before = compute_sharpe_ratio(recoveries_before)
    sharpe_after = compute_sharpe_ratio(recoveries_after)

    print()
    print("=" * 60)
    print("=== シャープレシオ比較（フォールド別回収率ベース） ===")
    print("=" * 60)
    print(f"{'':40s}{'平均回収率(%)':>14s}{'標準偏差(pt)':>14s}{'最小(%)':>10s}{'最大(%)':>10s}{'シャープレシオ':>14s}")
    print(f"{'モデルA（追加前）':40s}"
          f"{np.mean(valid_before):14.2f}{np.std(valid_before, ddof=1):14.2f}"
          f"{min(valid_before):10.2f}{max(valid_before):10.2f}{sharpe_before:14.3f}")
    print(f"{'モデルA\'（odds_adjusted_score追加後）':40s}"
          f"{np.mean(valid_after):14.2f}{np.std(valid_after, ddof=1):14.2f}"
          f"{min(valid_after):10.2f}{max(valid_after):10.2f}{sharpe_after:14.3f}")

    print()
    if sharpe_after > sharpe_before:
        print(f"シャープレシオでもモデルA'が上回る（{sharpe_after:.3f} > {sharpe_before:.3f}）。"
              f"ただし絶対値の解釈には注意（フォールド数が8と少ない）。")
    else:
        print(f"シャープレシオではモデルA'は上回らない（{sharpe_after:.3f} <= {sharpe_before:.3f}）。"
              f"合算回収率の改善は主にボラティリティの増加を伴っており、"
              f"リスク調整後で見ると「優れている」とは言い切れない。")


if __name__ == "__main__":
    main()
