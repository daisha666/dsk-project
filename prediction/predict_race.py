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

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import is_class_included
from ai.build_dataset import (
    CATEGORICAL_COLUMNS,
    FEATURE_COLUMNS_A_ODDS_ADJUSTED,
    build_dataset,
    build_upcoming_dataset,
)
from ai.train_model import train_model
from config.config import OUTPUT_DIR

# Stage3確定基準値（帯の代表値。README「Stage3としての基準値」参照）
EV_THRESHOLD = 1.4
ODDS_CAP = 30
CLASS_FILTER = True

PREDICTION_MARKS = ["◎", "○", "▲", "△", "☆"]

PREDICTIONS_OUTPUT_PATH = OUTPUT_DIR / "predictions_latest.json"


def train_current_model(log=print):
    """現時点で利用可能な全履歴データでモデルA'を学習する"""
    dataset = build_dataset()
    log(f"学習データ件数: {len(dataset)}（期間: {dataset['race_date'].min()} 〜 {dataset['race_date'].max()}）")
    return train_model(dataset, FEATURE_COLUMNS_A_ODDS_ADJUSTED, CATEGORICAL_COLUMNS)


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


def score_upcoming_races(model, log=print):
    """未確定レースを予測し、予測勝率・期待値・印・買い目推奨フラグを付けたDataFrameを返す"""
    upcoming = build_upcoming_dataset()
    if len(upcoming) == 0:
        log("予測対象レースがありません（未確定レースなし、または全馬デビュー戦のみ）")
        return upcoming

    X = upcoming[FEATURE_COLUMNS_A_ODDS_ADJUSTED]
    scored = upcoming.copy()
    scored["pred_win_prob"] = model.predict_proba(X)[:, 1]
    scored["expected_value"] = scored["pred_win_prob"] * scored["market_odds"]

    ev_ok = scored["expected_value"] >= EV_THRESHOLD
    odds_ok = scored["market_odds"] <= ODDS_CAP
    class_ok = scored["race_class"].apply(is_class_included) if CLASS_FILTER else True
    scored["is_recommended"] = ev_ok & odds_ok & class_ok

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
            log(f"{row['mark']:3s}{row['horse_number']:4.0f} {row['horse_name']:<14s}"
                f"{raw_rank:>8s}{odds_rank:>10s}"
                f"{row['market_odds']:8.1f}{row['pred_win_prob'] * 100:8.2f}%{row['expected_value']:7.2f}"
                f"  {'買い' if row['is_recommended'] else ''}")

    recommended = scored[scored["is_recommended"]].sort_values(
        ["race_id", "expected_value"], ascending=[True, False]
    )
    log("")
    log("=" * 70)
    log(f"買い目推奨: {len(recommended)}点  "
        f"（EV>={EV_THRESHOLD}・オッズ上限{ODDS_CAP}倍・"
        f"{'1勝クラス以上限定' if CLASS_FILTER else '全クラス'}）")
    log("=" * 70)
    for _, row in recommended.iterrows():
        log(f"  {row['race_date']} {row['course']}{row['round']:.0f}R "
            f"{row['horse_number']:.0f}番 {row['horse_name']}"
            f"（オッズ{row['market_odds']:.1f}倍 EV{row['expected_value']:.2f}）")


def export_predictions(scored, output_path=PREDICTIONS_OUTPUT_PATH):
    """予測結果をJSONに書き出す（Google Sheets連携・GitHub Pagesアプリが読み込む
    共通の中間ファイル。列名はスプレッドシート/アプリ側でそのまま使える形にする）"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(scored) == 0:
        payload = {"races": []}
    else:
        races = []
        for race_id, group in scored.groupby("race_id"):
            info = group.iloc[0]
            display = group.sort_values("odds_adjusted_rank", na_position="last")

            horses = []
            for _, row in display.iterrows():
                horses.append({
                    "mark": row["mark"],
                    "horse_number": int(row["horse_number"]),
                    "horse_name": row["horse_name"],
                    "raw_rank": None if pd.isna(row["raw_rank"]) else int(row["raw_rank"]),
                    "odds_adjusted_rank": None if pd.isna(row["odds_adjusted_rank"]) else int(row["odds_adjusted_rank"]),
                    "market_odds": None if pd.isna(row["market_odds"]) else float(row["market_odds"]),
                    "market_popularity": None if pd.isna(row["market_popularity"]) else int(row["market_popularity"]),
                    "pred_win_prob": float(row["pred_win_prob"]),
                    "expected_value": float(row["expected_value"]),
                    "is_recommended": bool(row["is_recommended"]),
                })

            races.append({
                "race_id": race_id,
                "race_date": info["race_date"],
                "course": info["course"],
                "round": int(info["round"]),
                "surface": info["surface"],
                "distance": int(info["distance"]),
                "race_class": info["race_class"],
                "horses": horses,
            })

        payload = {
            "generated_at": pd.Timestamp.now().isoformat(),
            "settings": {
                "ev_threshold": EV_THRESHOLD,
                "odds_cap": ODDS_CAP,
                "class_filter": CLASS_FILTER,
            },
            "races": races,
        }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return output_path


def main():
    print("=" * 70)
    print("dsk_Project")
    print("predict_race: Stage3確定基準での予想・購入判定")
    print(f"モデルA'（overall_score+odds_adjusted_score+市場情報）  "
          f"EV>={EV_THRESHOLD}  オッズ上限{ODDS_CAP}倍  "
          f"{'1勝クラス以上限定' if CLASS_FILTER else '全クラス'}")
    print("=" * 70)

    model = train_current_model()
    scored = score_upcoming_races(model)
    print_report(scored)

    output_path = export_predictions(scored)
    print()
    print(f"予測結果を出力: {output_path}")


if __name__ == "__main__":
    main()
