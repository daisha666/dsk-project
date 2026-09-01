"""
dsk_Project
特徴量生成: 新規レース向け差分計算のオーケストレーション
Version 0.1

これまで8項目パイプラインは全件フル再計算（約7時間、DatabaseManagerが
1回のSELECT/INSERTごとにDB接続を開閉する設計のオーバーヘッドが支配的）
しか無く、automation/denma_predict_job.pyが新規に出馬表を保存しても、
別途このフル再計算を走らせない限りpredict_race.pyはその新規レースを
スコアできなかった（build_upcoming_dataset()がfeaturesテーブルとINNER JOIN
しているため、featuresに行が無いレースは完全に不可視になる）。

各power scriptの計算コスト自体（履歴の一括ロード＋レース内Min-Max正規化）は
軽く、コストのほとんどはDB接続開閉の回数（＝対象レース数×出走頭数）に
比例するため、対象レースを絞り込むだけで劇的に速くなる。8項目それぞれに
build_for_races(race_ids)を追加済みなので、ここではそれらと
overall_score.py・odds_score.pyをまとめて呼び出す。

新規レースの特徴量が揃うまでの手順（automation/denma_predict_job.pyから
呼ばれる想定）:
  1. collectors/yahoo_denma_collector.pyで出馬表を取得（races/entries保存）
  2. build_features_for_races(race_ids) …本モジュール
  3. prediction/predict_race.pyで予測（features行が揃って初めてスコア可能になる）
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.agari_power import AgariPowerFeatureBuilder
from feature_engineering.distance_power import DistancePowerFeatureBuilder
from feature_engineering.jockey_power import JockeyPowerFeatureBuilder
from feature_engineering.kyakushitsu_power import KyakushitsuPowerFeatureBuilder
from feature_engineering.odds_score import OddsScoreFeatureBuilder
from feature_engineering.overall_score import OverallScoreFeatureBuilder
from feature_engineering.pedigree_power import PedigreePowerFeatureBuilder
from feature_engineering.stability_power import StabilityPowerFeatureBuilder
from feature_engineering.trainer_power import TrainerPowerFeatureBuilder
from feature_engineering.turn_power import TurnPowerFeatureBuilder

# pace_bias_power（ペースバイアス補正）は未実装（feature_engineering/pace_bias.py参照。
# 常にNULL=補正なしとして扱われるため、ここでは呼ばない）
POWER_BUILDER_CLASSES = [
    AgariPowerFeatureBuilder,
    KyakushitsuPowerFeatureBuilder,
    JockeyPowerFeatureBuilder,
    DistancePowerFeatureBuilder,
    TurnPowerFeatureBuilder,
    StabilityPowerFeatureBuilder,
    PedigreePowerFeatureBuilder,
    TrainerPowerFeatureBuilder,
]


def fetch_unconfirmed_race_ids(db=None):
    """出馬表（entries）はあるが、まだ結果が確定していないrace_id一覧を返す
    （ai/build_dataset.py::UPCOMING_QUERYと同じ絞り込み条件。featuresの有無は
    問わない＝ここではfeaturesが無いレースも対象に含めたいため）。
    automation/denma_predict_job.pyが「今回新規に取得したrace_id」を厳密に
    追跡する代わりに使う。現在未確定の全レースを毎回対象にすることで、
    過去に何らかの理由で特徴量計算が漏れたレースがあっても自然に追いつける"""
    if db is None:
        db = DatabaseManager()

    rows = db.fetchall("""
        SELECT DISTINCT e.race_id FROM entries e
        WHERE e.race_id NOT IN (
            SELECT DISTINCT race_id FROM results WHERE finish_position IS NOT NULL
        )
    """)
    return [r[0] for r in rows]


def build_features_for_races(race_ids, log=print):
    """指定したrace_idだけについて、8項目・overall_score・odds_adjusted_score
    （+raw_rank/odds_adjusted_rank）を計算・保存する。race_idsが空なら何もしない"""
    if not race_ids:
        log("対象レースなし。特徴量計算はスキップ")
        return

    log(f"特徴量差分計算を開始: 対象レース数={len(race_ids)}")

    for builder_cls in POWER_BUILDER_CLASSES:
        builder = builder_cls()
        log(f"[{builder_cls.__name__}] 計算中...")
        builder.build_for_races(race_ids, log=log)

    log("[OverallScoreFeatureBuilder] 計算中...")
    OverallScoreFeatureBuilder().build(log=log)

    log("[OddsScoreFeatureBuilder] 計算中...")
    OddsScoreFeatureBuilder().build_for_races(race_ids, log=log)

    log(f"特徴量差分計算が完了: 対象レース数={len(race_ids)}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="指定したrace_idの特徴量だけを差分計算する（動作確認用）")
    parser.add_argument("race_ids", nargs="+", help="対象のrace_id（複数指定可）")
    args = parser.parse_args()

    print("=" * 40)
    print("dsk_Project")
    print("build_for_new_races 実行")
    print("=" * 40)

    build_features_for_races(args.race_ids)
