"""
dsk_Project
追加検証: ①S基準の1日あたり購入件数分布 ②3連複のBOXサイズ拡張 ③ワイドのEV戦略
Version 0.1

OOS予測（8フォールド、ai/anchor_partner_stability_check.pyと同じ枠組み）を
1回だけ作り、以下3点を検証する。
  ①S基準（EV>=1.4・オッズ上限30倍・1勝クラス以上限定）で、1日あたりの
    購入件数が0件になる頻度・連続0件日数の実績
  ②3連複: 上位5/6/7/8頭BOXそれぞれの回収率・フォールド別安定性・
    的中時平均払戻金額
  ③ワイド: 馬連と同じ「相手をEVしきい値で絞る」戦略のグリッドサーチ・
    フォールド別安定性
"""

import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import compute_sharpe_ratio, is_class_included
from ai.build_dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.train_model import evaluate_model, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager

STAKE = 100
BOX_SIZES = [5, 6, 7, 8]
EV_GRID = [1.00, 1.10, 1.20, 1.30, 1.40, 1.50]
S_EV_THRESHOLD, S_ODDS_CAP = 1.4, 30


def build_oos_predictions_with_fold(dataset, folds, feature_columns, log=print):
    all_results = []
    for fold_idx, (test_start, test_end) in enumerate(folds):
        train_df, test_df = split_by_date(dataset, test_start, test_end)
        if len(train_df) == 0 or len(test_df) == 0:
            continue
        model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
        proba, y_test, auc, logloss = evaluate_model(model, test_df, feature_columns)
        log(f"  フォールド{fold_idx + 1} {test_start}〜{test_end}: テスト={len(test_df)} AUC(単勝)={auc:.4f}")

        result = test_df.loc[:, ["race_id", "race_date", "race_class", "horse_number", "market_odds", "market_popularity"]].copy()
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
    recoveries = [payout / stake * 100 if stake > 0 else float("nan") for stake, payout in zip(fold_stakes, fold_payouts)]
    valid = [r for r in recoveries if not np.isnan(r)]
    return {
        "フォールド別回収率": recoveries,
        "平均": float(np.mean(valid)) if valid else float("nan"),
        "標準偏差": float(np.std(valid, ddof=1)) if len(valid) >= 2 else float("nan"),
        "シャープレシオ": compute_sharpe_ratio(valid),
    }


# ------------------------------------------------------------
# ①S基準の1日あたり購入件数分布
# ------------------------------------------------------------

def check_daily_s_rank_volume(merged, dataset_race_dates, log=print):
    merged = merged.copy()
    merged["expected_value"] = merged["pred_win_prob"] * merged["market_odds"]
    s_picks = merged[
        (merged["expected_value"] >= S_EV_THRESHOLD)
        & (merged["market_odds"] <= S_ODDS_CAP)
        & (merged["race_class"].apply(is_class_included))
    ]

    counts_by_date = s_picks.groupby("race_date").size()
    all_dates = sorted(dataset_race_dates)
    daily_counts = pd.Series([counts_by_date.get(d, 0) for d in all_dates], index=all_dates)

    n_days = len(daily_counts)
    n_zero_days = int((daily_counts == 0).sum())

    # 連続0件日数（開催日ベース。開催の無い日は対象外）
    max_streak = 0
    current_streak = 0
    streaks = []
    for c in daily_counts:
        if c == 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            if current_streak > 0:
                streaks.append(current_streak)
            current_streak = 0
    if current_streak > 0:
        streaks.append(current_streak)

    print(f"対象開催日数: {n_days}")
    print(f"購入0件の日: {n_zero_days}日 ({n_zero_days / n_days * 100:.1f}%)")
    print(f"連続0件日数の最大: {max_streak}日")
    if streaks:
        print(f"連続0件日数の分布（発生回数）: {dict(pd.Series(streaks).value_counts().sort_index())}")

    print("\n1日あたり購入件数の分布:")
    bins = [0, 1, 2, 3, 5, 10, 20, 10**9]
    bin_labels = ["0件", "1件", "2件", "3〜4件", "5〜9件", "10〜19件", "20件以上"]
    binned = pd.cut(daily_counts, bins=bins, labels=bin_labels, right=False)
    for label, count in binned.value_counts().sort_index().items():
        print(f"  {label}: {count}日 ({count / n_days * 100:.1f}%)")
    print(f"\n1日あたり平均購入件数: {daily_counts.mean():.2f}件  中央値: {daily_counts.median():.1f}件  "
          f"最大: {daily_counts.max()}件")


