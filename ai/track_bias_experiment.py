"""
dsk_Project
①トラックバイアス補正の検証（本番反映前の重要度・AUC・シャープレシオ確認）
Version 0.1

最終版(ハイブリッド)のバイアスデータ設計を参考に、「同日・同競馬場で既に確定した
レース結果から、先行有利/内枠有利の傾向をリアルタイムに集計し、まだ確定していない
残りレースの特徴量として使う」トラックバイアス補正を試作する。

pace_bias.pyの元々のTODO（出走馬の脚質構成からハイペース/スローペースを予想する
「ペース予想」）とは異なる切り口だが、feature_engineering/overall_score.pyの
`overall_score = Σ8項目 × (1 + pace_bias_adjustment)`という乗算補正の受け皿は
そのまま使える設計になっている。

リーケージ防止: 同日・同競馬場・自分より前のラウンド（round）の確定済み結果だけを
集計対象にする（本番でも、その日の後のレースを予測する時点で先行レースの結果は
既に確定しているため、同じ条件で計算できる）。1レース目はまだ同日データが無いため
補正値は0（中立）になる。

本番のfeature_engineering/pace_bias.py・overall_score.pyは変更せず、この
スクリプト内で独立に特徴量を計算し、既存モデルに追加した場合の重要度・
ウォークフォワードAUC・シャープレシオへの影響を確認するだけに留める。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import compute_sharpe_ratio, is_class_included
from ai.build_dataset import (
    CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, _coerce_numeric_dtypes, build_dataset,
)
from ai.train_model import evaluate_model, feature_importance_report, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager
from feature_engineering.kyakushitsu_power import parse_passing

FRONT_THRESHOLD = 0.4  # 相対位置がこれ以下なら「先行集団」とみなす（逃げ・先行相当）
MIN_PRIOR_SAMPLES = 6  # 同日の集計対象がこれ未満なら中立(0)扱い
S_EV_THRESHOLD, S_ODDS_CAP = 1.4, 30


def load_raw_race_data(db=None):
    if db is None:
        db = DatabaseManager()
    rows = db.fetchall("""
        SELECT r.race_id, r.race_date, r.course, r.round,
               e.horse_id, e.horse_number,
               res.finish_position, res.passing,
               (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = r.race_id) AS field_size
        FROM races r
        JOIN entries e ON e.race_id = r.race_id
        LEFT JOIN results res ON res.race_id = r.race_id AND res.horse_id = e.horse_id
        ORDER BY r.course, r.race_date, r.round
    """)
    return pd.DataFrame(rows, columns=[
        "race_id", "race_date", "course", "round", "horse_id", "horse_number",
        "finish_position", "passing", "field_size",
    ])


def compute_bias_features(raw):
    """レースID単位のbias_front_runner_score・bias_inside_post_scoreを返す
    （同日・同競馬場・自分より前のroundの確定済み結果だけを使う、リーケージ無し）"""
    raw = raw.dropna(subset=["finish_position", "field_size"]).copy()
    raw = raw[raw["field_size"] > 1]

    def relative_position(row):
        if pd.isna(row["passing"]):
            return None
        positions = parse_passing(row["passing"])
        if not positions:
            return None
        return (positions[-1] - 1) / (row["field_size"] - 1)

    raw["relative_position"] = raw.apply(relative_position, axis=1)
    raw["post_pct"] = (raw["horse_number"] - 1) / (raw["field_size"] - 1)
    raw["place_threshold"] = raw["field_size"].apply(lambda n: 3 if n >= 8 else 2)
    raw["is_placed"] = raw["finish_position"] <= raw["place_threshold"]

    raw["is_front"] = raw["relative_position"] <= FRONT_THRESHOLD
    raw["is_inside"] = raw["post_pct"] <= 0.5

    # ラウンド単位で集計（同レース内の複数馬をまとめる）
    round_stats = raw.groupby(["course", "race_date", "round"]).apply(
        lambda g: pd.Series({
            "n_front": (g["relative_position"].notna() & g["is_front"]).sum(),
            "n_front_placed": (g["relative_position"].notna() & g["is_front"] & g["is_placed"]).sum(),
            "n_back": (g["relative_position"].notna() & ~g["is_front"]).sum(),
            "n_back_placed": (g["relative_position"].notna() & ~g["is_front"] & g["is_placed"]).sum(),
            "n_inside": g["is_inside"].sum(),
            "n_inside_placed": (g["is_inside"] & g["is_placed"]).sum(),
            "n_outside": (~g["is_inside"]).sum(),
            "n_outside_placed": (~g["is_inside"] & g["is_placed"]).sum(),
        }),
        include_groups=False,
    ).reset_index()

    round_stats = round_stats.sort_values(["course", "race_date", "round"])
    group_cols = ["course", "race_date"]
    cum_cols = ["n_front", "n_front_placed", "n_back", "n_back_placed",
                "n_inside", "n_inside_placed", "n_outside", "n_outside_placed"]
    for col in cum_cols:
        # 自分自身のroundを含めない「前のroundまでの累積」（shift(1)）
        round_stats[f"cum_{col}"] = (
            round_stats.groupby(group_cols)[col].cumsum() - round_stats[col]
        )

    front_rate = round_stats["cum_n_front_placed"] / round_stats["cum_n_front"].replace(0, np.nan)
    back_rate = round_stats["cum_n_back_placed"] / round_stats["cum_n_back"].replace(0, np.nan)
    inside_rate = round_stats["cum_n_inside_placed"] / round_stats["cum_n_inside"].replace(0, np.nan)
    outside_rate = round_stats["cum_n_outside_placed"] / round_stats["cum_n_outside"].replace(0, np.nan)

    enough_style = (round_stats["cum_n_front"] + round_stats["cum_n_back"]) >= MIN_PRIOR_SAMPLES
    enough_post = (round_stats["cum_n_inside"] + round_stats["cum_n_outside"]) >= MIN_PRIOR_SAMPLES

    round_stats["bias_front_runner_score"] = np.where(
        enough_style, (front_rate - back_rate).fillna(0), 0.0
    )
    round_stats["bias_inside_post_score"] = np.where(
        enough_post, (inside_rate - outside_rate).fillna(0), 0.0
    )

    race_bias = raw[["race_id", "course", "race_date", "round"]].drop_duplicates()
    race_bias = race_bias.merge(
        round_stats[["course", "race_date", "round", "bias_front_runner_score", "bias_inside_post_score"]],
        on=["course", "race_date", "round"], how="left",
    )
    return race_bias[["race_id", "bias_front_runner_score", "bias_inside_post_score"]]


def main():
    print("=" * 70)
    print("dsk_Project")
    print("①トラックバイアス補正の検証")
    print("=" * 70)

    raw = load_raw_race_data()
    print(f"生データ件数: {len(raw)}")
    race_bias = compute_bias_features(raw)
    print(f"バイアス値を計算したレース数: {len(race_bias)}")
    print(f"bias_front_runner_score 分布:\n{race_bias['bias_front_runner_score'].describe()}")
    print(f"\nbias_inside_post_score 分布:\n{race_bias['bias_inside_post_score'].describe()}")
    print(f"\n中立(0)扱いになったレース割合: "
          f"先行={  (race_bias['bias_front_runner_score']==0).mean()*100:.1f}%  "
          f"内枠={(race_bias['bias_inside_post_score']==0).mean()*100:.1f}%")

    dataset = build_dataset()
    dataset = dataset.merge(race_bias, on="race_id", how="left")
    dataset["bias_front_runner_score"] = dataset["bias_front_runner_score"].fillna(0)
    dataset["bias_inside_post_score"] = dataset["bias_inside_post_score"].fillna(0)

    feature_columns_with_bias = FEATURE_COLUMNS_A_ODDS_ADJUSTED + ["bias_front_runner_score", "bias_inside_post_score"]

    print()
    print("=" * 70)
    print("重要度分析（バイアス特徴量を追加したフルデータ学習）")
    print("=" * 70)
    model_full = train_model(dataset, feature_columns_with_bias, CATEGORICAL_COLUMNS)
    importances = feature_importance_report(model_full, feature_columns_with_bias, top_n=len(feature_columns_with_bias))
    total = importances.sum()
    for col in ["bias_front_runner_score", "bias_inside_post_score"]:
        val = importances.get(col, 0)
        rank = list(importances.index).index(col) + 1 if col in importances.index else None
        print(f"  {col}: 重要度={val:,.1f}  構成比={val/total*100:.3f}%  順位={rank}/{len(feature_columns_with_bias)}")

    print()
    print("=" * 70)
    print("ウォークフォワードAUC・Sランクのシャープレシオ比較（バイアス特徴量あり/なし）")
    print("=" * 70)
    folds = generate_folds(dataset)

    db = DatabaseManager()
    win_payouts = {}
    for race_id, horse_number, payout_yen in db.fetchall(
        "SELECT race_id, combination, payout_yen FROM payouts WHERE bet_type='単勝'"
    ):
        try:
            win_payouts[(race_id, int(horse_number))] = payout_yen
        except ValueError:
            pass

    for label, feature_columns in [("バイアス無し（現行）", FEATURE_COLUMNS_A_ODDS_ADJUSTED),
                                    ("バイアス有り", feature_columns_with_bias)]:
        aucs = []
        fold_recoveries = []
        for test_start, test_end in folds:
            train_df, test_df = split_by_date(dataset, test_start, test_end)
            if len(train_df) == 0 or len(test_df) == 0:
                continue
            model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
            proba, y_test, auc, _logloss = evaluate_model(model, test_df, feature_columns)
            aucs.append(auc)

            result = test_df.loc[:, ["race_id", "horse_number", "market_odds", "race_class"]].copy()
            result["label"] = y_test.values
            result["pred_win_prob"] = proba
            result["expected_value"] = result["pred_win_prob"] * result["market_odds"]

            candidates = result[
                (result["expected_value"] >= S_EV_THRESHOLD)
                & (result["market_odds"] <= S_ODDS_CAP)
                & (result["race_class"].apply(is_class_included))
            ]
            n_buys = len(candidates)
            if n_buys == 0:
                fold_recoveries.append(float("nan"))
                continue
            hits = candidates[candidates["label"] == 1]
            total_stake = n_buys * 100
            total_payout = sum(
                win_payouts.get((r["race_id"], int(r["horse_number"])), 0) / 100 * 100
                for _, r in hits.iterrows()
            )
            fold_recoveries.append(total_payout / total_stake * 100)

        valid = [r for r in fold_recoveries if not np.isnan(r)]
        sharpe = compute_sharpe_ratio(valid)
        print(f"\n  [{label}]")
        print(f"    フォールド別AUC: {[round(a,4) for a in aucs]}")
        print(f"    平均AUC: {np.mean(aucs):.4f}")
        print(f"    フォールド別回収率(Sランク): {[round(r,1) if not np.isnan(r) else None for r in fold_recoveries]}")
        print(f"    回収率平均={np.mean(valid):.1f}%  標準偏差={np.std(valid, ddof=1):.1f}  シャープレシオ={sharpe:.3f}")


if __name__ == "__main__":
    main()
