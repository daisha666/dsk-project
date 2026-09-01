"""
dsk_Project
自動化ジョブ: ②オッズ取得・予想更新（軽量処理）
Version 0.1

PROJECT_EVのautomation/refresh_job.pyと同じ考え方: モデルの再学習は行わず、
predictionsテーブルに保存済みの予測勝率（probability。predict_race.pyが
直近の学習結果から書き込んだキャッシュ）をそのまま再利用し、最新の単勝・
複勝オッズだけをcollectors/yahoo_denma_collector.py::fetch_tfw_oddsで
再取得して、期待値・買い目推奨・docs/data/predictions.jsonを更新する。

対象レース: entries はあるが結果はまだ確定していない（＝オッズが動き得る）
レース（ai/build_dataset.py::build_upcoming_dataset と同じ条件）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.yahoo_denma_collector import YahooDenmaCollector
from database.db_manager import DatabaseManager
from feature_engineering.odds_score import OddsScoreFeatureBuilder


def find_active_race_ids(db):
    """entriesはあるが結果未確定（オッズが動き得る）レースのrace_id一覧を返す"""
    rows = db.fetchall("""
        SELECT DISTINCT e.race_id FROM entries e
        WHERE e.race_id NOT IN (SELECT DISTINCT race_id FROM results WHERE finish_position IS NOT NULL)
    """)
    return [r[0] for r in rows]


def refresh_odds_for_race(race_id, collector, db, log=print):
    """1レース分のtfw（単勝・複勝）オッズとワイドオッズを再取得し、
    entries.odds等・combination_oddsを更新する。更新した頭数を返す
    （0なら出馬表が無い等でスキップ）。

    ワイドの発走前オッズは複勝・ワイドEVバックテストに必要だが過去分は
    存在しないため（README「複勝・ワイドのバックテスト」参照）、この
    自動更新を通じて今後蓄積していく"""
    odds = collector.fetch_tfw_odds(race_id)
    for horse_id, (win_odds, place_low, place_high) in odds.items():
        collector.update_entry_odds(race_id, horse_id, win_odds, place_low, place_high)

    try:
        wide_odds = collector.fetch_wide_odds(race_id)
        collector.save_wide_odds(race_id, wide_odds)
    except Exception as exc:
        log(f"[{race_id}] ワイドオッズ取得エラー（単勝・複勝は継続）: {exc}")

    return len(odds)


def run_odds_refresh_job(log=print):
    db = DatabaseManager()
    race_ids = find_active_race_ids(db)

    if not race_ids:
        return "対象レースなし（結果未確定のレースがありません）"

    log(f"オッズ再取得対象: {len(race_ids)}レース")
    collector = YahooDenmaCollector()

    updated_races = []
    for race_id in race_ids:
        try:
            n = refresh_odds_for_race(race_id, collector, db, log=log)
            if n > 0:
                updated_races.append(race_id)
                log(f"[{race_id}] オッズ再取得完了（{n}頭）")
        except Exception as exc:
            log(f"[{race_id}] オッズ再取得エラー（他レースは継続）: {exc}")

    if not updated_races:
        return "オッズを再取得できたレースがありませんでした"

    log("odds_adjusted_score・順位を再計算")
    odds_builder = OddsScoreFeatureBuilder()
    odds_builder.build_for_races(updated_races, log=log)

    log("predict_race.py: 予測勝率キャッシュ（predictionsテーブル）を使い期待値を再計算")
    from prediction.predict_race import save_predictions_to_db
    from ai.build_dataset import build_upcoming_dataset
    from ai.backtest import is_class_included
    from prediction.predict_race import EV_THRESHOLD, ODDS_CAP, CLASS_FILTER, assign_marks
    from prediction.generate_report import generate_site
    import pandas as pd

    upcoming = build_upcoming_dataset()
    if len(upcoming) == 0:
        return f"{len(updated_races)}レースのオッズを更新しましたが、予測対象レースはありません"

    cached_probs = db.fetchall("SELECT race_id, horse_id, probability FROM predictions")
    prob_map = {(r, h): p for r, h, p in cached_probs}

    upcoming = upcoming.copy()
    upcoming["pred_win_prob"] = upcoming.apply(
        lambda row: prob_map.get((row["race_id"], row["horse_id"])), axis=1
    )
    # 予測キャッシュが無い馬（初めてこのジョブより先にpredict_race.pyが
    # 走っていない等）は期待値を計算できないため対象外にする
    before = len(upcoming)
    upcoming = upcoming[upcoming["pred_win_prob"].notna()].copy()
    skipped = before - len(upcoming)
    if skipped:
        log(f"予測キャッシュが無い{skipped}頭はスキップ（先に①のジョブが必要）")

    if len(upcoming) == 0:
        return f"{len(updated_races)}レースのオッズを更新しましたが、予測キャッシュが無く期待値を再計算できません"

    upcoming["expected_value"] = upcoming["pred_win_prob"] * upcoming["market_odds"]
    ev_ok = upcoming["expected_value"] >= EV_THRESHOLD
    odds_ok = upcoming["market_odds"] <= ODDS_CAP
    if CLASS_FILTER:
        # race_classがcategory dtypeのため、.astype(bool)で素のbool型へ変換する
        # （prediction/predict_race.py::score_upcoming_racesと同じ理由。
        # dry run検証で発覚した不具合）
        class_ok = upcoming["race_class"].apply(is_class_included).astype(bool)
    else:
        class_ok = True
    upcoming["is_recommended"] = ev_ok & odds_ok & class_ok
    scored = assign_marks(upcoming)

    n_saved = save_predictions_to_db(scored)

    settings = {"ev_threshold": EV_THRESHOLD, "odds_cap": ODDS_CAP, "class_filter": CLASS_FILTER}
    generated_at = pd.Timestamp.now().isoformat()
    generate_site(scored, settings, generated_at, log=log)

    log("GitHub Pagesへデプロイ")
    from automation.git_deploy import deploy_docs
    deploy_docs(log=log)

    return f"{len(updated_races)}レースのオッズ・{n_saved}件の期待値を更新しデプロイしました"


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("odds_refresh_job 実行")
    print("=" * 40)
    print(run_odds_refresh_job())
