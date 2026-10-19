"""
dsk_Project
◎(軸)固定・相手選び戦略の検証（馬連・ワイド・3連複）
Version 0.1

これまでのEV/pred_win_prob単一指標での判断とは違う切り口: ◎（pred_win_prob
1位、軸として信頼性が高い馬）を固定し、「相手（2着・3着相手）」の選び方を
変えたときに馬連・ワイド・3連複の回収率がどう変わるかを検証する。

ウォークフォワード（8フォールド）のOOS予測（リーケージなし）を使う。

① 馬連・ワイド: ◎から見た相手選びを3パターンで比較する
   (a) 全流し: ◎以外の全馬を相手にする
   (b) EV基準: 相手候補自身のexpected_value(pred_win_prob×オッズ)が
       しきい値以上の馬だけを相手にする
   (c) オッズレンジ基準（中穴〜大穴）: 相手候補のオッズが一定以上（穴馬）の
       馬だけを相手にする

② 3連複: ◎+相手2頭の選び方を3パターンで比較する
   (a) 全流し（◎+相手2頭を全流し。全組み合わせのうち実際に的中する組み合わせは
       1レースにつき高々1通りなので、計算上は「◎が実際に3着内に入った
       レースで、その回の確定払戻金をそのまま使う」のと同じ）
   (b) 上位5頭BOX（◎+pred_win_prob2〜5位の4頭からペアを選ぶ、印5頭BOX相当。
       6点買い）
   (c) EV上位2頭（◎以外でexpected_valueが最も高い2頭だけを相手に固定。1点買い）

③ 仮説検証: 「◎と、配当を稼げる相手（穴馬）が絡んだときに回収率が上がる」か。
   ◎が実際に馬連的中に絡んだレース（◎が実際の1-2着以内）を、相手が穴馬
   （オッズ>=ANA_ODDS_THRESHOLD）だったケースとそうでないケースに分け、
   頻度・平均確定払戻金を比較する。
"""

import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.build_dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.train_model import evaluate_model, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager

STAKE = 100
EV_THRESHOLDS = [1.0, 1.2]
ANA_ODDS_THRESHOLD = 10  # 中穴〜大穴の下限（この倍数以上を「穴馬」とみなす）
TOP_BOX_SIZE = 5  # 印5頭BOX相当（◎含めて5頭）


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


class Ledger:
    """買い目1件ごとにstake/payoutを積み上げる集計器"""

    def __init__(self):
        self.n_bets = 0
        self.n_races = 0
        self.n_hits = 0
        self.total_stake = 0
        self.total_payout = 0.0
        self.hit_payouts = []

    def add_bet(self, hit, payout_yen=None, stake=STAKE):
        self.n_bets += 1
        self.total_stake += stake
        if hit:
            self.n_hits += 1
            if payout_yen is not None:
                self.total_payout += payout_yen / 100 * stake
                self.hit_payouts.append(payout_yen)

    def summary(self):
        recovery = self.total_payout / self.total_stake * 100 if self.total_stake > 0 else float("nan")
        hit_rate = self.n_hits / self.n_bets * 100 if self.n_bets > 0 else float("nan")
        avg_hit_payout = float(np.mean(self.hit_payouts)) if self.hit_payouts else float("nan")
        return {
            "対象レース数": self.n_races,
            "購入点数": self.n_bets,
            "的中数": self.n_hits,
            "的中率(%)": hit_rate,
            "総購入額(円)": self.total_stake,
            "総払戻額(円)": self.total_payout,
            "回収率(%)": recovery,
            "的中1点あたり平均払戻(円)": avg_hit_payout,
        }


