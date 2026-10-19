"""
dsk_Project
condition_weights（条件別重み）vs 均等配分の比較
Version 0.1

overall_scoreは8項目それぞれを「レース内Min-Max正規化 × condition_weights
テーブルの条件別重み（course, surface, distanceの組み合わせごと、360パターン。
旧「競馬予想2」のcondition_masterシート相当）」で計算し、合算したもの
（feature_engineering/overall_score.py）。本スクリプトは、この条件別重み付けが
実際にAUC・シャープレシオの改善に寄与しているかを、「重みを一律1（＝全項目
均等配分）にした場合」と比較して検証する。

均等配分版overall_score_equalの作り方:
  agari_power等の7項目（脚質力を除く）は、格納値 = 正規化値 × condition_weights
  の該当セルという決定的な式なので、condition_weightsテーブルを
  (course, surface, distance)でJOINし直し、「格納値 ÷ 該当の重み」で
  正規化値（＝重み=1のときの値）を逆算できる。

  脚質力（kyakushitsu_power）だけは例外で、正規化ステップが無く「格納値＝
  該当スタイル（逃げ/先行/差し/追込）の条件別重みそのもの」という設計
  （feature_engineering/kyakushitsu_power.py）。値と重みが不可分で「重み=1に
  した場合の正規化値」を逆算する術が無いため、この項目だけは条件別重み版・
  均等配分版の両方で同じ値（現状の格納値）を使う（脚質力の寄与は両モデルで
  同一に保たれるため、この項目に関しては公平な比較にならない点に注意。
  ①のitem_importance_analysis.pyの結果、脚質力は8項目中で最も寄与が小さい
  （8項目合計の4.5%）ため、この限定が比較全体の結論に与える影響は小さいと
  考えられる）。

  overall_score_equal = kyakushitsu_power(現状のまま)
                       + Σ(agari_power等7項目の格納値 ÷ 該当重み)（重み0/欠損はNaN→0扱い）
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import (
    STAKE, compute_sharpe_ratio, fetch_win_payouts, is_class_included,
    run_model_backtest, simulate_per_fold,
)
from ai.build_dataset import CATEGORICAL_COLUMNS, MARKET_COLUMNS
from ai.train_model import train_model, evaluate_model, split_by_date
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager

# 検証で使う購入判定基準（README「Stage3としての基準値」＝運用中のSランク基準）
EV_THRESHOLD = 1.4
ODDS_CAP = 30
CLASS_FILTER = True

# agari_power等の7項目 -> condition_weightsの列名（脚質力は逆算不可のため除外）
ITEM_TO_WEIGHT_COLUMN = {
    "agari_power": "weight_agari",
    "jockey_power": "weight_jockey",
    "distance_power": "weight_distance",
    "turn_power": "weight_turn",
    "stability_power": "weight_stability",
    "pedigree_power": "weight_pedigree",
    "trainer_power": "weight_trainer",
}
POWER_ITEM_COLUMNS = list(ITEM_TO_WEIGHT_COLUMN) + ["kyakushitsu_power"]

BASE_FEATURE_COLUMNS_NO_SCORE = CATEGORICAL_COLUMNS + [
    "round", "distance", "horse_number", "frame_number", "weight",
]
FEATURE_COLUMNS_WEIGHTED = BASE_FEATURE_COLUMNS_NO_SCORE + ["overall_score"] + MARKET_COLUMNS
FEATURE_COLUMNS_EQUAL = BASE_FEATURE_COLUMNS_NO_SCORE + ["overall_score_equal"] + MARKET_COLUMNS

NUMERIC_COLUMNS = [
    "round", "distance", "horse_number", "frame_number", "weight",
    "market_odds", "market_popularity", "overall_score",
] + POWER_ITEM_COLUMNS

QUERY = f"""
    SELECT
        e.race_id, e.horse_id, h.horse_name, r.race_date,
        r.course, r.surface, r.track_condition, r.race_class, r.direction, r.age_condition,
        r.round, r.distance,
        e.horse_number, e.frame_number, e.sex_age, e.weight,
        e.odds AS market_odds, e.popularity AS market_popularity,
        f.overall_score,
        {", ".join(f"f.{col}" for col in POWER_ITEM_COLUMNS)},
        res.finish_position
    FROM entries e
    JOIN races r ON r.race_id = e.race_id
    JOIN horses h ON h.horse_id = e.horse_id
    JOIN features f ON f.race_id = e.race_id AND f.horse_id = e.horse_id
    LEFT JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
    ORDER BY r.race_date, e.race_id, e.horse_number
"""

CONDITION_WEIGHTS_QUERY = """
    SELECT course, surface, distance,
           weight_agari, weight_jockey, weight_distance, weight_turn,
           weight_stability, weight_pedigree, weight_trainer
    FROM condition_weights
