"""
dsk_Project
Stage2: ウォークフォワード検証（市場情報あり/なしモデルの比較）
Version 0.1

PROJECT_EVのai/walk_forward_backtest.py・ai/compare_market_odds.pyと同じ
設計思想（日付分割・拡大窓・複数フォールド）を踏襲しつつ、本プロジェクトの
主眼である「overall_score（8項目合成スコア）にオッズ・人気を明示的に
加えるとAUC・LogLossがどう変わるか」（開発指示書5節の検証すべき問い1）を
確認するために作成した。

フォールド定義（PROJECT_EVは固定の月次カレンダー日付をハードコードしていたが、
dsk_Projectはまだデータ収集期間が短く固定日付では意味を持たないため、
実際にDBにあるrace_dateの範囲を動的にN+1個の期間へ均等分割し、最初の期間を
学習専用ウォームアップ、残りのN個をテストフォールドとする。各フォールドの
学習データは「そのテスト期間の開始日より前の全データ」（拡大窓）を使う。
データが十分に蓄積したら、PROJECT_EVのように月次等の固定フォールドへ
切り替えることを検討する）。

このスクリプトはAUC・LogLossの比較まで（Stage2の役割）に留め、期待値
（EV）に基づく購入判定・回収率シミュレーションはStage3の役割として
別途実装する（開発指示書4節参照）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.build_dataset import BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, FEATURE_COLUMNS, build_dataset
from ai.train_model import evaluate_model, split_by_date, train_model

N_FOLDS = 8


def generate_folds(dataset, n_folds=N_FOLDS):
    """race_dateのユニーク値をn_folds+1個の区間へ均等分割し、最初の区間を
    学習専用ウォームアップ、残りのn_folds個を(test_start, test_end)として返す"""
    dates = sorted(dataset["race_date"].unique())

    if len(dates) < n_folds + 1:
        raise ValueError(
            f"フォールド数={n_folds}に対してデータの日数（{len(dates)}日）が不足しています。"
            f"n_foldsを減らすか、データを追加収集してください。"
        )

    chunks = np.array_split(dates, n_folds + 1)
    return [(chunk[0], chunk[-1]) for chunk in chunks[1:]]


def score_fold(dataset, test_start, test_end, feature_columns, categorical_columns):
    """1フォールド分の学習・予測を行い、AUC・LogLossとテスト結果を返す"""
    train_df, test_df = split_by_date(dataset, test_start, test_end)

    if len(train_df) == 0 or len(test_df) == 0:
        return None

    model = train_model(train_df, feature_columns, categorical_columns)
    proba, y_test, auc, logloss = evaluate_model(model, test_df, feature_columns)

    return {
        "n_train": len(train_df),
        "n_test": len(test_df),
        "auc": auc,
        "logloss": logloss,
        "proba": proba,
        "y_test": y_test.reset_index(drop=True),
    }


def run_walk_forward(dataset, folds, feature_columns, categorical_columns, model_label, log=print):
    """全フォールドを順に実行し、フォールド別結果一覧を返す（全体AUC・LogLossは
    全フォールドの予測を結合してから再計算する。単純平均だとフォールドごとの
    件数差を無視してしまうため）"""
    from sklearn.metrics import log_loss, roc_auc_score

    fold_rows = []
    all_proba = []
    all_y = []

    for test_start, test_end in folds:
        outcome = score_fold(dataset, test_start, test_end, feature_columns, categorical_columns)
        if outcome is None:
            log(f"  [{model_label}] {test_start}〜{test_end}: 学習/テストデータなし、スキップ")
            continue

        fold_rows.append({
            "テスト期間": f"{test_start}〜{test_end}",
            "学習件数": outcome["n_train"],
            "テスト件数": outcome["n_test"],
            "AUC": outcome["auc"],
            "LogLoss": outcome["logloss"],
        })
        log(f"  [{model_label}] {test_start}〜{test_end}: "
            f"学習={outcome['n_train']} テスト={outcome['n_test']} "
            f"AUC={outcome['auc']:.4f} LogLoss={outcome['logloss']:.4f}")

        all_proba.append(outcome["proba"])
        all_y.append(outcome["y_test"])

    if not fold_rows:
        return pd.DataFrame(fold_rows), None

    combined_proba = np.concatenate(all_proba)
    combined_y = pd.concat(all_y, ignore_index=True)

    overall = {
        "n_test_total": len(combined_y),
        "auc": roc_auc_score(combined_y, combined_proba),
        "logloss": log_loss(combined_y, combined_proba),
    }

    return pd.DataFrame(fold_rows), overall


def main():
    print("=" * 40)
    print("dsk_Project")
    print("Stage2 ウォークフォワード検証: 市場情報あり/なしモデル比較")
    print("=" * 40)

    dataset = build_dataset()
    print(f"データセット件数: {len(dataset)}  期間: {dataset['race_date'].min()} 〜 {dataset['race_date'].max()}")

    folds = generate_folds(dataset, N_FOLDS)
    print(f"フォールド数: {len(folds)}")
    for test_start, test_end in folds:
        print(f"  テスト期間: {test_start} 〜 {test_end}")

    print()
    print("--- モデルA（市場情報あり: overall_score + market_odds + market_popularity） ---")
    fold_df_a, overall_a = run_walk_forward(
        dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, "モデルA"
    )

    print()
    print("--- モデルB（市場情報除外: overall_scoreのみ + レース条件） ---")
    fold_df_b, overall_b = run_walk_forward(
        dataset, folds, BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, "モデルB"
    )

    print()
    print("=== フォールド別結果 ===")
    if not fold_df_a.empty:
        print("モデルA（市場情報あり）:")
        print(fold_df_a.to_string(index=False))
    if not fold_df_b.empty:
        print()
        print("モデルB（市場情報除外）:")
        print(fold_df_b.to_string(index=False))

    print()
    print("=== 全フォールド合算結果 ===")
    if overall_a and overall_b:
        print(f"{'':30s}{'テスト件数':>12s}{'AUC':>10s}{'LogLoss':>10s}")
        print(f"{'モデルA(市場情報あり)':30s}{overall_a['n_test_total']:12d}"
              f"{overall_a['auc']:10.4f}{overall_a['logloss']:10.4f}")
        print(f"{'モデルB(市場情報除外)':30s}{overall_b['n_test_total']:12d}"
              f"{overall_b['auc']:10.4f}{overall_b['logloss']:10.4f}")
        print()
        print(f"AUC差(A-B) = {overall_a['auc'] - overall_b['auc']:+.4f}   "
              f"（プラスなら市場情報を加えた方が的中順位付けの精度が高い）")
        print(f"LogLoss差(B-A) = {overall_b['logloss'] - overall_a['logloss']:+.4f}   "
              f"（プラスなら市場情報を加えた方が予測確率の較正が良い）")
    else:
        print("いずれかのモデルで有効なフォールドが無かったため比較できません。")

    if len(dataset["race_date"].unique()) < 60:
        print()
        print("注意: 現時点のデータ期間はまだ短く（1〜2ヶ月程度）、フォールドあたりの")
        print("件数が少ないため、AUC・LogLossの差は誤差の範囲である可能性が高い。")
        print("データ収集期間を伸ばしてから再検証することを推奨する。")


if __name__ == "__main__":
    main()