# ------------------------------------------------------------
# ②3連複: BOXサイズ5〜8での比較
# ------------------------------------------------------------

def run_sanrenpuku_box_by_fold(merged, sanrenpuku_payouts, n_folds, box_size, log=print):
    stakes = [0] * n_folds
    payouts = [0.0] * n_folds
    bets = [0] * n_folds
    hits = [0] * n_folds
    hit_payout_values = []

    for (race_id, fold_idx), group in merged.groupby(["race_id", "fold_idx"]):
        if group["finish_position"].isna().any() or len(group) < 3:
            continue
        ranked = group.sort_values("pred_win_prob", ascending=False).reset_index(drop=True)
        anchor_num = ranked.loc[0, "horse_number"]
        others = ranked.iloc[1:]

        actual_top3 = group[group["finish_position"] <= 3]["horse_number"].tolist()
        anchor_in_top3 = anchor_num in actual_top3
        actual_other_two = frozenset(set(actual_top3) - {anchor_num}) if anchor_in_top3 and len(actual_top3) == 3 else None

        box_others = others.iloc[: box_size - 1]
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
                    hit_payout_values.append(payout)

    return stakes, payouts, bets, hits, hit_payout_values


def check_sanrenpuku_box_sizes(merged, sanrenpuku_payouts, n_folds, fold_labels, log=print):
    for box_size in BOX_SIZES:
        stakes, payouts, bets, hits, hit_payout_values = run_sanrenpuku_box_by_fold(
            merged, sanrenpuku_payouts, n_folds, box_size
        )
        stats = fold_recovery_stats(stakes, payouts)
        total_stake, total_payout = sum(stakes), sum(payouts)
        overall_recovery = total_payout / total_stake * 100 if total_stake > 0 else float("nan")
        n_combos = box_size - 1
        n_combos = n_combos * (n_combos - 1) // 2
        avg_hit_payout = float(np.mean(hit_payout_values)) if hit_payout_values else float("nan")

        print(f"\n--- 上位{box_size}頭BOX（1レースあたり{n_combos}点） ---")
        print(f"  全体回収率={overall_recovery:.1f}%  フォールド平均={stats['平均']:.1f}%  "
              f"標準偏差={stats['標準偏差']:.1f}  シャープレシオ={stats['シャープレシオ']:.3f}")
        print(f"  的中時平均払戻: {avg_hit_payout:,.0f}円（100円購入あたり{avg_hit_payout / 100:.1f}倍）")
        print("  フォールド別回収率: " + ", ".join(
            f"{label.split('〜')[0][2:]}={r:.1f}%" if not np.isnan(r) else f"{label}=N/A"
            for label, r in zip(fold_labels, stats["フォールド別回収率"])
        ))


# ------------------------------------------------------------
# ③ワイド: EVしきい値グリッドサーチ
# ------------------------------------------------------------