def run_umaren_wide_strategies(merged, umaren_payouts, wide_payouts, log=print):
    results = {
        "馬連_全流し": Ledger(), "ワイド_全流し": Ledger(),
    }
    for th in EV_THRESHOLDS:
        results[f"馬連_EV{th}以上"] = Ledger()
        results[f"ワイド_EV{th}以上"] = Ledger()
    results[f"馬連_オッズ{ANA_ODDS_THRESHOLD}倍以上"] = Ledger()
    results[f"ワイド_オッズ{ANA_ODDS_THRESHOLD}倍以上"] = Ledger()

    for race_id, group in merged.groupby("race_id"):
        if group["finish_position"].isna().any():
            continue
        anchor_row = group.loc[group["pred_win_prob"].idxmax()]
        anchor_num = anchor_row["horse_number"]
        others = group[group["horse_number"] != anchor_num].copy()
        if len(others) == 0:
            continue
        others["expected_value"] = others["pred_win_prob"] * others["market_odds"]

        actual_top2 = set(group[group["finish_position"] <= 2]["horse_number"])
        actual_top3 = set(group[group["finish_position"] <= 3]["horse_number"])
        anchor_in_top2 = anchor_num in actual_top2
        anchor_in_top3 = anchor_num in actual_top3
        actual_umaren_partner = (actual_top2 - {anchor_num}) if anchor_in_top2 else set()

        strategies = {
            "全流し": others,
        }
        for th in EV_THRESHOLDS:
            strategies[f"EV{th}以上"] = others[others["expected_value"] >= th]
        strategies[f"オッズ{ANA_ODDS_THRESHOLD}倍以上"] = others[others["market_odds"] >= ANA_ODDS_THRESHOLD]

        for strat_label, partners in strategies.items():
            umaren_ledger = results[f"馬連_{strat_label}"]
            wide_ledger = results[f"ワイド_{strat_label}"]
            if len(partners) == 0:
                continue
            umaren_ledger.n_races += 1
            wide_ledger.n_races += 1
            for _, p in partners.iterrows():
                p_num = p["horse_number"]

                umaren_hit = anchor_in_top2 and (p_num in actual_umaren_partner)
                umaren_payout = umaren_payouts.get((race_id, umaren_key(anchor_num, p_num))) if umaren_hit else None
                umaren_ledger.add_bet(umaren_hit, umaren_payout)

                wide_hit = anchor_in_top3 and (p_num in actual_top3)
                wide_payout = wide_payouts.get((race_id, umaren_key(anchor_num, p_num))) if wide_hit else None
                wide_ledger.add_bet(wide_hit, wide_payout)

    return results


def run_sanrenpuku_strategies(merged, sanrenpuku_payouts, log=print):
    results = {"全流し": Ledger(), f"上位{TOP_BOX_SIZE}頭BOX": Ledger(), "EV上位2頭": Ledger()}

    for race_id, group in merged.groupby("race_id"):
        if group["finish_position"].isna().any():
            continue
        if len(group) < 3:
            continue

        ranked = group.sort_values("pred_win_prob", ascending=False).reset_index(drop=True)
        anchor_num = ranked.loc[0, "horse_number"]
        others = ranked.iloc[1:].copy()
        others["expected_value"] = others["pred_win_prob"] * others["market_odds"]

        actual_top3 = group[group["finish_position"] <= 3]["horse_number"].tolist()
        anchor_in_top3 = anchor_num in actual_top3
        actual_other_two = frozenset(set(actual_top3) - {anchor_num}) if anchor_in_top3 and len(actual_top3) == 3 else None

        # --- 全流し: field_size-1頭から2頭を選ぶ全組み合わせ。
        #     的中は「実際の他2頭の組み合わせ」がその中に必ず1通りだけ含まれるので、
        #     ◎が3着内なら払戻1件・◎が3着内でなければ0件として計算できる ---
        n_others = len(others)
        n_combos = n_others * (n_others - 1) // 2 if n_others >= 2 else 0
        if n_combos > 0:
            ledger = results["全流し"]
            ledger.n_races += 1
            ledger.total_stake += n_combos * STAKE
            ledger.n_bets += n_combos
            if anchor_in_top3 and actual_other_two is not None and actual_other_two.issubset(set(others["horse_number"])):
                payout = sanrenpuku_payouts.get((race_id, sanrenpuku_key(anchor_num, *actual_other_two)))
                ledger.n_hits += 1
                if payout is not None:
                    ledger.total_payout += payout / 100 * STAKE
                    ledger.hit_payouts.append(payout)

        # --- 上位5頭BOX: ◎+pred_win_prob2〜5位の4頭からペアを選ぶ6通り ---
        box_others = others.iloc[: TOP_BOX_SIZE - 1]
        if len(box_others) >= 2:
            ledger = results[f"上位{TOP_BOX_SIZE}頭BOX"]
            ledger.n_races += 1
            box_nums = box_others["horse_number"].tolist()
            for a, b in combinations(box_nums, 2):
                hit = anchor_in_top3 and actual_other_two == frozenset({a, b})
                payout = sanrenpuku_payouts.get((race_id, sanrenpuku_key(anchor_num, a, b))) if hit else None
                ledger.add_bet(hit, payout)

        # --- EV上位2頭: ◎以外でexpected_valueが最も高い2頭を相手に固定（1点買い） ---
        if len(others) >= 2:
            top2_ev = others.sort_values("expected_value", ascending=False).iloc[:2]
            a, b = top2_ev["horse_number"].tolist()
            ledger = results["EV上位2頭"]
            ledger.n_races += 1
            hit = anchor_in_top3 and actual_other_two == frozenset({a, b})
            payout = sanrenpuku_payouts.get((race_id, sanrenpuku_key(anchor_num, a, b))) if hit else None
            ledger.add_bet(hit, payout)

    return results


