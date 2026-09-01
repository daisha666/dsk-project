"""
dsk_Project
自動化ジョブ: 出馬表取得→特徴量差分計算→予想実行
Version 0.2

collectors/yahoo_denma_collector.pyで未確定レースの出馬表を取得し、
feature_engineering/build_for_new_races.pyで8項目・overall_score・
odds_adjusted_scoreを差分計算してから、prediction/predict_race.pyで
予想・買い目推奨を計算する。

（旧v0.1では特徴量計算を別途フル再計算ジョブに任せる設計だったが、全件
フル再計算は約7時間かかり自動化に組み込めなかった。DB接続開閉の回数
〔対象レース数×出走頭数に比例〕が支配的コストだったため、対象レースを
絞り込んだbuild_for_races()を8項目それぞれに実装し、新規レースだけの
差分計算で完結するようにした。README「既知の運用課題」参照）
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.yahoo_denma_collector import YahooDenmaCollector


def run_denma_predict_job(log=print):
    log("出馬表取得を開始")
    collector = YahooDenmaCollector()
    collect_stats = collector.collect(log=log)
    log(f"出馬表取得完了: 保存レース数={collect_stats['races_saved']} "
        f"保存出走馬数={collect_stats['entries_saved']}")

    if collect_stats["races_saved"] == 0:
        return "出馬表: 新規保存0件（対象レース無し）。predict_race.pyはスキップ"

    log("特徴量差分計算を開始（8項目・overall_score・odds_adjusted_score）")
    from feature_engineering.build_for_new_races import build_features_for_races, fetch_unconfirmed_race_ids

    unconfirmed_race_ids = fetch_unconfirmed_race_ids()
    build_features_for_races(unconfirmed_race_ids, log=log)

    log("予想実行を開始")

    from prediction.predict_race import (
        CLASS_FILTER,
        EV_THRESHOLD,
        ODDS_CAP,
        save_predictions_to_db,
        score_upcoming_races,
        train_current_model,
    )
    from prediction.generate_report import generate_site
    import pandas as pd

    model = train_current_model(log=log)
    scored = score_upcoming_races(model, log=log)
    n_saved = save_predictions_to_db(scored)

    settings = {"ev_threshold": EV_THRESHOLD, "odds_cap": ODDS_CAP, "class_filter": CLASS_FILTER}
    generated_at = pd.Timestamp.now().isoformat()
    generate_site(scored, settings, generated_at, log=log)

    log("GitHub Pagesへデプロイ")
    from automation.git_deploy import deploy_docs
    deploy_docs(log=log)

    return f"出馬表{collect_stats['races_saved']}レース保存、予測{n_saved}件保存"


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("denma_predict_job 実行")
    print("=" * 40)
    print(run_denma_predict_job())
