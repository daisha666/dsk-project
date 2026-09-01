"""
dsk_Project
Stage3: 1勝クラス以上限定 / EV最大1頭のみ購入 の効果検証（モデルA/B比較）
Version 0.1

PROJECT_EVで既に効果が実証されている2つの施策を、dsk_Projectのモデル
A（市場情報あり）・B（市場情報除外）それぞれに適用し、同様の改善が
見られるか確認する。EV閾値・オッズ上限のチューニングはまだ行わず、
前回のバックテスト（ai/backtest.py）で使った基準値（EV>=1.20/1.50、
オッズ上限30倍）のまま、購入判定の絞り込み方だけを変える。

施策1: 1勝クラス以上限定（PROJECT_EVのai/class_filter_backtest.py）
  新馬戦・未勝利戦を除外する。モデルの学習データは変えず、購入判定の
  対象レースを絞り込むだけ（ai/backtest.py::classify_class_tier /
  is_class_included参照）。

施策2: EV最大1頭のみ購入（PROJECT_EVのai/top1_ev_backtest.py）
  同一レースで複数の推奨馬（EV閾値・オッズ上限を満たす馬）がいる場合、
  期待値が最大の1頭だけを購入する（ai/backtest.py::select_top1_per_race参照）。

学習・予測（モデルA/Bのウォークフォワード）はai/backtest.py::run_model_backtest
を再利用し、1回だけ実行する（施策の有無で学習をやり直す必要は無い。
いずれも購入判定時点の絞り込みロジックのみが変わるため）。
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import (
    BASE_FEATURE_COLUMNS,
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    fetch_win_payouts,
    run_model_backtest,
    simulate,
)
from ai.build_dataset import build_dataset
from ai.walk_forward_backtest import generate_folds

CONFIGS = [
    ("ベースライン（全クラス・全頭購入）", False, False),
    ("1勝クラス以上限定", True, False),
    ("EV最大1頭のみ購入", False, True),
    ("1勝クラス以上限定 + EV最大1頭のみ", True, True),
]

REFERENCE_SETTINGS = [
    ("EV>=1.20・オッズ上限30倍", 1.20, 30),
    ("EV>=1.50・オッズ上限30倍", 1.50, 30),
]


def report_class_volume(dataset, log=print):
    """データセット全体で、新馬戦・未勝利戦を除外するとレース数がどれだけ減るかを集計する"""
    from ai.backtest import classify_class_tier, is_class_included

    races = dataset.drop_duplicates("race_id")[["race_id", "race_class"]].copy()
    races["included"] = races["race_class"].apply(is_class_included)
    races["tier"] = races["race_class"].apply(classify_class_tier)

    total = len(races)
    included = int(races["included"].sum())
    excluded = total - included

    log(f"全レース数: {total}")
    log(f"1勝クラス以上（対象）: {included}（{included / total * 100:.1f}%）")
    log(f"除外（新馬戦・未勝利戦）: {excluded}（{excluded / total * 100:.1f}%）")
    for tier, n in races.loc[~races["included"], "tier"].value_counts().items():
        log(f"  内訳 - {tier}: {n}")


def run_config_matrix(df, model_label, log=print):
    """1モデル分のOOS予測に対し、REFERENCE_SETTINGS × CONFIGS の全組み合わせを計算する"""
    rows = []
    for setting_label, ev_th, odds_cap in REFERENCE_SETTINGS:
        for config_label, class_filter, top1 in CONFIGS:
            result = simulate(df, ev_th, odds_cap=odds_cap, class_filter=class_filter, top1=top1)
            rows.append({
                "モデル": model_label,
                "設定": setting_label,
                "施策": config_label,
                "買い件数": result["買い件数"],
                "買い対象レース数": result["買い対象レース数"],
                "的中数": result["的中数"],
                "的中率(%)": result["的中率(%)"],
                "回収率(%)": result["回収率(%)"],
            })
    return pd.DataFrame(rows)


def main():
    print("=" * 60)
    print("dsk_Project")
    print("Stage3: 1勝クラス以上限定 / EV最大1頭のみ購入 の効果検証")
    print("=" * 60)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()

    print()
    print("--- クラス別レース数（データセット全体） ---")
    report_class_volume(dataset, log=print)

    print()
    print("--- モデルA（市場情報あり）のフォールド別学習・予測 ---")
    df_a = run_model_backtest(dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルA")

    print()
    print("--- モデルB（市場情報除外）のフォールド別学習・予測 ---")
    df_b = run_model_backtest(dataset, folds, BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルB")

    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
    pd.set_option("display.width", 200)

    matrix_a = run_config_matrix(df_a, "モデルA", log=print)
    matrix_b = run_config_matrix(df_b, "モデルB", log=print)
    matrix = pd.concat([matrix_a, matrix_b], ignore_index=True)

    for setting_label, _, _ in REFERENCE_SETTINGS:
        print()
        print(f"=== {setting_label} ===")
        sub = matrix[matrix["設定"] == setting_label].drop(columns=["設定"])
        print(sub.to_string(index=False))

    print()
    print("=== ベースライン比の回収率変化(pt) ===")
    diff_rows = []
    for model_label in ("モデルA", "モデルB"):
        for setting_label, _, _ in REFERENCE_SETTINGS:
            base = matrix[
                (matrix["モデル"] == model_label) & (matrix["設定"] == setting_label)
                & (matrix["施策"] == CONFIGS[0][0])
            ]["回収率(%)"].iloc[0]
            for config_label, _, _ in CONFIGS[1:]:
                cur = matrix[
                    (matrix["モデル"] == model_label) & (matrix["設定"] == setting_label)
                    & (matrix["施策"] == config_label)
                ]["回収率(%)"].iloc[0]
                diff_rows.append({
                    "モデル": model_label, "設定": setting_label, "施策": config_label,
                    "ベースライン回収率(%)": round(base, 2),
                    "施策後回収率(%)": round(cur, 2),
                    "差分(pt)": round(cur - base, 2),
                })
    diff_df = pd.DataFrame(diff_rows)
    print(diff_df.to_string(index=False))


if __name__ == "__main__":
    main()
