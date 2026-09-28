"""
dsk_Project
実運用の予測ログを確定結果で検証する
Version 0.1

prediction/predict_race.pyが書き込むprediction_snapshotsテーブルのうち、
レースごとに「発走前の最後の予測」（predicted_at < 発走時刻）を、後日確定した
results・payoutsと突き合わせ、机上のバックテストではなく実運用で蓄積された
実データでの的中率・回収率を計算する。ユーザー確定事項（README「Stage3と
しての基準値」）の通り、今後の精緻化はこの実データ検証に委ねる。

2026-09-28まではpredictionsテーブル（最新の1件で上書き）と確定後オッズを
使っていたが、発走後も予測を作り直していたため、検証しているのが発走後の値に
なっており、発走前に実際に表示していた推奨と食い違っていた（README参照）。
発走時刻（races.post_time）が不明なレースは、発走前の版を特定できないため
検証対象から外す（count_unverifiable_races()で件数を確認できる）。

買い目推奨ランク（S/A/B）は、ここでai/backtest.py::classify_recommendation_rankと
CLASS_FILTER（prediction/predict_race.py）を使って、スナップショットの期待値と
予測に使ったオッズから都度再計算する（推奨ロジックを変更した場合、過去の
予測ログに対しても新基準で再評価できるようにするため）。
"""

import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import RANK_THRESHOLDS, classify_recommendation_rank
from database.db_manager import DatabaseManager
from prediction.predict_race import CLASS_FILTER, PREDICTION_MARKS

STAKE = 100
RANKS = [r for r, _, _ in RANK_THRESHOLDS]  # ["S", "A", "B"]
MARK_BOX_SIZE = len(PREDICTION_MARKS)  # 印（◎○▲△☆）を割り当てる頭数と同じ（5頭）

# 発走日からこの日数を過ぎたレースは、スナップショットを発走前の最後の1件だけに圧縮する
# （5分おきの自動更新で1開催日あたり数万行増えるため。直近分は推移を追えるよう全件残す）
SNAPSHOT_KEEP_DAYS = 30

# レースごとの「発走前の最後の予測」の時刻
LAST_PRE_POST_SQL = """
    SELECT s.race_id, MAX(s.predicted_at) AS predicted_at
    FROM prediction_snapshots s
    JOIN races r ON r.race_id = s.race_id
    WHERE r.post_time IS NOT NULL
      AND s.predicted_at < r.race_date || ' ' || r.post_time || ':00'
    GROUP BY s.race_id
"""


def fetch_resolved_predictions(db=None):
    """結果が確定したレースについて、発走前の最後の予測スナップショットを
    予測に使ったオッズ・race_class・確定単勝払戻と一緒に返す。
    行の形式（row[0]〜row[9]）は従来のpredictionsベースの版と同じ:
    race_id, horse_id, horse_number, race_date, race_class, market_odds,
    expected_value, rank(odds_adjusted_rank), finish_position, payout_yen

    finish_positionが無い馬のうち、中止・失格（競走は成立したが完走できず、
    払戻なしの負けになる）はrow[8]=NULLのまま含める（払戻も無いのでsummarize()は
    自動的に「買ったが外れた」として扱う）。取消・除外（レース不成立で全額
    返還される）は、実際に賭けたとしても損益が発生しないため対象から除く
    （2026-09-29。従来finish_position IS NOT NULLだけで絞っており、中止・失格も
    まとめて検証から漏れていた）"""
    if db is None:
        db = DatabaseManager()

    return db.fetchall(f"""
        WITH last_pre AS ({LAST_PRE_POST_SQL})
        SELECT
            s.race_id, s.horse_id, e.horse_number, r.race_date, r.race_class,
            s.market_odds, s.expected_value, s.odds_adjusted_rank,
            res.finish_position,
            (SELECT payout_yen FROM payouts pay
             WHERE pay.race_id = s.race_id AND pay.bet_type = '単勝'
               AND pay.combination = CAST(e.horse_number AS TEXT)) AS payout_yen
        FROM last_pre lp
        JOIN prediction_snapshots s ON s.race_id = lp.race_id AND s.predicted_at = lp.predicted_at
        JOIN entries e ON e.race_id = s.race_id AND e.horse_id = s.horse_id
        JOIN races r ON r.race_id = s.race_id
        JOIN results res ON res.race_id = s.race_id AND res.horse_id = s.horse_id
        WHERE res.finish_position IS NOT NULL OR res.finish_status IN ('中止', '失格')
        ORDER BY r.race_date, s.race_id
    """)


def count_unverifiable_races(db=None):
    """結果が確定し予測スナップショットもあるのに、発走時刻が不明、または
    発走前のスナップショットが無いため検証できないレース数を返す
    （黙って検証対象から漏れるのを防ぐため、result_verify_job.pyがログに出す）"""
    if db is None:
        db = DatabaseManager()

    row = db.fetchone(f"""
        SELECT COUNT(DISTINCT s.race_id)
        FROM prediction_snapshots s
        WHERE s.race_id IN (SELECT race_id FROM results WHERE finish_position IS NOT NULL)
          AND s.race_id NOT IN (SELECT race_id FROM ({LAST_PRE_POST_SQL}))
    """)
    return row[0]


