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
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import CATEGORICAL_COLUMNS, fetch_win_payouts, run_model_backtest, simulate
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
    print("=== 比較結果 ===")
    print("=" * 60)
    print(f"{'':40s}{'買い件数':>10s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    print(f"{'モデルA（追加前）':40s}{result_before['買い件数']:10d}"
          f"{result_before['的中率(%)']:12.2f}{result_before['回収率(%)']:12.2f}")
    print(f"{'モデルA\'（odds_adjusted_score追加後）':40s}{result_after['買い件数']:10d}"
          f"{result_after['的中率(%)']:12.2f}{result_after['回収率(%)']:12.2f}")
    print()
    diff = result_after["回収率(%)"] - result_before["回収率(%)"]
    print(f"回収率差（追加後－追加前） = {diff:+.2f}pt")


if __name__ == "__main__":
    main()
