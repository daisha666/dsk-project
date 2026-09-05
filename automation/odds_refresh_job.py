"""
dsk_Project
自動化ジョブ: ②オッズ取得・予想更新
Version 0.2

対象レース: entries はあるが結果はまだ確定していない（＝オッズが動き得る）
レース（ai/build_dataset.py::build_upcoming_dataset と同じ条件）。

v0.1ではPROJECT_EVのautomation/refresh_job.pyと同じ考え方（モデルの再学習は
せず、predictionsテーブルの予測勝率をキャッシュとして再利用し、オッズだけ
最新化する軽量設計）を採用していたが、2026-09-05の本番運用で重大な不具合が
見つかったため撤回した。

不具合の経緯: 枠番確定直後（オッズ・馬体重が未収集）に①データ取得・検証・
予想生成を実行すると、market_odds等が全馬分NULLになり、
model.predict_proba()がNaNの既定分岐へ収束してpred_win_probがレース内で
ほぼ均一な無意味な値になる（README「既知の運用課題」参照）。v0.1の設計では
この壊れた値がpredictionsテーブルにキャッシュされたまま、当日のオッズ自動
更新サイクルで何度再実行されても再学習されず使われ続けてしまい、表示上は
オッズが最新化されているため異常に気づきにくかった。

対策として、キャッシュ再利用をやめ、predict_race.pyと同じくtrain_current_model()
＋score_upcoming_races()を毎回フルで実行するよう変更した。8項目パイプライン・
DB接続まわりの高速化（2026-09-04対応）により、全履歴での再学習＋全未確定
レースの再スコアリングは約30秒程度で完了するため、5分おきの自動更新サイクル
に十分収まる。これによりキャッシュの陳腐化・破損というクラスの不具合が
構造的に発生しなくなる。
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

    log("predict_race.py: モデルを再学習し、最新オッズで予測・期待値を再計算")
    from prediction.predict_race import (
        EV_THRESHOLD, ODDS_CAP, CLASS_FILTER,
        save_predictions_to_db, score_upcoming_races, train_current_model,
    )
    from prediction.generate_report import generate_site
    import pandas as pd

    model = train_current_model(log=log)
    scored = score_upcoming_races(model, log=log)
    if len(scored) == 0:
        return f"{len(updated_races)}レースのオッズを更新しましたが、予測対象レースはありません"

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
