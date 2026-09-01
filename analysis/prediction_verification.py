"""
dsk_Project
実運用の予測ログを確定結果で検証する
Version 0.1

prediction/predict_race.pyが書き込むpredictionsテーブル（実際に予想を出した
時点でのscore=odds_adjusted_score・probability=pred_win_prob・
expected_value・rank=odds_adjusted_rank）を、後日確定したresults・payoutsと
突き合わせ、机上のバックテストではなく実運用で蓄積された実データでの
的中率・回収率を計算する。ユーザー確定事項（README「Stage3としての基準値」）
の通り、今後の精緻化はこの実データ検証に委ねる。

買い目推奨（is_recommended）はpredictionsテーブルには保存していないため、
ここでai/backtest.py::is_class_includedとStage3確定基準値
（prediction/predict_race.py::EV_THRESHOLD・ODDS_CAP・CLASS_FILTER）を
使って都度再計算する（推奨ロジックを変更した場合、過去の予測ログに対しても
新基準で再評価できるようにするため）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import is_class_included
from database.db_manager import DatabaseManager
from prediction.predict_race import CLASS_FILTER, EV_THRESHOLD, ODDS_CAP

STAKE = 100


def fetch_resolved_predictions(db=None):
    """predictionsテーブルのうち、既に結果が確定しているものを
    market_odds・race_class・確定単勝払戻と一緒に返す"""
    if db is None:
        db = DatabaseManager()

    return db.fetchall("""
        SELECT
            p.race_id, p.horse_id, e.horse_number, r.race_date, r.race_class,
            e.odds AS market_odds, p.expected_value, p.rank,
            res.finish_position,
            (SELECT payout_yen FROM payouts pay
             WHERE pay.race_id = p.race_id AND pay.bet_type = '単勝'
               AND pay.combination = CAST(e.horse_number AS TEXT)) AS payout_yen
        FROM predictions p
        JOIN entries e ON e.race_id = p.race_id AND e.horse_id = p.horse_id
        JOIN races r ON r.race_id = p.race_id
        JOIN results res ON res.race_id = p.race_id AND res.horse_id = p.horse_id
        WHERE res.finish_position IS NOT NULL
        ORDER BY r.race_date, p.race_id
    """)


def summarize(rows, stake=STAKE):
    """is_recommended（Stage3確定基準を満たすか）で絞り込んだ上での
    的中率・回収率を計算する"""
    recommended = [
        row for row in rows
        if row[6] is not None and row[6] >= EV_THRESHOLD  # expected_value
        and row[5] is not None and row[5] <= ODDS_CAP  # market_odds
        and (is_class_included(row[4]) if CLASS_FILTER else True)  # race_class
    ]

    n_buys = len(recommended)
    hits = [row for row in recommended if row[8] == 1]  # finish_position
    n_hits = len(hits)

    total_stake = n_buys * stake
    total_payout = sum((row[9] or 0) * (stake / 100) for row in hits if row[9] is not None)
    missing_payout = sum(1 for row in hits if row[9] is None)

    hit_rate = n_hits / n_buys * 100 if n_buys else float("nan")
    recovery_rate = total_payout / total_stake * 100 if total_stake else float("nan")

    return {
        "対象予測数（結果確定済み）": len(rows),
        "買い目推奨数": n_buys,
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
    print("実運用予測ログの検証（机上バックテストではなく実データ）")
    print(f"基準: EV>={EV_THRESHOLD}・オッズ上限{ODDS_CAP}倍・"
          f"{'1勝クラス以上限定' if CLASS_FILTER else '全クラス'}")
    print("=" * 60)

    rows = fetch_resolved_predictions()

    if not rows:
        print()
        print("結果が確定した予測ログがまだありません。")
        print("prediction/predict_race.pyを運用し、レースの結果が確定してから再実行してください。")
        return

    result = summarize(rows)
    print()
    for key, value in result.items():
        if isinstance(value, float):
            print(f"{key}: {value:,.2f}")
        else:
            print(f"{key}: {value:,}")


if __name__ == "__main__":
    main()
