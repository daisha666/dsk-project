"""
dsk_Project
①トラックバイアス補正の本番反映前・最終確認
Version 0.1

本番パイプライン経由（ai/build_dataset.py::build_dataset()。
FEATURE_COLUMNS_A_ODDS_ADJUSTEDに組み込み済み）で、ai/track_bias_experiment.py
の検証結果と一致するかを再確認し、あわせてF6（2025-08-24〜2025-12-21）が
悪化した原因を、Sランク購入対象の入れ替わりから調べる。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import compute_sharpe_ratio, is_class_included
from ai.build_dataset import (
    CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, TRACK_BIAS_COLUMNS, build_dataset,
)
from ai.train_model import evaluate_model, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager

S_EV_THRESHOLD, S_ODDS_CAP = 1.4, 30
FEATURE_COLUMNS_NO_BIAS = [c for c in FEATURE_COLUMNS_A_ODDS_ADJUSTED if c not in TRACK_BIAS_COLUMNS]


def score_fold(dataset, feature_columns, test_start, test_end, win_payouts):
    train_df, test_df = split_by_date(dataset, test_start, test_end)
    if len(train_df) == 0 or len(test_df) == 0:
        return None
    model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
    proba, y_test, auc, _logloss = evaluate_model(model, test_df, feature_columns)

    result = test_df.loc[:, ["race_id", "horse_number", "market_odds", "race_class"]].copy()
    result["label"] = y_test.values
    result["pred_win_prob"] = proba
    result["expected_value"] = result["pred_win_prob"] * result["market_odds"]

    candidates = result[
        (result["expected_value"] >= S_EV_THRESHOLD)
        & (result["market_odds"] <= S_ODDS_CAP)
        & (result["race_class"].apply(is_class_included))
    ].copy()
    n_buys = len(candidates)
    if n_buys == 0:
        return {"auc": auc, "recovery": float("nan"), "candidates": candidates}

    hits = candidates[candidates["label"] == 1]
    total_stake = n_buys * 100
    total_payout = sum(win_payouts.get((r["race_id"], int(r["horse_number"])), 0) / 100 * 100 for _, r in hits.iterrows())
    recovery = total_payout / total_stake * 100
    return {"auc": auc, "recovery": recovery, "candidates": candidates}


def main():
    print("=" * 70)
    print("dsk_Project")
    print("①トラックバイアス補正: 本番パイプライン経由の最終確認")
    print("=" * 70)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    fold_labels = [f"{s}〜{e}" for s, e in folds]

    db = DatabaseManager()
    win_payouts = {}
    for race_id, horse_number, payout_yen in db.fetchall("SELECT race_id, combination, payout_yen FROM payouts WHERE bet_type='単勝'"):
        try:
            win_payouts[(race_id, int(horse_number))] = payout_yen
        except ValueError:
            pass

    print("\n--- 本番パイプライン経由での再確認（ai/track_bias_experiment.pyの数字と一致するはず） ---")
    fold_data = {"バイアス無し": [], "バイアス有り（本番）": []}
    for label, feature_columns in [("バイアス無し", FEATURE_COLUMNS_NO_BIAS),
                                    ("バイアス有り（本番）", FEATURE_COLUMNS_A_ODDS_ADJUSTED)]:
        for test_start, test_end in folds:
            r = score_fold(dataset, feature_columns, test_start, test_end, win_payouts)
            fold_data[label].append(r)

        aucs = [r["auc"] for r in fold_data[label] if r]
        recoveries = [r["recovery"] for r in fold_data[label] if r]
        valid = [r for r in recoveries if not np.isnan(r)]
        sharpe = compute_sharpe_ratio(valid)
        print(f"\n  [{label}]")
        print(f"    平均AUC={np.mean(aucs):.4f}")
        print(f"    フォールド別回収率: {[round(r,1) if not np.isnan(r) else None for r in recoveries]}")
        print(f"    平均={np.mean(valid):.1f}%  標準偏差={np.std(valid, ddof=1):.1f}  シャープレシオ={sharpe:.3f}")

    print()
    print("=" * 70)
    print("F6（2025-08-24〜2025-12-21）が悪化した原因の調査")
    print("=" * 70)
    f6_idx = 5
    no_bias_candidates = fold_data["バイアス無し"][f6_idx]["candidates"]
    with_bias_candidates = fold_data["バイアス有り（本番）"][f6_idx]["candidates"]

    no_bias_keys = set(zip(no_bias_candidates["race_id"], no_bias_candidates["horse_number"]))
    with_bias_keys = set(zip(with_bias_candidates["race_id"], with_bias_candidates["horse_number"]))

    common = no_bias_keys & with_bias_keys
    only_no_bias = no_bias_keys - with_bias_keys
    only_with_bias = with_bias_keys - no_bias_keys

    print(f"F6の購入対象: バイアス無し={len(no_bias_keys)}件 バイアス有り={len(with_bias_keys)}件")
    print(f"  共通={len(common)}件  バイアス無しのみ={len(only_no_bias)}件  バイアス有りのみ={len(only_with_bias)}件")

    def hit_summary(candidates, keys):
        sub = candidates[candidates.apply(lambda r: (r["race_id"], r["horse_number"]) in keys, axis=1)]
        n = len(sub)
        n_hits = int(sub["label"].sum()) if n else 0
        return n, n_hits

    n_common_old, hits_common_old = hit_summary(no_bias_candidates, common)
    n_only_old, hits_only_old = hit_summary(no_bias_candidates, only_no_bias)
    n_only_new, hits_only_new = hit_summary(with_bias_candidates, only_with_bias)

    print(f"\n  共通で買われた{n_common_old}件中 的中={hits_common_old}件（両モデルとも同じ的中実績のはず）")
    print(f"  バイアス無しだけが買った{n_only_old}件中 的中={hits_only_old}件（バイアス有りが「除外」した馬）")
    print(f"  バイアス有りだけが買った{n_only_new}件中 的中={hits_only_new}件（バイアス有りが「追加」した馬）")
    print("\n  → バイアス有りが除外した馬の的中率と、追加した馬の的中率を比較することで、")
    print("    「入れ替わった一部の馬」がF6の結果を左右したかどうかが分かる")


if __name__ == "__main__":
    main()
