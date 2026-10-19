"""
dsk_Project
pred_win_prob>=0.30で絞り込んだ馬の複勝回収率・オッズ/人気の分布
Version 0.1

ai/place_hit_rate_by_confidence.pyで、pred_win_prob>=0.30に絞ると複勝的中率が
71.52%まで上がることを確認したが、的中率が高くても人気馬（低オッズ）に
偏っていれば複勝回収率自体は低い可能性がある。ここでは同じ絞り込み条件で、
①確定payoutsベースの複勝回収率、②平均オッズ・平均人気を確認する。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.build_dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.train_model import evaluate_model, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager

PROB_THRESHOLDS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
STAKE = 100


def build_oos_predictions(dataset, folds, feature_columns, log=print):
    all_results = []
    for test_start, test_end in folds:
        train_df, test_df = split_by_date(dataset, test_start, test_end)
        if len(train_df) == 0 or len(test_df) == 0:
            continue
        model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
        proba, y_test, auc, logloss = evaluate_model(model, test_df, feature_columns)
        log(f"  {test_start}〜{test_end}: テスト={len(test_df)} AUC(単勝)={auc:.4f}")

        result = test_df.loc[:, ["race_id", "horse_number", "market_odds", "market_popularity"]].copy()
        result["pred_win_prob"] = proba
        all_results.append(result)

    return pd.concat(all_results, ignore_index=True)


def attach_place_info(oos_df):
    """finish_position・field_size・確定複勝payout_yenを付与する"""
    db = DatabaseManager()
    rows = db.fetchall("""
        SELECT e.race_id, e.horse_number, res.finish_position,
               (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = e.race_id) AS field_size,
               (SELECT payout_yen FROM payouts pay
                WHERE pay.race_id = e.race_id AND pay.bet_type = '複勝'
                  AND pay.combination = CAST(e.horse_number AS TEXT)) AS place_payout_yen
        FROM entries e
        JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
        WHERE res.finish_position IS NOT NULL
    """)
    finish_df = pd.DataFrame(
        rows, columns=["race_id", "horse_number", "finish_position", "field_size", "place_payout_yen"]
    )

    merged = oos_df.merge(finish_df, on=["race_id", "horse_number"], how="left")
    merged["place_threshold"] = merged["field_size"].apply(lambda n: 3 if n >= 8 else 2)
    merged["is_place_hit"] = merged["finish_position"] <= merged["place_threshold"]
    return merged


def summarize(picks, stake=STAKE):
    n = len(picks)
    if n == 0:
        return None

    hits = picks[picks["is_place_hit"]]
    n_hits = len(hits)
    hit_rate = n_hits / n * 100

    valid_payout = hits["place_payout_yen"].dropna()
    missing_payout = n_hits - len(valid_payout)
    total_stake = n * stake
    total_payout = (valid_payout * (stake / 100)).sum()
    recovery_rate = total_payout / total_stake * 100 if total_stake > 0 else float("nan")

    return {
        "件数": n,
        "複勝的中率(%)": hit_rate,
        "複勝回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
        "平均オッズ": picks["market_odds"].mean(),
        "中央値オッズ": picks["market_odds"].median(),
        "平均人気": picks["market_popularity"].mean(),
    }


def main():
    print("=" * 70)
    print("dsk_Project")
    print("pred_win_prob絞り込みによる複勝回収率・オッズ/人気の分布")
    print("=" * 70)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    oos_df = build_oos_predictions(dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED)
    merged = attach_place_info(oos_df)
    merged = merged.dropna(subset=["finish_position", "field_size"])
    print(f"\n複勝判定可能なOOS予測件数: {len(merged)}")

    baseline = summarize(merged)
    print(f"\nベースライン（絞り込みなし、全馬）:")
    print(f"  件数={baseline['件数']:,}  複勝的中率={baseline['複勝的中率(%)']:.2f}%  "
          f"複勝回収率={baseline['複勝回収率(%)']:.1f}%  平均オッズ={baseline['平均オッズ']:.1f}倍  "
          f"平均人気={baseline['平均人気']:.2f}位")

    print()
    print("--- pred_win_prob>=X で絞り込んだ場合 ---")
    print(f"{'しきい値':>10s}{'件数':>8s}{'複勝的中率':>12s}{'複勝回収率':>12s}{'払戻欠損':>8s}{'平均オッズ':>10s}{'中央値オッズ':>12s}{'平均人気':>9s}")
    for th in PROB_THRESHOLDS:
        picks = merged[merged["pred_win_prob"] >= th]
        s = summarize(picks)
        if s is None:
            print(f"{th:>10.2f}{'0件':>8s}")
            continue
        print(f"{th:>10.2f}{s['件数']:>8,}{s['複勝的中率(%)']:>11.2f}%{s['複勝回収率(%)']:>11.1f}%"
              f"{s['払戻欠損']:>8d}{s['平均オッズ']:>9.1f}倍{s['中央値オッズ']:>11.1f}倍{s['平均人気']:>8.2f}位")

    print()
    print("--- 参考: pred_win_prob>=0.30の内訳を人気別に分解 ---")
    target = merged[merged["pred_win_prob"] >= 0.30]
    pop_bins = [(1, 1, "1番人気"), (2, 2, "2番人気"), (3, 3, "3番人気"), (4, 99, "4番人気以下")]
    for lo, hi, label in pop_bins:
        sub = target[(target["market_popularity"] >= lo) & (target["market_popularity"] <= hi)]
        s = summarize(sub)
        if s is None:
            print(f"  {label}: 0件")
            continue
        print(f"  {label}: 件数={s['件数']:5,} ({s['件数']/len(target)*100:5.1f}%)  "
              f"複勝的中率={s['複勝的中率(%)']:6.2f}%  複勝回収率={s['複勝回収率(%)']:6.1f}%")


if __name__ == "__main__":
    main()
