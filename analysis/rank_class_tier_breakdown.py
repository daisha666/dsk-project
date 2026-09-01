"""
dsk_Project
単純集計④: odds_adjusted_rank上位1頭（単勝）を新馬戦/未勝利戦/1勝クラス以上で分割
Version 0.1

analysis/rank_class_filter_backtest.pyで「1勝クラス以上限定」がodds_adjusted_rank
ベースの回収率を悪化させることが分かったが、これは新馬戦・未勝利戦をまとめて
「1勝クラス未満」として除外した場合の話だった。新馬戦と未勝利戦は市場の
成熟度が異なる可能性がある（新馬戦は出走馬全頭が初出走で近走成績が一切無いが、
未勝利戦は過去走データがある馬が混じる）ため、両者を分けて再集計する。

ai/backtest.py::classify_class_tierをそのまま使い、"新馬"・"未勝利"・
それ以外（1勝クラス以上・オープン・重賞等、is_class_included=True相当）の
3グループで、odds_adjusted_rank=1位の馬の単勝成績を比較する。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import classify_class_tier, is_class_included
from analysis.rank_multi_bet_backtest import fetch_win_payouts
from database.db_manager import DatabaseManager

STAKE = 100

GROUPS = [
    ("新馬戦のみ", lambda tier: tier == "新馬"),
    ("未勝利戦のみ", lambda tier: tier == "未勝利"),
    ("1勝クラス以上のみ", lambda tier: tier not in ("新馬", "未勝利")),
]


def fetch_rank1_horses_with_class(db):
    """odds_adjusted_rank=1位の馬を (race_id, horse_number, finish_position, race_class) で返す"""
    return db.fetchall("""
        SELECT f.race_id, e.horse_number, res.finish_position, r.race_class
        FROM features f
        JOIN entries e ON e.race_id = f.race_id AND e.horse_id = f.horse_id
        JOIN races r ON r.race_id = f.race_id
        LEFT JOIN results res ON res.race_id = f.race_id AND res.horse_id = f.horse_id
        WHERE f.odds_adjusted_rank = 1
    """)


def simulate_group(label, tier_pred, rows, win_payouts, stake=STAKE):
    filtered = [
        (race_id, horse_number, finish_position)
        for race_id, horse_number, finish_position, race_class in rows
        if tier_pred(classify_class_tier(race_class))
    ]

    n_buys = len(filtered)
    n_hits = 0
    total_payout = 0.0
    missing_payout = 0

    for race_id, horse_number, finish_position in filtered:
        if finish_position != 1:
            continue
        n_hits += 1
        payout_yen = win_payouts.get((race_id, horse_number))
        if payout_yen is None:
            missing_payout += 1
            continue
        total_payout += payout_yen * (stake / 100)

    total_stake = n_buys * stake
    hit_rate = n_hits / n_buys * 100 if n_buys else float("nan")
    recovery_rate = total_payout / total_stake * 100 if total_stake else float("nan")

    return {
        "グループ": label,
        "購入点数": n_buys,
        "的中数": n_hits,
        "的中率(%)": hit_rate,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
    }


def main():
    print("=" * 60)
    print("dsk_Project")
    print("単純集計④: odds_adjusted_rank上位1頭（単勝）× 新馬/未勝利/1勝クラス以上")
    print("=" * 60)

    db = DatabaseManager()
    win_payouts = fetch_win_payouts(db)
    rows = fetch_rank1_horses_with_class(db)

    print()
    print(f"odds_adjusted_rank=1位の対象件数（全体）: {len(rows)}")

    # 内訳確認（クラス帯ごとの件数）
    tier_counts = {}
    for _, _, _, race_class in rows:
        tier = classify_class_tier(race_class)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    print("クラス帯別の内訳:")
    for tier, n in sorted(tier_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {tier}: {n}")

    print()
    results = []
    for label, tier_pred in GROUPS:
        result = simulate_group(label, tier_pred, rows, win_payouts)
        results.append(result)

        print(f"--- {label} ---")
        for key in ["購入点数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]:
            v = result[key]
            if isinstance(v, float):
                print(f"  {key}: {v:,.2f}")
            else:
                print(f"  {key}: {v:,}")
        print()

    print("=" * 60)
    print("=== 比較表 ===")
    print("=" * 60)
    print(f"{'グループ':20s}{'購入点数':>10s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    for result in results:
        print(f"{result['グループ']:20s}{result['購入点数']:10d}"
              f"{result['的中率(%)']:12.2f}{result['回収率(%)']:12.2f}")


if __name__ == "__main__":
    main()