def compact_prediction_snapshots(db=None, keep_days=SNAPSHOT_KEEP_DAYS, today=None):
    """発走日からkeep_days日を過ぎたレースのスナップショットを、発走前の最後の1件
    （検証に使う版）だけ残して削除する。発走前の版が特定できないレース
    （発走時刻不明など）は消さずに残す。削除した行数を返す"""
    if db is None:
        db = DatabaseManager()

    cutoff = ((today or date.today()) - timedelta(days=keep_days)).isoformat()
    conn = db.connect()
    try:
        cur = conn.execute(f"""
            DELETE FROM prediction_snapshots
            WHERE race_id IN (SELECT race_id FROM races WHERE race_date < ?)
              AND race_id IN (SELECT race_id FROM ({LAST_PRE_POST_SQL}))
              AND (race_id, predicted_at) NOT IN ({LAST_PRE_POST_SQL})
        """, (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _row_rank(row):
    """1行（fetch_resolved_predictions()の戻り値の1要素）の買い目推奨ランクを
    判定する。row[6]=expected_value, row[5]=market_odds（予測に使ったオッズ）,
    row[4]=race_class"""
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


def group_rows_by_race(rows):
    """rows（fetch_resolved_predictions()の戻り値）をrace_idごとにまとめて返す
    （row[0]=race_id）。◎・印5頭BOXはレース単位でしか判定できない
    （S/A/B買い目推奨は馬単位で完結するため使わない）"""
    races = {}
    for row in rows:
        races.setdefault(row[0], []).append(row)
    return races


def _lookup_umaren_payout(db, race_id, horse_number_a, horse_number_b):
    combo = f"{min(horse_number_a, horse_number_b)}-{max(horse_number_a, horse_number_b)}"
    row = db.fetchone(
        "SELECT payout_yen FROM payouts WHERE race_id = ? AND bet_type = '馬連' AND combination = ?",
        (race_id, combo),
    )
    return row[0] if row else None


def summarize_honmei_win(rows, stake=STAKE):
    """◎（odds_adjusted_rank=1位の馬）を単勝1点買いしたとして検証する。
    S/A/B買い目推奨（EV・オッズ上限による選別）とは無関係に、レースごとに
    必ず1点買う前提の指標であるため、性質がまったく異なる（買い目推奨が
    0件のレースでも◎の単勝は毎回購入したものとして数える）"""
    races = group_rows_by_race(rows)

    n_races = 0
    n_hits = 0
    total_stake = 0
    total_payout = 0.0
    missing_payout = 0

    for race_rows in races.values():
        honmei = next((r for r in race_rows if r[7] == 1), None)  # r[7]=rank(odds_adjusted_rank)
        if honmei is None:
            continue

        n_races += 1
        total_stake += stake
        if honmei[8] == 1:  # r[8]=finish_position
            n_hits += 1
            payout_yen = honmei[9]  # r[9]=単勝payout_yen（fetch_resolved_predictionsで既にJOIN済み）
            if payout_yen is not None:
                total_payout += payout_yen / 100 * stake
            else:
                missing_payout += 1

    hit_rate = n_hits / n_races * 100 if n_races else float("nan")
    recovery_rate = total_payout / total_stake * 100 if total_stake else float("nan")

    return {
        "対象レース数": n_races,
        "的中数": n_hits,
        "的中率(%)": hit_rate,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
        "払戻欠損": missing_payout,
    }


def summarize_mark_box_umaren(rows, db=None, stake=STAKE, box_size=MARK_BOX_SIZE):
    """印（◎○▲△☆、odds_adjusted_rank上位5頭）をBOX馬連で購入したとして検証する。
    S/A/B買い目推奨とは無関係に、レースごとに必ず1BOX買う前提の指標である点が
    ◎単勝と同じ理由で性質が異なる。実際の1着・2着が両方ともBOX内なら的中とし、
    確定払戻額（payoutsテーブル）で回収率を計算する
    （combination_oddsは発走前スナップショットのため使わない。理由はREADME
    「combination_oddsは払戻計算には使わない」の記載と同じ）"""
    if db is None:
        db = DatabaseManager()

    races = group_rows_by_race(rows)

    n_races = 0
    n_hits = 0
    total_stake = 0
    total_payout = 0.0
    missing_payout = 0

    for race_id, race_rows in races.items():
        box = [r for r in race_rows if r[7] is not None and r[7] <= box_size]  # r[7]=rank
        if len(box) < 2:
            continue

        n_pairs = len(box) * (len(box) - 1) // 2
        n_races += 1
        total_stake += n_pairs * stake

        finishers = sorted(
            ((r[2], r[8]) for r in race_rows if r[8] is not None),  # r[2]=horse_number, r[8]=finish_position
            key=lambda x: x[1],
        )
        if len(finishers) < 2:
            continue

        top1, top2 = finishers[0][0], finishers[1][0]
        box_numbers = {r[2] for r in box}
        if top1 in box_numbers and top2 in box_numbers:
            n_hits += 1
            payout_yen = _lookup_umaren_payout(db, race_id, top1, top2)
            if payout_yen is not None:
                total_payout += payout_yen / 100 * stake
            else:
                missing_payout += 1

    hit_rate = n_hits / n_races * 100 if n_races else float("nan")
    recovery_rate = total_payout / total_stake * 100 if total_stake else float("nan")

    return {
        "対象レース数": n_races,
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
