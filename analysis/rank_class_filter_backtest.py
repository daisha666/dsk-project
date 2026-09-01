"""
dsk_Project
単純集計③: 1勝クラス以上限定での単勝複数購入・馬連BOX（LightGBM不使用）
Version 0.1

analysis/rank_multi_bet_backtest.py（①単勝複数購入 ②馬連BOX）を、新馬戦・
未勝利戦を除外した「1勝クラス以上」のレースに限定して再集計し、クラス限定
あり／なしの回収率を比較する。クラス判定はai/backtest.py::is_class_included
（PROJECT_EVのanalysis/similar_races.py::classify_class_tierと同じロジック）
をそのまま使う。モデル学習は一切行わない。

購入対象の絞り込み（class_filter）は「そのレースを購入対象にするかどうか」の
運用判断であり、raw_rank・odds_adjusted_rankの算出方法自体は変えない
（ai/class_filter_backtest.pyでのPROJECT_EVの扱いと同じ）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import is_class_included
from analysis.rank_multi_bet_backtest import fetch_actual_top2, fetch_umaren_payouts, fetch_win_payouts
from database.db_manager import DatabaseManager

STAKE = 100


# ------------------------------------------------------------
# ① 単勝複数購入（クラスフィルタ対応版）
# ------------------------------------------------------------

def fetch_rank_le_n_horses(rank_column, top_n, db, class_filter=False):
    rows = db.fetchall(f"""
        SELECT f.race_id, e.horse_number, res.finish_position, r.race_class
        FROM features f
        JOIN entries e ON e.race_id = f.race_id AND e.horse_id = f.horse_id
        JOIN races r ON r.race_id = f.race_id
        LEFT JOIN results res ON res.race_id = f.race_id AND res.horse_id = f.horse_id
        WHERE f.{rank_column} <= ? AND f.{rank_column} IS NOT NULL
    """, (top_n,))

    if class_filter:
        rows = [row for row in rows if is_class_included(row[3])]

    return [(race_id, horse_number, finish_position) for race_id, horse_number, finish_position, _ in rows]


def simulate_win_multi(rank_column, top_n, win_payouts, db, class_filter=False, stake=STAKE):
    rows = fetch_rank_le_n_horses(rank_column, top_n, db, class_filter=class_filter)

    n_buys = len(rows)
    n_hits = 0
    total_payout = 0.0
    missing_payout = 0

    for race_id, horse_number, finish_position in rows:
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

    label = f"{rank_column} 上位{top_n}頭" + ("（1勝クラス以上）" if class_filter else "（全クラス）")
    return {
        "対象": label,
        "購入点数": n_buys,
        "的中数": n_hits,
        "的中率(%)": hit_rate,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
    }


# ------------------------------------------------------------
# ② 馬連BOX（クラスフィルタ対応版）
# ------------------------------------------------------------

def fetch_races_with_rank_horses(rank_column, box_size, db, class_filter=False):
    rows = db.fetchall(f"""
        SELECT f.race_id, e.horse_number, f.{rank_column} AS rk, r.race_class
        FROM features f
        JOIN entries e ON e.race_id = f.race_id AND e.horse_id = f.horse_id
        JOIN races r ON r.race_id = f.race_id
        WHERE f.{rank_column} <= ?
        ORDER BY f.race_id, rk
    """, (box_size,))

    races = {}
    race_classes = {}
    for race_id, horse_number, rk, race_class in rows:
        races.setdefault(race_id, []).append(horse_number)
        race_classes[race_id] = race_class

    full_boxes = {race_id: horses for race_id, horses in races.items() if len(horses) == box_size}

    if class_filter:
        full_boxes = {
            race_id: horses for race_id, horses in full_boxes.items()
            if is_class_included(race_classes[race_id])
        }

    return full_boxes


def simulate_umaren_box(box_size, umaren_payouts, db, class_filter=False, stake=STAKE):
    races = fetch_races_with_rank_horses("odds_adjusted_rank", box_size, db, class_filter=class_filter)
    actual_top2 = fetch_actual_top2(db)

    n_pairs = box_size * (box_size - 1) // 2
    n_boxes = 0
    n_hits = 0
    total_payout = 0.0
    missing_payout = 0

    for race_id, box_horses in races.items():
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

    label = f"odds_adjusted_rank 上位{box_size}頭BOX（{n_pairs}点）" + \
            ("（1勝クラス以上）" if class_filter else "（全クラス）")
    return {
        "対象": label,
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
    print("単純集計③: 1勝クラス以上限定 vs 全クラス（単勝複数購入・馬連BOX）")
    print("=" * 60)

    db = DatabaseManager()
    win_payouts = fetch_win_payouts(db)
    umaren_payouts = fetch_umaren_payouts(db)

    win_keys = ["購入点数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]
    box_keys = ["購入レース数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]

    print()
    print("=" * 60)
    print("① 単勝複数購入: raw_rank・odds_adjusted_rank × 上位1/2/3頭 × クラス限定あり/なし")
    print("=" * 60)

    win_results = {}
    for rank_column in ["raw_rank", "odds_adjusted_rank"]:
        for top_n in [1, 2, 3]:
            for class_filter in [False, True]:
                print()
                result = simulate_win_multi(rank_column, top_n, win_payouts, db, class_filter=class_filter)
                print_result(result, win_keys)
                win_results[(rank_column, top_n, class_filter)] = result

    print()
    print("=" * 60)
    print("② 馬連BOX: odds_adjusted_rank 上位3/5頭 × クラス限定あり/なし")
    print("=" * 60)

    box_results = {}
    for box_size in [3, 5]:
        for class_filter in [False, True]:
            print()
            result = simulate_umaren_box(box_size, umaren_payouts, db, class_filter=class_filter)
            print_result(result, box_keys)
            box_results[(box_size, class_filter)] = result

    print()
    print("=" * 60)
    print("=== 主要3設定: クラス限定あり/なし比較（依頼された比較） ===")
    print("=" * 60)

    headline = [
        ("odds_adjusted_rank 上位1頭（単勝）", win_results[("odds_adjusted_rank", 1, False)],
         win_results[("odds_adjusted_rank", 1, True)]),
        ("odds_adjusted_rank 上位2頭（単勝）", win_results[("odds_adjusted_rank", 2, False)],
         win_results[("odds_adjusted_rank", 2, True)]),
        ("odds_adjusted_rank 上位3頭BOX（馬連）", box_results[(3, False)], box_results[(3, True)]),
    ]

    print(f"{'対象':32s}{'全クラス回収率(%)':>18s}{'1勝以上回収率(%)':>18s}{'差分(pt)':>12s}")
    for label, full, filtered in headline:
        diff = filtered["回収率(%)"] - full["回収率(%)"]
        print(f"{label:32s}{full['回収率(%)']:18.2f}{filtered['回収率(%)']:18.2f}{diff:12.2f}")

    print()
    print("=== ①全設定 比較表 ===")
    print(f"{'対象':40s}{'購入点数':>10s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    for result in win_results.values():
        print(f"{result['対象']:40s}{result['購入点数']:10d}"
              f"{result['的中率(%)']:12.2f}{result['回収率(%)']:12.2f}")

    print()
    print("=== ②全設定 比較表 ===")
    print(f"{'対象':46s}{'購入レース数':>12s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    for result in box_results.values():
        print(f"{result['対象']:46s}{result['購入レース数']:12d}"
              f"{result['的中率(%)']:12.2f}{result['回収率(%)']:12.2f}")


if __name__ == "__main__":
    main()
