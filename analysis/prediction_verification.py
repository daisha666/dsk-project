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

買い目推奨ランク（S/A/B。recommendation_rank）はpredictionsテーブルには
保存していないため、ここでai/backtest.py::classify_recommendation_rankと
CLASS_FILTER（prediction/predict_race.py）を使って都度再計算する
（推奨ロジックを変更した場合、過去の予測ログに対しても新基準で
再評価できるようにするため）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import RANK_THRESHOLDS, classify_recommendation_rank
from database.db_manager import DatabaseManager
from prediction.predict_race import CLASS_FILTER

STAKE = 100
RANKS = [r for r, _, _ in RANK_THRESHOLDS]  # ["S", "A", "B"]


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


def _row_rank(row):
    """1行（fetch_resolved_predictions()の戻り値の1要素）の買い目推奨ランクを
    判定する。row[6]=expected_value, row[5]=market_odds, row[4]=race_class"""
    return classify_recommendation_rank(row[6], row[5], row[4], class_filter=CLASS_FILTER)


def summarize(rows, stake=STAKE, rank=None):
    """買い目推奨（S/A/Bいずれかのランクに該当するもの）に絞り込んだ上での
    的中率・回収率を計算する。rank="S"/"A"/"B"を指定すると、そのランクだけ
    （他のランクは含まない、ランクは重複しない排他的な区分）に絞って集計する。
    rank=None（既定）なら、S/A/Bいずれかに該当する予測すべてが対象"""
    if rank is not None:
        recommended = [row for row in rows if _row_rank(row) == rank]
    else:
        recommended = [row for row in rows if _row_rank(row) is not None]

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


def filter_most_recent_date(rows):
    """rows（fetch_resolved_predictions()の戻り値。row[3]=race_date）のうち、
    最も新しいrace_date（＝直近の検証ジョブ実行で新たに結果が確定した開催日、
    という前提）に属する行だけを返す。「今回（直近）の成績」用"""
    if not rows:
        return []
    latest_date = max(row[3] for row in rows)
    return [row for row in rows if row[3] == latest_date]


def group_by_month(rows):
    """rowsをrace_date（row[3]、"YYYY-MM-DD"）の年月ごとにまとめ、
    [(year_month, rows), ...] を古い順に返す。「月別成績」用"""
    months = {}
    for row in rows:
        year_month = row[3][:7]
        months.setdefault(year_month, []).append(row)
    return sorted(months.items())


def main():
    print("=" * 60)
    print("dsk_Project")
    print("実運用予測ログの検証（机上バックテストではなく実データ）")
    print(f"基準: S(EV>=1.4・上限30倍) / A(EV>=1.2・上限35倍) / B(EV>=1.0・上限35倍)・"
          f"{'1勝クラス以上限定' if CLASS_FILTER else '全クラス'}")
    print("=" * 60)

    rows = fetch_resolved_predictions()

    if not rows:
        print()
        print("結果が確定した予測ログがまだありません。")
        print("prediction/predict_race.pyを運用し、レースの結果が確定してから再実行してください。")
        return

    for rank in [None] + RANKS:
        label = "全ランク合計" if rank is None else f"ランク{rank}"
        print()
        print(f"--- {label} ---")
        result = summarize(rows, rank=rank)
        for key, value in result.items():
            if isinstance(value, float):
                print(f"{key}: {value:,.2f}")
            else:
                print(f"{key}: {value:,}")


if __name__ == "__main__":
    main()
