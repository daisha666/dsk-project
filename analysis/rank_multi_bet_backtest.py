"""
dsk_Project
単純集計②: 単勝複数購入・馬連BOX（LightGBM不使用、既存スコア列＋確定payoutsのみ）
Version 0.1

analysis/rank_baseline_backtest.py（raw_rank=1位 / odds_adjusted_rank=1位の
単勝1点買い）の延長で、以下2種類を追加集計する。モデル学習は一切行わず、
featuresテーブルの既存順位列（raw_rank・odds_adjusted_rank）と確定payouts
テーブルのみを使う。

① 単勝複数購入: raw_rank・odds_adjusted_rankそれぞれについて、
   上位1頭／2頭／3頭（各100円ずつ）を単勝購入した場合の的中率・回収率。
   的中率・回収率は「購入した馬券（チケット）単位」で集計する
   （analysis/rank_baseline_backtest.pyやai/backtest.pyと同じ集計単位）。

② 馬連BOX: odds_adjusted_rankの上位3頭・上位5頭でBOX（全組み合わせ）を
   購入した場合の的中率・回収率。的中判定は実際の1着・2着が両方とも
   BOX構成馬に含まれているかどうか（PROJECT_EVのai/umaren_box_backtest.pyと
   同じ判定ロジック）。

  ★払戻金の計算方針: PROJECT_EVのumaren_box_backtest.pyはcombination_odds
  （発走前オッズのスナップショット）を使っていたが、dsk_Projectでは単勝の
  場合と同じ理由で確定payoutsテーブル（bet_type='馬連'、combination=
  "小さい方馬番-大きい方馬番"）を必ず使う。BOX構成馬の絞り込み自体は
  odds_adjusted_rank（＝発走前オッズ由来の情報）を使うが、的中時の払戻額は
  確定値のみを使う。

  BOXサイズに満たない頭数のレース（例: 5頭BOXで出走4頭以下のレース）は
  そもそもBOXを組めないため集計から除外する。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager

STAKE = 100


def fetch_win_payouts(db):
    """{(race_id, horse_number): payout_yen} を返す（単勝、100円あたり）"""
    rows = db.fetchall("SELECT race_id, combination, payout_yen FROM payouts WHERE bet_type = '単勝'")
    payouts = {}
    for race_id, combination, payout_yen in rows:
        try:
            horse_number = int(combination)
        except (TypeError, ValueError):
            continue
        payouts.setdefault((race_id, horse_number), payout_yen)
    return payouts


def fetch_umaren_payouts(db):
    """{(race_id, "小-大"): payout_yen} を返す（馬連、100円あたり）"""
    rows = db.fetchall("SELECT race_id, combination, payout_yen FROM payouts WHERE bet_type = '馬連'")
    payouts = {}
    for race_id, combination, payout_yen in rows:
        payouts.setdefault((race_id, combination), payout_yen)
    return payouts


# ------------------------------------------------------------
# ① 単勝複数購入
# ------------------------------------------------------------

def fetch_rank_le_n_horses(rank_column, top_n, db):
    """rank_columnがtop_n以下の馬を (race_id, horse_number, finish_position) で返す"""
    rows = db.fetchall(f"""
        SELECT f.race_id, e.horse_number, res.finish_position
        FROM features f
        JOIN entries e ON e.race_id = f.race_id AND e.horse_id = f.horse_id
        LEFT JOIN results res ON res.race_id = f.race_id AND res.horse_id = f.horse_id
        WHERE f.{rank_column} <= ? AND f.{rank_column} IS NOT NULL
    """, (top_n,))
    return rows


def simulate_win_multi(rank_column, top_n, win_payouts, db, stake=STAKE):
    rows = fetch_rank_le_n_horses(rank_column, top_n, db)

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

    return {
        "対象": f"{rank_column} 上位{top_n}頭",
        "購入点数": n_buys,
        "的中数": n_hits,
        "的中率(%)": hit_rate,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
    }


# ------------------------------------------------------------
# ② 馬連BOX
# ------------------------------------------------------------

def fetch_races_with_rank_horses(rank_column, box_size, db):
    """rank_columnが1〜box_sizeの馬を持つレースを
    {race_id: [horse_number, ...]}（box_size頭ぴったり揃っているレースのみ）で返す"""
    rows = db.fetchall(f"""
        SELECT f.race_id, e.horse_number, f.{rank_column} AS rk
        FROM features f
        JOIN entries e ON e.race_id = f.race_id AND e.horse_id = f.horse_id
        WHERE f.{rank_column} <= ?
        ORDER BY f.race_id, rk
    """, (box_size,))

    races = {}
    for race_id, horse_number, rk in rows:
        races.setdefault(race_id, []).append(horse_number)

    return {race_id: horses for race_id, horses in races.items() if len(horses) == box_size}


def fetch_actual_top2(db):
    """{race_id: (1着馬番, 2着馬番)}（同着等で1着・2着それぞれ複数いる場合は
    最初の1件のみを使う。単純集計のため厳密な同着処理はしない）"""
    rows = db.fetchall("""
        SELECT r.race_id, e.horse_number, res.finish_position
        FROM results res
        JOIN entries e ON e.race_id = res.race_id AND e.horse_id = res.horse_id
        JOIN races r ON r.race_id = res.race_id
        WHERE res.finish_position IN (1, 2)
    """)

    top2 = {}
    for race_id, horse_number, finish_position in rows:
        entry = top2.setdefault(race_id, {})
        entry.setdefault(finish_position, horse_number)

    return {
        race_id: (positions[1], positions[2])
        for race_id, positions in top2.items()
        if 1 in positions and 2 in positions
    }


def simulate_umaren_box(box_size, umaren_payouts, db, stake=STAKE):
    races = fetch_races_with_rank_horses("odds_adjusted_rank", box_size, db)
    actual_top2 = fetch_actual_top2(db)

    n_pairs = box_size * (box_size - 1) // 2
    n_boxes = 0
    n_hits = 0
    total_payout = 0.0
    missing_payout = 0

    for race_id, box_horses in races.items():
        finish = actual_top2.get(race_id)
        if finish is None:
            continue  # 結果未確定・データ欠損のレースは対象外

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
        "対象": f"odds_adjusted_rank 上位{box_size}頭BOX（{n_pairs}点）",
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
    print("単純集計②: 単勝複数購入・馬連BOX（既存スコア列＋確定payoutsのみ）")
    print("=" * 60)

    db = DatabaseManager()
    win_payouts = fetch_win_payouts(db)
    umaren_payouts = fetch_umaren_payouts(db)

    print()
    print(f"確定単勝払戻データ件数: {len(win_payouts)}")
    print(f"確定馬連払戻データ件数: {len(umaren_payouts)}")

    win_keys = ["購入点数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]
    box_keys = ["購入レース数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]

    print()
    print("=" * 60)
    print("① 単勝複数購入")
    print("=" * 60)

    win_results = {}
    for rank_column in ["raw_rank", "odds_adjusted_rank"]:
        for top_n in [1, 2, 3]:
            print()
            result = simulate_win_multi(rank_column, top_n, win_payouts, db)
            print_result(result, win_keys)
            win_results[(rank_column, top_n)] = result

    print()
    print("=== ①比較表 ===")
    print(f"{'対象':32s}{'購入点数':>10s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    for (rank_column, top_n), result in win_results.items():
        print(f"{result['対象']:32s}{result['購入点数']:10d}"
              f"{result['的中率(%)']:12.2f}{result['回収率(%)']:12.2f}")

    print()
    print("=" * 60)
    print("② 馬連BOX（odds_adjusted_rank基準）")
    print("=" * 60)

    box_results = {}
    for box_size in [3, 5]:
        print()
        result = simulate_umaren_box(box_size, umaren_payouts, db)
        print_result(result, box_keys)
        box_results[box_size] = result

    print()
    print("=== ②比較表 ===")
    print(f"{'対象':38s}{'購入レース数':>12s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    for box_size, result in box_results.items():
        print(f"{result['対象']:38s}{result['購入レース数']:12d}"
              f"{result['的中率(%)']:12.2f}{result['回収率(%)']:12.2f}")


if __name__ == "__main__":
    main()
