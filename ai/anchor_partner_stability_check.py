"""
dsk_Project
◎(軸)固定・相手EV戦略のフォールド別安定性検証
Version 0.1

ai/anchor_partner_strategy_check.pyで見つかった「馬連: 相手をEV>=1.2で絞ると
回収率87.3%」「3連複: 上位5頭BOXで回収率81.2%」について、これまでの単勝の
グリッドサーチ・頑健性検証（ai/model_a_odds_adjusted_grid_search.py・
ai/equal_weight_robustness_check.py）と同じ考え方で、
  ①フォールド別の回収率・シャープレシオ・標準偏差
  ②馬連のEVしきい値をEV1.0〜1.5で細かく振ったグリッドサーチ（1.2が
    たまたま良く見えているだけの「点」なのか、周辺のしきい値でも
    同じ傾向が続く「帯」なのかを確認する）
を検証する。
"""

import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import compute_sharpe_ratio
from ai.build_dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.train_model import evaluate_model, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager

STAKE = 100
TOP_BOX_SIZE = 5
EV_GRID = [1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.45, 1.50]


def build_oos_predictions_with_fold(dataset, folds, feature_columns, log=print):
    """OOS予測にfold_idx（0-indexed）を付与して返す"""
    all_results = []
    for fold_idx, (test_start, test_end) in enumerate(folds):
        train_df, test_df = split_by_date(dataset, test_start, test_end)
        if len(train_df) == 0 or len(test_df) == 0:
            continue
        model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
        proba, y_test, auc, logloss = evaluate_model(model, test_df, feature_columns)
        log(f"  フォールド{fold_idx + 1} {test_start}〜{test_end}: テスト={len(test_df)} AUC(単勝)={auc:.4f}")

        result = test_df.loc[:, ["race_id", "horse_number", "market_odds", "market_popularity"]].copy()
        result["pred_win_prob"] = proba
        result["fold_idx"] = fold_idx
        all_results.append(result)

    return pd.concat(all_results, ignore_index=True)


def attach_finish_info(oos_df):
    db = DatabaseManager()
    rows = db.fetchall("""
        SELECT e.race_id, e.horse_number, res.finish_position,
               (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = e.race_id) AS field_size
        FROM entries e
        JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
        WHERE res.finish_position IS NOT NULL
    """)
    finish_df = pd.DataFrame(rows, columns=["race_id", "horse_number", "finish_position", "field_size"])
    return oos_df.merge(finish_df, on=["race_id", "horse_number"], how="left")


def load_payout_lookup(bet_type):
    db = DatabaseManager()
    rows = db.fetchall("SELECT race_id, combination, payout_yen FROM payouts WHERE bet_type = ?", (bet_type,))
    lookup = {}
    for race_id, combination, payout_yen in rows:
        lookup.setdefault((race_id, combination), payout_yen)
    return lookup


def umaren_key(a, b):
    return f"{min(a, b)}-{max(a, b)}"


def sanrenpuku_key(a, b, c):
    return "-".join(str(x) for x in sorted([a, b, c]))


def fold_recovery_stats(fold_stakes, fold_payouts):
    """フォールド別のstake/payoutリストから、フォールド別回収率・平均・標準偏差・
    シャープレシオを計算する（購入が無いフォールドはnanとして扱う）"""
    recoveries = []
    for stake, payout in zip(fold_stakes, fold_payouts):
        recoveries.append(payout / stake * 100 if stake > 0 else float("nan"))

    valid = [r for r in recoveries if not np.isnan(r)]
    return {
        "フォールド別回収率": recoveries,
        "平均": float(np.mean(valid)) if valid else float("nan"),
        "標準偏差": float(np.std(valid, ddof=1)) if len(valid) >= 2 else float("nan"),
        "シャープレシオ": compute_sharpe_ratio(valid),
    }


def run_umaren_ev_grid(merged, umaren_payouts, n_folds, log=print):
    """馬連: 相手をEVしきい値で絞る戦略を、EV_GRIDの各値・フォールド別に集計する"""
    grid_stakes = {th: [0] * n_folds for th in EV_GRID}
    grid_payouts = {th: [0.0] * n_folds for th in EV_GRID}
    grid_bets = {th: [0] * n_folds for th in EV_GRID}
    grid_hits = {th: [0] * n_folds for th in EV_GRID}

    for (race_id, fold_idx), group in merged.groupby(["race_id", "fold_idx"]):
        if group["finish_position"].isna().any():
            continue
        anchor_row = group.loc[group["pred_win_prob"].idxmax()]
        anchor_num = anchor_row["horse_number"]
        others = group[group["horse_number"] != anchor_num].copy()
        if len(others) == 0:
            continue
        others["expected_value"] = others["pred_win_prob"] * others["market_odds"]

        actual_top2 = set(group[group["finish_position"] <= 2]["horse_number"])
        anchor_in_top2 = anchor_num in actual_top2
        actual_partner = (actual_top2 - {anchor_num}) if anchor_in_top2 else set()

        for th in EV_GRID:
            partners = others[others["expected_value"] >= th]
            if len(partners) == 0:
                continue
            for _, p in partners.iterrows():
                p_num = p["horse_number"]
                grid_stakes[th][fold_idx] += STAKE
                grid_bets[th][fold_idx] += 1
                hit = anchor_in_top2 and (p_num in actual_partner)
                if hit:
                    grid_hits[th][fold_idx] += 1
                    payout = umaren_payouts.get((race_id, umaren_key(anchor_num, p_num)))
                    if payout is not None:
                        grid_payouts[th][fold_idx] += payout / 100 * STAKE

    return grid_stakes, grid_payouts, grid_bets, grid_hits


