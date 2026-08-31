"""
dsk_Project
Stage2: LightGBMモデル学習
Version 0.1

ai/build_dataset.py で作成したデータセットを日付で学習用・テスト用に分割し、
LightGBM（二値分類、1着=1）でモデルを学習・評価する。PROJECT_EVの
ai/train_model.pyと同じ設計（日付分割・pandas category dtypeのまま
LightGBMへ渡す）を踏襲。

データ分割ルール（データリーケージ防止。PROJECT_EVと同じ理由）:
  ランダム分割ではなく日付で分割する。overall_scoreの元になる8項目は
  「対象レースより前の通算成績」であり日付をまたいで少しずつ値が変わる
  ため、ランダム分割だと学習・テストが同じ馬・血統・騎手の近い時期の
  レコード同士になりやすく、テスト精度が不当に高く出てしまう。
"""

import sys
from pathlib import Path

import joblib
import lightgbm as lgb
from sklearn.metrics import log_loss, roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.build_dataset import BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, FEATURE_COLUMNS, build_dataset

MODEL_DIR = PROJECT_ROOT / "model"
MODEL_WITH_ODDS_PATH = MODEL_DIR / "lightgbm_win_model_with_odds.pkl"
MODEL_NO_ODDS_PATH = MODEL_DIR / "lightgbm_win_model_no_odds.pkl"

# train_model() のデフォルトLightGBMハイパーパラメータ（PROJECT_EVの
# DEFAULT_LGBM_PARAMSと同じ値を初期値に採用。dsk_Project独自のチューニングは
# データが十分に蓄積してから行う）
DEFAULT_LGBM_PARAMS = {
    "n_estimators": 300,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "random_state": 42,
    "importance_type": "gain",
    "verbosity": -1,
}


def split_by_date(df, test_start, test_end):
    """日付で学習用・テスト用に分割する（ランダム分割はしない）"""
    train_df = df[df["race_date"] < test_start].copy()
    test_df = df[(df["race_date"] >= test_start) & (df["race_date"] <= test_end)].copy()
    return train_df, test_df


def train_model(train_df, feature_columns=None, categorical_columns=None, lgbm_params=None):
    """学習データでLightGBMモデルを学習する。
    feature_columns/categorical_columnsを省略した場合はFEATURE_COLUMNS
    （市場情報あり）を使う"""
    if feature_columns is None:
        feature_columns = FEATURE_COLUMNS
    if categorical_columns is None:
        categorical_columns = CATEGORICAL_COLUMNS
    if lgbm_params is None:
        lgbm_params = DEFAULT_LGBM_PARAMS

    X_train = train_df[feature_columns]
    y_train = train_df["label"]

    model = lgb.LGBMClassifier(
        objective="binary",
        **lgbm_params,
    )

    model.fit(
        X_train, y_train,
        categorical_feature=[c for c in categorical_columns if c in feature_columns],
    )

    return model


def evaluate_model(model, test_df, feature_columns=None):
    """テストデータでAUC・LogLossを計算する"""
    if feature_columns is None:
        feature_columns = FEATURE_COLUMNS

    X_test = test_df[feature_columns]
    y_test = test_df["label"]

    proba_test = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, proba_test)
    logloss = log_loss(y_test, proba_test)

    return proba_test, y_test, auc, logloss


def feature_importance_report(model, feature_columns=None, top_n=15):
    """特徴量重要度（gainベース）の上位top_n件を返す"""
    import pandas as pd

    if feature_columns is None:
        feature_columns = FEATURE_COLUMNS

    importances = pd.Series(model.feature_importances_, index=feature_columns)
    return importances.sort_values(ascending=False).head(top_n)


def main():
    print("=" * 40)
    print("dsk_Project")
    print("train_model 実行")
    print("=" * 40)

    dataset = build_dataset()

    dates = sorted(dataset["race_date"].unique())
    if len(dates) < 2:
        print("学習・テストを分割するには最低2日分のデータが必要です。")
        return

    # 直近日を1テスト日として使う（データが十分蓄積したらai/walk_forward_backtest.py
    # の複数フォールド検証を使うこと。本スクリプトは単発の学習・保存が目的）
    test_start = test_end = dates[-1]
    train_df, test_df = split_by_date(dataset, test_start, test_end)

    print()
    print(f"学習データ期間: {train_df['race_date'].min()} 〜 {train_df['race_date'].max()}")
    print(f"テストデータ期間: {test_start} 〜 {test_end}")
    print(f"学習データ件数: {len(train_df)}（陽性率 {train_df['label'].mean() * 100:.2f}%）")
    print(f"テストデータ件数: {len(test_df)}（陽性率 {test_df['label'].mean() * 100:.2f}%）")

    print()
    print("--- モデルA（市場情報あり） ---")
    model_with_odds = train_model(train_df, FEATURE_COLUMNS, CATEGORICAL_COLUMNS)
    _, _, auc_a, logloss_a = evaluate_model(model_with_odds, test_df, FEATURE_COLUMNS)
    print(f"AUC={auc_a:.4f}  LogLoss={logloss_a:.4f}")
    print(feature_importance_report(model_with_odds, FEATURE_COLUMNS, top_n=15))

    print()
    print("--- モデルB（市場情報除外） ---")
    model_no_odds = train_model(train_df, BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS)
    _, _, auc_b, logloss_b = evaluate_model(model_no_odds, test_df, BASE_FEATURE_COLUMNS)
    print(f"AUC={auc_b:.4f}  LogLoss={logloss_b:.4f}")
    print(feature_importance_report(model_no_odds, BASE_FEATURE_COLUMNS, top_n=15))

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model_with_odds, MODEL_WITH_ODDS_PATH)
    joblib.dump(model_no_odds, MODEL_NO_ODDS_PATH)
    print()
    print(f"モデル保存先: {MODEL_WITH_ODDS_PATH}")
    print(f"モデル保存先: {MODEL_NO_ODDS_PATH}")


if __name__ == "__main__":
    main()
