"""
dsk_Project
単純集計: raw_rank=1位 vs odds_adjusted_rank=1位 の単勝的中率・回収率
Version 0.1

開発指示書5節「検証すべき問い2」（素点ベース順位とオッズ反映後順位、
どちらを印の基準にする方が回収率が高いか）に直接答えるための、
LightGBM学習を一切使わない単純集計。

featuresテーブルに既に保存されている2つの順位列をそのまま使う:
  raw_rank          : overall_score（8項目合成スコア、オッズ反映前）の
                       レース内順位（feature_engineering/odds_score.py）
  odds_adjusted_rank : odds_adjusted_score（overall_score×(1/オッズ)×係数、
                       開発指示書2.3の「AK列」相当）のレース内順位

各順位が1位の馬を、収集済み全レースで単勝均等購入したと仮定し、
的中率・回収率を確定payoutsテーブル（bet_type='単勝'）ベースで計算する
（ai/backtest.pyと同じ確定払戻の使い方。発走前オッズのスナップショットは
順位そのものの計算には既に使われているが、払戻額の計算には使わない）。

モデル学習・EV計算・オッズ上限フィルタは一切行わない。対象レースの
絞り込み（新馬戦除外等）も行わない（「3年分の全レース」という依頼通り）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import fetch_win_payouts
from database.db_manager import DatabaseManager

STAKE = 100


def fetch_rank1_horses(rank_column, db):
    """指定したrank列（raw_rank or odds_adjusted_rank）が1位の馬を
    (race_id, horse_number, finish_position) のリストで返す"""
    rows = db.fetchall(f"""
        SELECT f.race_id, e.horse_number, res.finish_position
        FROM features f
        JOIN entries e ON e.race_id = f.race_id AND e.horse_id = f.horse_id
        LEFT JOIN results res ON res.race_id = f.race_id AND res.horse_id = f.horse_id
        WHERE f.{rank_column} = 1
    """)
    return rows


def simulate_rank1(rank_column, win_payouts, db, stake=STAKE):
    rows = fetch_rank1_horses(rank_column, db)

    n_races = len(rows)
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

    total_stake = n_races * stake
    hit_rate = n_hits / n_races * 100 if n_races else float("nan")
    recovery_rate = total_payout / total_stake * 100 if total_stake else float("nan")

    return {
        "rank_column": rank_column,
        "対象レース数": n_races,
        "的中数": n_hits,
        "的中率(%)": hit_rate,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
    }


def main():
    print("=" * 50)
    print("dsk_Project")
    print("単純集計: raw_rank=1位 vs odds_adjusted_rank=1位 の単勝成績")
    print("=" * 50)

    db = DatabaseManager()
    win_payouts = fetch_win_payouts(db)

    print()
    print(f"確定単勝払戻データ件数: {len(win_payouts)}")

    result_raw = simulate_rank1("raw_rank", win_payouts, db)
    result_odds = simulate_rank1("odds_adjusted_rank", win_payouts, db)

    print()
    for label, result in [("raw_rank=1位（素点ベース）", result_raw),
                           ("odds_adjusted_rank=1位（オッズ反映後）", result_odds)]:
        print(f"--- {label} ---")
        for key in ["対象レース数", "的中数", "的中率(%)", "総購入額(円)", "総払戻額(円)", "回収率(%)", "払戻欠損"]:
            v = result[key]
            if isinstance(v, float):
                print(f"  {key}: {v:,.2f}")
            else:
                print(f"  {key}: {v:,}")
        print()

    print("=" * 50)
    print("=== 比較 ===")
    print("=" * 50)
    print(f"{'':32s}{'対象レース数':>12s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    print(f"{'raw_rank=1位（素点ベース）':32s}"
          f"{result_raw['対象レース数']:12d}{result_raw['的中率(%)']:12.2f}{result_raw['回収率(%)']:12.2f}")
    print(f"{'odds_adjusted_rank=1位（オッズ反映後）':32s}"
          f"{result_odds['対象レース数']:12d}{result_odds['的中率(%)']:12.2f}{result_odds['回収率(%)']:12.2f}")
    print()
    print(f"回収率差（オッズ反映後－素点ベース） = {result_odds['回収率(%)'] - result_raw['回収率(%)']:+.2f}pt")
    print(f"的中率差（オッズ反映後－素点ベース） = {result_odds['的中率(%)'] - result_raw['的中率(%)']:+.2f}pt")


if __name__ == "__main__":
    main()