def run_sanrenpuku_box_by_fold(merged, sanrenpuku_payouts, n_folds, log=print):
    """3連複: 上位5頭BOX戦略をフォールド別に集計する"""
    stakes = [0] * n_folds
    payouts = [0.0] * n_folds
    bets = [0] * n_folds
    hits = [0] * n_folds

    for (race_id, fold_idx), group in merged.groupby(["race_id", "fold_idx"]):
        if group["finish_position"].isna().any() or len(group) < 3:
            continue
        ranked = group.sort_values("pred_win_prob", ascending=False).reset_index(drop=True)
        anchor_num = ranked.loc[0, "horse_number"]
        others = ranked.iloc[1:]

        actual_top3 = group[group["finish_position"] <= 3]["horse_number"].tolist()
        anchor_in_top3 = anchor_num in actual_top3
        actual_other_two = frozenset(set(actual_top3) - {anchor_num}) if anchor_in_top3 and len(actual_top3) == 3 else None

        box_others = others.iloc[: TOP_BOX_SIZE - 1]
        if len(box_others) < 2:
            continue
        box_nums = box_others["horse_number"].tolist()
        for a, b in combinations(box_nums, 2):
            stakes[fold_idx] += STAKE
            bets[fold_idx] += 1
            hit = anchor_in_top3 and actual_other_two == frozenset({a, b})
            if hit:
                hits[fold_idx] += 1
                payout = sanrenpuku_payouts.get((race_id, sanrenpuku_key(anchor_num, a, b)))
                if payout is not None:
                    payouts[fold_idx] += payout / 100 * STAKE

    return stakes, payouts, bets, hits


def main():
    print("=" * 70)
    print("dsk_Project")
    print("◎(軸)固定・相手EV戦略のフォールド別安定性検証")
    print("=" * 70)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    n_folds = len(folds)
    print(f"データセット件数: {len(dataset)}  フォールド数: {n_folds}")
    fold_labels = [f"{s}〜{e}" for s, e in folds]

    oos_df = build_oos_predictions_with_fold(dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED)
    merged = attach_finish_info(oos_df)
    merged = merged.dropna(subset=["field_size"])

    umaren_payouts = load_payout_lookup("馬連")
    sanrenpuku_payouts = load_payout_lookup("3連複")

    print()
    print("=" * 70)
    print("①②馬連: EVしきい値グリッドサーチ（EV1.00〜1.50）・フォールド別安定性")
    print("=" * 70)
    grid_stakes, grid_payouts, grid_bets, grid_hits = run_umaren_ev_grid(merged, umaren_payouts, n_folds)

    print(f"\n{'EVしきい値':>10s}{'総購入点数':>12s}{'総的中数':>10s}{'全体回収率':>12s}{'平均':>9s}{'標準偏差':>9s}{'シャープレシオ':>14s}")
    for th in EV_GRID:
        stats = fold_recovery_stats(grid_stakes[th], grid_payouts[th])
        total_stake = sum(grid_stakes[th])
        total_payout = sum(grid_payouts[th])
        overall_recovery = total_payout / total_stake * 100 if total_stake > 0 else float("nan")
        total_bets = sum(grid_bets[th])
        total_hits = sum(grid_hits[th])
        print(f"{th:>10.2f}{total_bets:>12,}{total_hits:>10,}{overall_recovery:>11.1f}%"
              f"{stats['平均']:>8.1f}%{stats['標準偏差']:>9.1f}{stats['シャープレシオ']:>14.3f}")

    print("\n--- EV>=1.20のフォールド別内訳（詳細） ---")
    stats_120 = fold_recovery_stats(grid_stakes[1.20], grid_payouts[1.20])
    for i, (label, recovery, bets, hits) in enumerate(zip(
        fold_labels, stats_120["フォールド別回収率"], grid_bets[1.20], grid_hits[1.20]
    )):
        recovery_str = f"{recovery:6.1f}%" if not np.isnan(recovery) else "  N/A  "
        print(f"  フォールド{i + 1} ({label}): 購入点数={bets:4d} 的中数={hits:3d} 回収率={recovery_str}")
    print(f"  → 平均={stats_120['平均']:.1f}%  標準偏差={stats_120['標準偏差']:.1f}  シャープレシオ={stats_120['シャープレシオ']:.3f}")

    print()
    print("=" * 70)
    print("③3連複 上位5頭BOX: フォールド別安定性")
    print("=" * 70)
    s_stakes, s_payouts, s_bets, s_hits = run_sanrenpuku_box_by_fold(merged, sanrenpuku_payouts, n_folds)
    stats_box = fold_recovery_stats(s_stakes, s_payouts)
    for i, (label, recovery, bets, hits) in enumerate(zip(
        fold_labels, stats_box["フォールド別回収率"], s_bets, s_hits
    )):
        recovery_str = f"{recovery:6.1f}%" if not np.isnan(recovery) else "  N/A  "
        print(f"  フォールド{i + 1} ({label}): 購入点数={bets:4d} 的中数={hits:3d} 回収率={recovery_str}")
    total_stake = sum(s_stakes)
    total_payout = sum(s_payouts)
    overall_recovery = total_payout / total_stake * 100 if total_stake > 0 else float("nan")
    print(f"  → 全体回収率={overall_recovery:.1f}%  フォールド平均={stats_box['平均']:.1f}%  "
          f"標準偏差={stats_box['標準偏差']:.1f}  シャープレシオ={stats_box['シャープレシオ']:.3f}")


if __name__ == "__main__":
    main()