"""


def build_equal_weight_dataset(db=None):
    if db is None:
        db = DatabaseManager()

    conn = db.connect()
    df = pd.read_sql_query(QUERY, conn)
    weights_df = pd.read_sql_query(CONDITION_WEIGHTS_QUERY, conn)
    conn.close()

    df = df[df["finish_position"].notna()].copy()
    race_all_debut = df.groupby("race_id")["stability_power"].transform(lambda s: s.isna().all())
    df = df[~race_all_debut].copy()

    df["label"] = (df["finish_position"] == 1).astype(int)
    df = df.drop(columns=["finish_position"])

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.merge(weights_df, on=["course", "surface", "distance"], how="left")

    unweighted_cols = []
    for item_col, weight_col in ITEM_TO_WEIGHT_COLUMN.items():
        safe_weight = df[weight_col].replace(0, np.nan)
        unweighted_col = f"{item_col}_unweighted"
        df[unweighted_col] = df[item_col] / safe_weight
        unweighted_cols.append(unweighted_col)

    df["overall_score_equal"] = df[unweighted_cols].fillna(0).sum(axis=1) + df["kyakushitsu_power"].fillna(0)

    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")

    return df


def run_auc_comparison(dataset, folds, log=print):
    """モデルA'（condition_weights版）とモデルA'_equal（均等配分版）の
    ウォークフォワードAUC/LogLossを比較する（ai/walk_forward_backtest.pyと
    同じ考え方だが、この検証用に必要な列だけの軽量なdatasetを使い回すため
    直接ここに実装する）"""
    results = {}
    for label, feature_columns in [("condition_weights版", FEATURE_COLUMNS_WEIGHTED),
                                    ("均等配分版", FEATURE_COLUMNS_EQUAL)]:
        fold_aucs = []
        fold_loglosses = []
        for test_start, test_end in folds:
            train_df, test_df = split_by_date(dataset, test_start, test_end)
            if len(train_df) == 0 or len(test_df) == 0:
                continue
            model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
            _proba, _y, auc, logloss = evaluate_model(model, test_df, feature_columns)
            fold_aucs.append(auc)
            fold_loglosses.append(logloss)
            log(f"  [{label}] {test_start}〜{test_end}: AUC={auc:.4f} LogLoss={logloss:.4f}")
        results[label] = {
            "AUC(平均)": float(np.mean(fold_aucs)) if fold_aucs else float("nan"),
            "LogLoss(平均)": float(np.mean(fold_loglosses)) if fold_loglosses else float("nan"),
        }
    return results


def run_sharpe_comparison(dataset, folds, win_payouts, log=print):
    """モデルA'（condition_weights版）とモデルA'_equal（均等配分版）の
    シャープレシオ・回収率を、運用中のSランク購入基準（EV>=1.4・オッズ上限
    30倍・1勝クラス以上）で比較する"""
    results = {}
    for label, feature_columns in [("condition_weights版", FEATURE_COLUMNS_WEIGHTED),
                                    ("均等配分版", FEATURE_COLUMNS_EQUAL)]:
        oos_df = run_model_backtest(dataset, folds, feature_columns, CATEGORICAL_COLUMNS, win_payouts, label, log=log)
        if len(oos_df) == 0:
            results[label] = None
            continue
        fold_results = simulate_per_fold(
            oos_df, folds, EV_THRESHOLD, odds_cap=ODDS_CAP, class_filter=CLASS_FILTER,
        )
        recoveries = [r["回収率(%)"] for r in fold_results]
        sharpe = compute_sharpe_ratio(recoveries)
        valid = [r for r in recoveries if r is not None and not np.isnan(r)]
        results[label] = {
            "フォールド別回収率(%)": recoveries,
            "回収率(平均,%)": float(np.mean(valid)) if valid else float("nan"),
            "回収率(標準偏差)": float(np.std(valid, ddof=1)) if len(valid) >= 2 else float("nan"),
            "シャープレシオ": sharpe,
        }
    return results


def main():
    print("=" * 70)
    print("dsk_Project")
    print("condition_weights（条件別重み・360パターン）vs 均等配分の比較")
    print(f"購入判定基準（回収率・シャープレシオ算出用）: EV>=1.4 オッズ上限30倍 1勝クラス以上限定")
    print("=" * 70)

    dataset = build_equal_weight_dataset()
    print(f"データセット件数: {len(dataset)}")
    print(f"期間: {dataset['race_date'].min()} 〜 {dataset['race_date'].max()}")

    folds = generate_folds(dataset)
    print(f"フォールド数: {len(folds)}")

    print()
    print("--- AUC・LogLossの比較（ウォークフォワード） ---")
    auc_results = run_auc_comparison(dataset, folds)
    for label, r in auc_results.items():
        print(f"[{label}] AUC(平均)={r['AUC(平均)']:.4f}  LogLoss(平均)={r['LogLoss(平均)']:.4f}")

    print()
    print("--- 回収率・シャープレシオの比較 ---")
    win_payouts = fetch_win_payouts()
    sharpe_results = run_sharpe_comparison(dataset, folds, win_payouts)
    for label, r in sharpe_results.items():
        if r is None:
            print(f"[{label}] データ不足のため算出不可")
            continue
        print(f"[{label}] 回収率(平均)={r['回収率(平均,%)']:.1f}%  "
              f"標準偏差={r['回収率(標準偏差)']:.1f}  シャープレシオ={r['シャープレシオ']:.3f}")
        print(f"          フォールド別回収率(%): {[round(v, 1) if v is not None and not np.isnan(v) else None for v in r['フォールド別回収率(%)']]}")


if __name__ == "__main__":
    main()
