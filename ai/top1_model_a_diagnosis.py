"""
dsk_Project
Stage3: モデルAでEV最大1頭のみ購入が悪化した原因の調査
Version 0.1

ai/class_and_top1_backtest.pyで、モデルA（市場情報あり）は「EV最大1頭のみ購入」
に絞ると回収率が悪化した（EV>=1.50・オッズ上限30倍で-10.51pt）。原因を切り分ける
ため、以下3点を確認する（全てモデルAのウォークフォワードOOS予測、EV>=1.20/1.50・
オッズ上限30倍の2設定で検証）。

  1. 全頭購入 vs EV最大1頭のみ購入で、選ばれる馬の平均オッズ・平均人気が
     どう変わるか（1頭のみ方式が人気馬に偏っていないか）
  2. モデルAの予測勝率(pred_win_prob)と市場のインプライド確率
     （1/oddsをレース内正規化したもの）の相関係数（PROJECT_EVの
     ai/compare_market_odds.pyと同じ計算）。相関が非常に高ければ、
     モデルAの「期待値」は実質的に人気順選抜と大差ないことになる
  3. 1頭のみ方式で「切り捨てられた」馬（EV閾値・オッズ上限は満たすが
     最大EVではない馬）のうち、実際に的中していた馬の数・オッズ・
     払戻額（取りこぼした払戻の合計）
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import (
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS,
    fetch_win_payouts,
    run_model_backtest,
    select_top1_per_race,
)
from ai.build_dataset import build_dataset
from ai.walk_forward_backtest import generate_folds

STAKE = 100
SETTINGS = [("EV>=1.20・オッズ上限30倍", 1.20, 30), ("EV>=1.50・オッズ上限30倍", 1.50, 30)]


def compute_implied_probability(df):
    """レースごとに1/market_oddsを正規化し、市場のインプライド確率を計算する
    （ai/compare_market_odds.pyと同じ計算式）"""
    inv_odds = 1.0 / df["market_odds"]
    race_sum = inv_odds.groupby(df["race_id"]).transform("sum")
    return inv_odds / race_sum


def diagnose(df_a, ev_threshold, odds_cap, log=print):
    candidates = df_a[(df_a["expected_value"] >= ev_threshold) & (df_a["market_odds"] <= odds_cap)]
    full_buys = candidates
    top1_buys = select_top1_per_race(candidates)
    discarded = candidates.drop(top1_buys.index)

    log(f"候補（絞り込み前の全頭購入相当）: {len(full_buys)}件 / "
        f"レース数{full_buys['race_id'].nunique()}")
    log(f"1頭のみ購入後: {len(top1_buys)}件")
    log(f"切り捨てられた馬: {len(discarded)}件")

    # (1) 平均オッズ・平均人気の比較
    log("")
    log("--- (1) 平均オッズ・平均人気: 全頭購入 vs 1頭のみ ---")
    for label, group in [("全頭購入", full_buys), ("1頭のみ", top1_buys), ("切り捨て分", discarded)]:
        if len(group) == 0:
            log(f"  {label}: 該当なし")
            continue
        log(f"  {label}: 平均オッズ={group['market_odds'].mean():.2f}倍  "
            f"平均人気={group['market_popularity'].mean():.2f}位  "
            f"件数={len(group)}  的中率={group['label'].mean() * 100:.2f}%")

    # (2) モデルAの予測勝率と市場インプライド確率の相関
    log("")
    log("--- (2) モデルAの予測勝率 と 市場インプライド確率 の相関（候補馬全体） ---")
    implied = compute_implied_probability(candidates)
    valid = implied.notna() & candidates["pred_win_prob"].notna()
    corr = np.corrcoef(candidates.loc[valid, "pred_win_prob"], implied[valid])[0, 1]
    log(f"  相関係数 = {corr:.4f}（対象{valid.sum()}件）")
    log(f"  （参考）候補馬全体の平均人気: {candidates['market_popularity'].mean():.2f}位、"
        f"1〜3番人気の割合: {(candidates['market_popularity'] <= 3).mean() * 100:.1f}%")

    # (3) 切り捨てられた馬のうち的中していた馬
    log("")
    log("--- (3) 切り捨てられた馬のうち的中していた馬 ---")
    discarded_hits = discarded[discarded["label"] == 1]
    top1_hits = top1_buys[top1_buys["label"] == 1]
    log(f"  1頭のみ方式での的中数: {len(top1_hits)}")
    log(f"  切り捨てた馬の中の的中数: {len(discarded_hits)}"
        f"（全頭購入時の的中数{len(top1_hits) + len(discarded_hits)}件中）")

    if len(discarded_hits) > 0:
        forgone_payout = (discarded_hits["confirmed_payout_yen"].dropna() * (STAKE / 100)).sum()
        forgone_stake = len(discarded_hits) * STAKE
        log(f"  取りこぼした払戻の合計: {forgone_payout:,.0f}円"
            f"（該当馬の購入額換算 {forgone_stake:,}円分の的中を逃した計算）")
        log(f"  取りこぼした的中馬の平均オッズ: {discarded_hits['market_odds'].mean():.2f}倍"
            f"（1頭のみ方式で採用された的中馬の平均オッズ: "
            f"{top1_hits['market_odds'].mean():.2f}倍）")
        log("  取りこぼした的中馬の内訳（race_id, 馬番, オッズ, 人気, 確定払戻/100円）:")
        for _, row in discarded_hits.sort_values("market_odds", ascending=False).head(10).iterrows():
            log(f"    {row['race_id']} 馬番{row['horse_number']} "
                f"オッズ{row['market_odds']:.1f}倍 人気{row['market_popularity']}位 "
                f"払戻{row['confirmed_payout_yen']}円")
    else:
        log("  切り捨てた馬の中に的中馬はいなかった")

    return {
        "full_buys": full_buys, "top1_buys": top1_buys, "discarded": discarded,
        "corr": corr,
    }


def main():
    print("=" * 60)
    print("dsk_Project")
    print("モデルA: EV最大1頭のみ購入が悪化した原因の調査")
    print("=" * 60)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()

    print()
    print("--- モデルA（市場情報あり）のフォールド別学習・予測 ---")
    df_a = run_model_backtest(dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルA")

    for setting_label, ev_threshold, odds_cap in SETTINGS:
        print()
        print("=" * 60)
        print(f"=== {setting_label} ===")
        print("=" * 60)
        diagnose(df_a, ev_threshold, odds_cap, log=print)


if __name__ == "__main__":
    main()
