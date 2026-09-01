"""
dsk_Project
自動化ジョブ: 出馬表取得→予想実行
Version 0.1

collectors/yahoo_denma_collector.pyで未確定レースの出馬表を取得し、
prediction/predict_race.pyで予想・買い目推奨を計算する
（8項目・overall_score・odds_adjusted_scoreの再計算はこのジョブでは行わない。
新規に取得した出馬表の特徴量計算は別途フル再計算ジョブに任せる設計が
将来的には必要になる可能性があるが、Stage1土台の実装が非常に重い
〔約7時間、README「既知の課題」参照〕ため、現段階では手動実行を前提とする）
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

    log("予想実行を開始（predict_race.pyはfeature_engineering未計算のレースを"
        "スコアできない点に注意。事前に8項目パイプラインの実行が必要）")

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