def run_wide_ev_grid(merged, wide_payouts, n_folds, log=print):
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

        actual_top3 = set(group[group["finish_position"] <= 3]["horse_number"])
        anchor_in_top3 = anchor_num in actual_top3

        for th in EV_GRID:
            partners = others[others["expected_value"] >= th]
            if len(partners) == 0:
                continue
            for _, p in partners.iterrows():
                p_num = p["horse_number"]
                grid_stakes[th][fold_idx] += STAKE
                grid_bets[th][fold_idx] += 1
                hit = anchor_in_top3 and (p_num in actual_top3)
                if hit:
                    grid_hits[th][fold_idx] += 1
                    payout = wide_payouts.get((race_id, umaren_key(anchor_num, p_num)))
                    if payout is not None:
                        grid_payouts[th][fold_idx] += payout / 100 * STAKE

    return grid_stakes, grid_payouts, grid_bets, grid_hits


def check_wide_ev_strategy(merged, wide_payouts, n_folds, fold_labels, log=print):
    grid_stakes, grid_payouts, grid_bets, grid_hits = run_wide_ev_grid(merged, wide_payouts, n_folds)

    print(f"\n{'EVしきい値':>10s}{'総購入点数':>12s}{'総的中数':>10s}{'全体回収率':>12s}{'平均':>9s}{'標準偏差':>9s}{'シャープレシオ':>14s}")
    for th in EV_GRID:
        stats = fold_recovery_stats(grid_stakes[th], grid_payouts[th])
        total_stake, total_payout = sum(grid_stakes[th]), sum(grid_payouts[th])
        overall_recovery = total_payout / total_stake * 100 if total_stake > 0 else float("nan")
        print(f"{th:>10.2f}{sum(grid_bets[th]):>12,}{sum(grid_hits[th]):>10,}{overall_recovery:>11.1f}%"
              f"{stats['平均']:>8.1f}%{stats['標準偏差']:>9.1f}{stats['シャープレシオ']:>14.3f}")

    print("\n--- EV>=1.20のフォールド別内訳（馬連と同水準で比較） ---")
    stats_120 = fold_recovery_stats(grid_stakes[1.20], grid_payouts[1.20])
    for i, (label, recovery, bets, hits) in enumerate(zip(
        fold_labels, stats_120["フォールド別回収率"], grid_bets[1.20], grid_hits[1.20]
    )):
        recovery_str = f"{recovery:6.1f}%" if not np.isnan(recovery) else "  N/A  "
        print(f"  フォールド{i + 1} ({label}): 購入点数={bets:4d} 的中数={hits:3d} 回収率={recovery_str}")
    print(f"  → 平均={stats_120['平均']:.1f}%  標準偏差={stats_120['標準偏差']:.1f}  シャープレシオ={stats_120['シャープレシオ']:.3f}")


def main():
    print("=" * 70)
    print("dsk_Project")
    print("追加検証: ①S基準の日次購入件数分布 ②3連複BOXサイズ拡張 ③ワイドEV戦略")
    print("=" * 70)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    n_folds = len(folds)
    fold_labels = [f"{s}〜{e}" for s, e in folds]
    print(f"データセット件数: {len(dataset)}  フォールド数: {n_folds}")

    oos_df = build_oos_predictions_with_fold(dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED)
    merged = attach_finish_info(oos_df)
    merged = merged.dropna(subset=["field_size"])

    print()
    print("=" * 70)
    print("①S基準（EV>=1.4・オッズ上限30倍・1勝クラス以上）の1日あたり購入件数分布")
    print("=" * 70)
    dataset_race_dates = oos_df["race_date"].unique()
    check_daily_s_rank_volume(merged, dataset_race_dates)

    print()
    print("=" * 70)
    print("②3連複: 上位5〜8頭BOXの比較")
    print("=" * 70)
    sanrenpuku_payouts = load_payout_lookup("3連複")
    check_sanrenpuku_box_sizes(merged, sanrenpuku_payouts, n_folds, fold_labels)

    print()
    print("=" * 70)
    print("③ワイド: 相手をEVしきい値で絞るグリッドサーチ・フォールド別安定性")
    print("=" * 70)
    wide_payouts = load_payout_lookup("ワイド")
    check_wide_ev_strategy(merged, wide_payouts, n_folds, fold_labels)


if __name__ == "__main__":
    main()