def run_hypothesis_check(merged, umaren_payouts, log=print):
    """◎が実際に馬連的中に絡んだレースで、相手が穴馬だったか否かで
    頻度・平均払戻金を比較する"""
    ana_group = {"n": 0, "payouts": []}
    favorite_group = {"n": 0, "payouts": []}

    for race_id, group in merged.groupby("race_id"):
        if group["finish_position"].isna().any():
            continue
        anchor_row = group.loc[group["pred_win_prob"].idxmax()]
        anchor_num = anchor_row["horse_number"]

        actual_top2 = group[group["finish_position"] <= 2]
        if anchor_num not in set(actual_top2["horse_number"]):
            continue

        partner_row = actual_top2[actual_top2["horse_number"] != anchor_num]
        if len(partner_row) != 1:
            continue
        partner_num = partner_row.iloc[0]["horse_number"]
        partner_odds = partner_row.iloc[0]["market_odds"]

        payout = umaren_payouts.get((race_id, umaren_key(anchor_num, partner_num)))
        target = ana_group if partner_odds >= ANA_ODDS_THRESHOLD else favorite_group
        target["n"] += 1
        if payout is not None:
            target["payouts"].append(payout)

    return ana_group, favorite_group


def main():
    print("=" * 70)
    print("dsk_Project")
    print("◎(軸)固定・相手選び戦略の検証（馬連・ワイド・3連複）")
    print("=" * 70)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    oos_df = build_oos_predictions(dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED)
    merged = attach_finish_info(oos_df)
    merged = merged.dropna(subset=["field_size"])
    print(f"複勝判定可能なOOS予測件数: {len(merged)}  対象レース数: {merged['race_id'].nunique()}")

    umaren_payouts = load_payout_lookup("馬連")
    wide_payouts = load_payout_lookup("ワイド")
    sanrenpuku_payouts = load_payout_lookup("3連複")

    print()
    print("=" * 70)
    print("①馬連・ワイド: 相手選び戦略の比較")
    print("=" * 70)
    uw_results = run_umaren_wide_strategies(merged, umaren_payouts, wide_payouts)
    for label, ledger in uw_results.items():
        s = ledger.summary()
        print(f"[{label}] 対象レース={s['対象レース数']:5,} 購入点数={s['購入点数']:6,} "
              f"的中数={s['的中数']:5,} 的中率={s['的中率(%)']:5.2f}% "
              f"回収率={s['回収率(%)']:6.1f}% 的中時平均払戻={s['的中1点あたり平均払戻(円)']:7.0f}円")

    print()
    print("=" * 70)
    print("②3連複: ◎からの流し方の比較")
    print("=" * 70)
    sanren_results = run_sanrenpuku_strategies(merged, sanrenpuku_payouts)
    for label, ledger in sanren_results.items():
        s = ledger.summary()
        print(f"[{label}] 対象レース={s['対象レース数']:5,} 購入点数={s['購入点数']:6,} "
              f"的中数={s['的中数']:5,} 的中率={s['的中率(%)']:5.2f}% "
              f"回収率={s['回収率(%)']:6.1f}% 的中時平均払戻={s['的中1点あたり平均払戻(円)']:7.0f}円")

    print()
    print("=" * 70)
    print(f"③仮説検証: ◎的中時、相手が穴馬(オッズ{ANA_ODDS_THRESHOLD}倍以上)かどうかで頻度・払戻を比較")
    print("=" * 70)
    ana_group, favorite_group = run_hypothesis_check(merged, umaren_payouts)
    total_n = ana_group["n"] + favorite_group["n"]
    for label, g in [("穴馬が相手（オッズ" + str(ANA_ODDS_THRESHOLD) + "倍以上）", ana_group),
                      ("人気馬が相手（オッズ" + str(ANA_ODDS_THRESHOLD) + "倍未満）", favorite_group)]:
        avg_payout = float(np.mean(g["payouts"])) if g["payouts"] else float("nan")
        pct = g["n"] / total_n * 100 if total_n else float("nan")
        print(f"  {label}: {g['n']:4,}件 ({pct:5.1f}%)  平均馬連払戻={avg_payout:7.0f}円 "
              f"(100円あたり{avg_payout/100 if not np.isnan(avg_payout) else float('nan'):.1f}倍)")


if __name__ == "__main__":
    main()
