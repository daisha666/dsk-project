"""
dsk_Project
Stage3確定基準での実際の予想・購入判定（PROJECT_EVのprediction/predict_race.py相当）
Version 0.1

Stage3の一連の検証（ai/model_a_odds_adjusted_ablation.py・
ai/stage3_stability_recheck.py・ai/model_a_odds_adjusted_grid_search.py・
ai/model_a_odds_adjusted_holdout_validation.py）で確定した基準値を使い、
まだ結果が確定していないレース（collectors/yahoo_denma_collector.pyで
収集済み）について予測勝率・期待値を計算し、印（◎○▲△☆）・買い目推奨を
出力する。

Stage3確定基準（README「Stage3としての基準値」参照）:
  モデル: モデルA'（overall_score + odds_adjusted_score + 市場情報を
          特徴量に持つLightGBM。ai/build_dataset.py::FEATURE_COLUMNS_A_ODDS_ADJUSTED）
  EV閾値: 1.4（グリッドサーチ・ホールドアウト検証を踏まえ、1.3〜1.5の帯の
          中央寄りをピンポイントではなく「帯」の代表値として採用）
  オッズ上限: 30倍（同様に25〜30倍の帯の上限寄りを採用）
  クラスフィルタ: 1勝クラス以上限定（新馬戦・未勝利戦は買い目推奨の対象外。
          ただし全馬のスコア自体は新馬戦・未勝利戦も表示する）

  ★これらは「ピンポイントの最適値」ではなく「帯の代表値」である
  （ai/model_a_odds_adjusted_holdout_validation.pyで、単一の最適点は
  多重比較により信頼できないことを確認済み）。今後の精緻化は机上の
  バックテストではなく、実際に運用しながら蓄積される実データで
  継続検証していく方針（ユーザー確定事項）。

印の基準（開発指示書2.4のB案）:
  odds_adjusted_rank（オッズ反映後の順位）を基準に、レースごとに上位5頭へ
  ◎○▲△☆を割り当てる。raw_rank（素点ベース順位）も並行して表示し、
  どちらの方式が実際に回収率が良いか運用しながら比較できるようにする。

モデルの学習方法:
  Stage3のバックテストのような日付分割・ウォークフォワードはしない。
  実運用では「現時点で利用可能な全履歴データ」で学習し、まだ結果が
  確定していないレースを予測するのが自然なため、build_dataset()の
  全件で毎回学習し直す（実行のたびに直近レースまで反映した最新モデルになる）。
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import classify_recommendation_rank, is_class_included
from ai.build_dataset import (
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS_A_ODDS_ADJUSTED,
    build_dataset,
    build_upcoming_dataset,
)
from ai.train_model import train_model
from database.db_manager import DatabaseManager
from prediction.generate_report import generate_site

# Stage3確定基準値（帯の代表値。README「Stage3としての基準値」参照）
EV_THRESHOLD = 1.4
ODDS_CAP = 30
CLASS_FILTER = True

PREDICTION_MARKS = ["◎", "○", "▲", "△", "☆"]


def train_current_model(log=print):
    """現時点で利用可能な全履歴データでモデルA'を学習する。
    学習データの最終開催日をmodel.trained_through_に持たせる（予測スナップショットに記録するため）"""
    dataset = build_dataset()
    log(f"学習データ件数: {len(dataset)}（期間: {dataset['race_date'].min()} 〜 {dataset['race_date'].max()}）")
    model = train_model(dataset, FEATURE_COLUMNS_A_ODDS_ADJUSTED, CATEGORICAL_COLUMNS)
    model.trained_through_ = str(dataset["race_date"].max())
    return model


def assign_marks(scored):
    """レースごとにodds_adjusted_rank昇順で上位5頭へ◎○▲△☆を割り当てる"""
    scored = scored.copy()
    scored["mark"] = ""

    for _race_id, group in scored.groupby("race_id"):
        ranked = group.sort_values("odds_adjusted_rank", na_position="last")
        for i, (idx, _row) in enumerate(ranked.iterrows()):
            if i < len(PREDICTION_MARKS):
                scored.loc[idx, "mark"] = PREDICTION_MARKS[i]

    return scored


def score_upcoming_races(model, log=print, now=None):
    """未確定・発走前のレースを予測し、予測勝率・期待値・印・買い目推奨フラグを付けた
    DataFrameを返す。nowの時点で発走済みのレースは対象外（予測を作り直さない。
    ai/build_dataset.py::before_post_clause参照）"""
    upcoming = build_upcoming_dataset(now=now)
    if len(upcoming) == 0:
        log("予測対象レースがありません（未確定レースなし、または全馬デビュー戦のみ）")
        return upcoming

    X = upcoming[FEATURE_COLUMNS_A_ODDS_ADJUSTED]
    scored = upcoming.copy()
    scored["pred_win_prob"] = model.predict_proba(X)[:, 1]
    scored["expected_value"] = scored["pred_win_prob"] * scored["market_odds"]

    # 買い目推奨ランク（S=現行確定基準・A/B=段階的に緩い参考範囲。
    # ai/backtest.py::RANK_THRESHOLDS参照）。DataFrame.apply(axis=1)は
    # race_classがcategory dtypeでも素のobject dtype（文字列/None混在）を
    # 返すため、Series.apply()で以前発生したcategory dtype起因のTypeError
    # （dry run検証で発覚）は起きない
    scored["recommendation_rank"] = scored.apply(
        lambda row: classify_recommendation_rank(
            row["expected_value"], row["market_odds"], row["race_class"], class_filter=CLASS_FILTER
        ),
        axis=1,
    )
    scored["is_recommended"] = scored["recommendation_rank"].notna()

    return assign_marks(scored)


def print_report(scored, log=print):
    if len(scored) == 0:
        return

    for _race_id, group in scored.groupby("race_id"):
        info = group.iloc[0]
        log("")
        log("=" * 70)
        log(f"{info['race_date']} {info['course']} {info['round']:.0f}R "
            f"{info['surface']}{info['distance']:.0f}m {info['race_class']}")
        log("=" * 70)

        display = group.sort_values("odds_adjusted_rank", na_position="last")
        log(f"{'印':3s}{'馬番':>4s} {'馬名':<14s}{'素点順位':>8s}{'オッズ後順位':>10s}"
            f"{'オッズ':>8s}{'予測勝率':>9s}{'EV':>7s}  推奨")
        for _, row in display.iterrows():
            raw_rank = "-" if pd.isna(row["raw_rank"]) else f"{row['raw_rank']:.0f}"
            odds_rank = "-" if pd.isna(row["odds_adjusted_rank"]) else f"{row['odds_adjusted_rank']:.0f}"
            rank_label = row["recommendation_rank"] or "－"
            log(f"{row['mark']:3s}{row['horse_number']:4.0f} {row['horse_name']:<14s}"
                f"{raw_rank:>8s}{odds_rank:>10s}"
                f"{row['market_odds']:8.1f}{row['pred_win_prob'] * 100:8.2f}%{row['expected_value']:7.2f}"
                f"  {rank_label}")

    recommended = scored[scored["is_recommended"]].sort_values(
        ["race_id", "recommendation_rank", "expected_value"], ascending=[True, True, False]
    )
    log("")
    log("=" * 70)
    log(f"買い目推奨: {len(recommended)}点  "
        f"（S: EV>=1.4・上限30倍 / A: EV>=1.2・上限35倍 / B: EV>=1.0・上限35倍・"
        f"{'1勝クラス以上限定' if CLASS_FILTER else '全クラス'}）")
    log("=" * 70)
    for _, row in recommended.iterrows():
        log(f"  [{row['recommendation_rank']}] {row['race_date']} {row['course']}{row['round']:.0f}R "
            f"{row['horse_number']:.0f}番 {row['horse_name']}"
            f"（オッズ{row['market_odds']:.1f}倍 EV{row['expected_value']:.2f}）")


def _num(value, cast=float):
    """NaN/None→None、numpy型→Pythonの数値に変換する（SQLite保存用）"""
    return None if value is None or pd.isna(value) else cast(value)


def _text(value):
    return None if value is None or pd.isna(value) else str(value)


def save_predictions_to_db(scored, predicted_at, trained_through, db=None):
    """予測結果を保存する（実運用の予測ログ）。2つのテーブルに書く:

    predictions: レースごとの最新の予測（race_id+horse_idで上書き）。
    prediction_snapshots: 予測を実行するたびに1件ずつ追加する記録（2026-09-28追加）。
      予測値・表示した買い目推奨ランクに加え、その時点でモデルに入力した値
      （オッズ・人気・overall_score・odds_adjusted_score・トラックバイアス・
      馬体重など、時刻とともに変わるもの）を残す。検証は、レースごとに発走前の
      最後のスナップショットを使う（analysis/prediction_verification.py）。
      実運用とバックテストで推奨件数がずれたとき、入力値のどこが違ったかを
      後から追えるようにするため。

    predicted_at: 入力データを取り終えた後の時刻（datetime）。1回の実行で共通。
    trained_through: 学習データの最終開催日（train_current_model()のmodel.trained_through_）"""
    if db is None:
        db = DatabaseManager()

    if len(scored) == 0:
        return 0

    predicted_at_text = predicted_at.strftime("%Y-%m-%d %H:%M:%S")
    prediction_rows = []
    snapshot_rows = []
    for _, row in scored.iterrows():
        prediction_rows.append((
            row["race_id"], row["horse_id"], _num(row["odds_adjusted_score"]),
            _num(row["pred_win_prob"]), _num(row["expected_value"]), _num(row["odds_adjusted_rank"], int),
        ))
        snapshot_rows.append((
            row["race_id"], row["horse_id"], predicted_at_text, "live", trained_through,
            _num(row["pred_win_prob"]), _num(row["expected_value"]), _text(row["recommendation_rank"]),
            _num(row["raw_rank"], int), _num(row["odds_adjusted_rank"], int),
            _num(row["market_odds"]), _num(row["market_popularity"], int),
            _num(row["overall_score"]), _num(row["odds_adjusted_score"]),
            _num(row["bias_front_runner_score"]), _num(row["bias_inside_post_score"]),
            _num(row["weight"]), _num(row["horse_number"], int), _num(row["frame_number"], int),
            _text(row["track_condition"]),
        ))

    conn = db.connect()
    try:
        conn.executemany("""
            INSERT INTO predictions (race_id, horse_id, score, probability, expected_value, rank)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                score = excluded.score,
                probability = excluded.probability,
                expected_value = excluded.expected_value,
                rank = excluded.rank
        """, prediction_rows)
        conn.executemany("""
            INSERT OR REPLACE INTO prediction_snapshots (
                race_id, horse_id, predicted_at, source, trained_through,
                probability, expected_value, recommendation_rank, raw_rank, odds_adjusted_rank,
                market_odds, market_popularity, overall_score, odds_adjusted_score,
                bias_front_runner_score, bias_inside_post_score,
                weight, horse_number, frame_number, track_condition
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, snapshot_rows)
        conn.commit()
    finally:
        conn.close()

    return len(prediction_rows)


def main():
    print("=" * 70)
    print("dsk_Project")
    print("predict_race: Stage3確定基準での予想・購入判定")
    print(f"モデルA'（overall_score+odds_adjusted_score+市場情報）  "
          f"EV>={EV_THRESHOLD}  オッズ上限{ODDS_CAP}倍  "
          f"{'1勝クラス以上限定' if CLASS_FILTER else '全クラス'}")
    print("=" * 70)

    model = train_current_model()
    predicted_at = datetime.now()
    scored = score_upcoming_races(model, now=predicted_at)
    print_report(scored)

    n_saved = save_predictions_to_db(scored, predicted_at, model.trained_through_)
    print()
    print(f"predictionsテーブルへ保存: {n_saved}件")

    settings = {"ev_threshold": EV_THRESHOLD, "odds_cap": ODDS_CAP, "class_filter": CLASS_FILTER}
    generated_at = pd.Timestamp.now().isoformat()
    generate_site(scored, settings, generated_at)


if __name__ == "__main__":
    main()
