"""
dsk_Project
Stage3: モデルA/BのEVレース内順位付けによる単勝複数購入・馬連BOX（フィルタなし）
Version 0.1

ai/backtest.pyのEV閾値・オッズ上限フィルタを一切かけず、「レース内で
期待値（expected_value = 予測勝率 × 発走前オッズ）が高い順に何頭買うか」
だけを見る単純なバックテスト。analysis/rank_multi_bet_backtest.py
（featuresテーブルのraw_rank・odds_adjusted_rankを使った単純集計）の
LightGBM版に相当する。

順位付けの基準について: 依頼は「EV（または予測勝率）でレース内順位付け」
だったが、本スクリプトはこれまでのStage3検証全体（ai/backtest.py・
ai/class_and_top1_backtest.py・ai/top1_model_a_diagnosis.py）で一貫して
使ってきたexpected_value（EV）で順位付けする。市場情報を含まないモデルB
でもexpected_value自体はmarket_odds（発走前オッズ）を掛けて計算するため、
モデルBの「EV順位」も市場情報の影響を受ける点に注意（これは
ai/backtest.pyでも同じ扱い）。

学習・OOS予測はai/backtest.py::run_model_backtestをそのまま再利用する
（8フォールド・拡大窓のウォークフォワード。EV閾値・オッズ上限フィルタは
未適用のOOS予測そのものを使う）。

払戻金は確定payoutsテーブルのみを使う（単勝・馬連とも）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, FEATURE_COLUMNS, fetch_win_payouts, run_model_backtest
from ai.build_dataset import build_dataset
from ai.walk_forward_backtest import generate_folds
from analysis.rank_multi_bet_backtest import fetch_actual_top2, fetch_umaren_payouts
from database.db_manager import DatabaseManager

STAKE = 100
WIN_TOP_N = [1, 2, 3]
BOX_SIZES = [3, 5]


def add_ev_rank(df):
    """race_id内でexpected_value降順のev_rank列を追加する
    （同値はDataFrame内の出現順、market_oddsがNaNの行はev_rankもNaNのまま
    残り、後続のtop_nフィルタで自然に除外される）"""
    df = df.copy()
    df["ev_rank"] = df.groupby("race_id")["expected_value"].rank(method="first", ascending=False)
    return df


def simulate_win_multi(df, top_n, stake=STAKE):
    buys = df[df["ev_rank"] <= top_n]

    n_buys = len(buys)
    hits = buys[buys["label"] == 1]
    n_hits = len(hits)

    valid_payout = hits["confirmed_payout_yen"].dropna()
    missing_payout = len(hits) - len(valid_payout)

    total_stake = n_buys * stake
    total_payout = (valid_payout * (stake / 100)).sum()

    hit_rate = n_hits / n_buys * 100 if n_buys else float("nan")
    recovery_rate = total_payout / total_stake * 100 if total_stake else float("nan")

    return {
        "対象": f"予測順位(EV) 上位{top_n}頭",
        "購入点数": n_buys,
        "的中数": n_hits,
        "的中率(%)": hit_rate,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
    }


def simulate_umaren_box(df, box_size, umaren_payouts, actual_top2, stake=STAKE):
    boxes = {}
    for race_id, group in df[df["ev_rank"] <= box_size].groupby("race_id"):
        if len(group) == box_size:
            boxes[race_id] = group["horse_number"].tolist()

    n_pairs = box_size * (box_size - 1) // 2
    n_boxes = 0
    n_hits = 0
    total_payout = 0.0
    missing_payout = 0

    for race_id, box_horses in boxes.items():
        finish = actual_top2.get(race_id)
        if finish is None:
            continue

        n_boxes += 1
        first, second = finish
        hit = first in box_horses and second in box_horses
        if not hit:
            continue

        n_hits += 1
        combination = f"{min(first, second)}-{max(first, second)}"
        payout_yen = umaren_payouts.get((race_id, combination))
        if payout_yen is None:
            missing_payout += 1
            continue
        total_payout += payout_yen * (stake / 100)

    total_stake = n_boxes * n_pairs * stake
    hit_rate = n_hits / n_boxes * 100 if n_boxes else float("nan")
    recovery_rate = total_payout / total_stake * 100 if total_stake else float("nan")

    return {
        "対象": f"予測順位(EV) 上位{box_size}頭BOX（{n_pairs}点）",
        "購入レース数": n_boxes,
        "的中数": n_hits,
        "的中率(%)": hit_rate,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
    }


def print_result(result, keys, log=print):
    log(f"--- {result['対象']} ---")
    for key in keys:
        v = result[key]
        if isinstance(v, float):
            log(f"  {key}: {v:,.2f}")
        else:
            log(f"  {key}: {v:,}")


def main():
    print("=" * 60)
    print("dsk_Project")
    print("Stage3: モデルA/B EVレース内順位付け 単勝複数購入・馬連BOX（フィルタなし）")
    print("=" * 60)

    db = DatabaseManager()
    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts(db)
    umaren_payouts = fetch_umaren_payouts(db)
    actual_top2 = fetch_actual_top2(db)

    print()
    print("--- モデルA（市場情報あり）のフォールド別学習・予測 ---")
    df_a = add_ev_rank(run_model_backtest(dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルA"))

    print()
    print("--- モデルB（市場情報除外）のフォールド別学習・予測 ---")
    df_b = add_ev_rank(run_model_backtest(dataset, folds, BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルB"))

    win_keys = ["購入点数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]
    box_keys = ["購入レース数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]

    all_win_results = {}
    all_box_results = {}

    for model_label, df in [("モデルA", df_a), ("モデルB", df_b)]:
        print()
        print("=" * 60)
        print(f"① {model_label}: 単勝複数購入")
        print("=" * 60)
        for top_n in WIN_TOP_N:
            print()
            result = simulate_win_multi(df, top_n)
            print_result(result, win_keys)
            all_win_results[(model_label, top_n)] = result

        print()
        print("=" * 60)
        print(f"② {model_label}: 馬連BOX")
        print("=" * 60)
        for box_size in BOX_SIZES:
            print()
            result = simulate_umaren_box(df, box_size, umaren_payouts, actual_top2)
            print_result(result, box_keys)
            all_box_results[(model_label, box_size)] = result

    print()
    print("=" * 60)
    print("=== ①比較表: 単勝複数購入（モデルA vs B） ===")
    print("=" * 60)
    print(f"{'モデル':10s}{'対象':24s}{'購入点数':>10s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    for (model_label, top_n), result in all_win_results.items():
        print(f"{model_label:10s}{result['対象']:24s}{result['購入点数']:10d}"
              f"{result['的中率(%)']:12.2f}{result['回収率(%)']:12.2f}")

    print()
    print("=" * 60)
    print("=== ②比較表: 馬連BOX（モデルA vs B） ===")
    print("=" * 60)
    print(f"{'モデル':10s}{'対象':30s}{'購入レース数':>12s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    for (model_label, box_size), result in all_box_results.items():
        print(f"{model_label:10s}{result['対象']:30s}{result['購入レース数']:12d}"
              f"{result['的中率(%)']:12.2f}{result['回収率(%)']:12.2f}")


if __name__ == "__main__":
    main()
