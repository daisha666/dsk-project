"""
dsk_Project
モデル自信度で絞り込んだ場合の複勝的中率の検証
Version 0.1

dsk_Projectのモデルは単勝（1着=1）を目的変数に学習しているが、「的中率目標」が
複勝（3着以内）を指している可能性を踏まえ、pred_win_prob（1着確率の予測値）で
馬を絞り込んだ場合、実際の複勝的中率（3着以内率。8頭未満は2着以内）が
どこまで上げられるかを検証する。

学習に使ったデータで確信度を測ると過大評価になる（リーケージ）ため、
ai/walk_forward_backtest.pyと同じウォークフォワード（8フォールド）でOOS
（Out-of-Sample）予測を作り、そのOOS予測だけを対象に集計する。

絞り込み方式:
  (A) レース内順位（pred_win_prob降順の上位K頭。K=1〜5）: 「上位K頭を買う」に相当。
      的中率はK頭の「購入頭数」に対する的中頭数の割合（1頭でも当たれば良いという
      レース単位の的中率ではない。購入判断の実態に合わせるため）
  (B) 予測確率の絶対しきい値（pred_win_prob >= X）: 頭数に関わらず「自信がある馬」
      だけを買う場合に相当。しきい値を上げるほど購入頭数が絞られ確信度が上がる
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.build_dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.train_model import evaluate_model, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds

TOPK_VALUES = [1, 2, 3, 4, 5]
PROB_THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]


def build_oos_predictions(dataset, folds, feature_columns, log=print):
    """ウォークフォワードでOOS予測（race_id・horse_number・pred_win_prob・
    finish_position・field_size）を作る"""
    all_results = []
    for test_start, test_end in folds:
        train_df, test_df = split_by_date(dataset, test_start, test_end)
        if len(train_df) == 0 or len(test_df) == 0:
            continue
        model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
        proba, y_test, auc, logloss = evaluate_model(model, test_df, feature_columns)
        log(f"  {test_start}〜{test_end}: テスト={len(test_df)} AUC(単勝)={auc:.4f}")

        result = test_df.loc[:, ["race_id", "horse_number", "label"]].copy()
        result["pred_win_prob"] = proba
        all_results.append(result)

    return pd.concat(all_results, ignore_index=True)


def attach_place_info(oos_df, dataset):
    """finish_position（実際の着順）とfield_size（頭数）を付与し、
    複勝的中判定（8頭以上なら3着以内、7頭以下なら2着以内）を追加する。
    build_dataset()のQUERYはfinish_positionを特徴量化のために既にlabelへ
    変換して落としているため、別途races/entries/resultsから引き直す"""
    from database.db_manager import DatabaseManager
    db = DatabaseManager()
    rows = db.fetchall("""
        SELECT e.race_id, e.horse_number, res.finish_position,
               (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = e.race_id) AS field_size
        FROM entries e
        JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
        WHERE res.finish_position IS NOT NULL
    """)
    finish_df = pd.DataFrame(rows, columns=["race_id", "horse_number", "finish_position", "field_size"])

    merged = oos_df.merge(finish_df, on=["race_id", "horse_number"], how="left")
    merged["place_threshold"] = merged["field_size"].apply(lambda n: 3 if n >= 8 else 2)
    merged["is_place_hit"] = merged["finish_position"] <= merged["place_threshold"]
    return merged


def summarize(picks):
    n = len(picks)
    if n == 0:
        return {"件数": 0, "複勝的中率(%)": float("nan")}
    hit_rate = picks["is_place_hit"].mean() * 100
    return {"件数": n, "複勝的中率(%)": hit_rate}


def main():
    print("=" * 70)
    print("dsk_Project")
    print("モデル自信度で絞り込んだ場合の複勝的中率の検証（ウォークフォワードOOS）")
    print("=" * 70)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")

    oos_df = build_oos_predictions(dataset, folds, FEATURE_COLUMNS_A_ODDS_ADJUSTED)
    merged = attach_place_info(oos_df, dataset)
    merged = merged.dropna(subset=["finish_position", "field_size"])
    print(f"\n複勝判定可能なOOS予測件数: {len(merged)}")

    baseline = summarize(merged)
    print(f"\nベースライン（絞り込みなし、全馬）: 件数={baseline['件数']:,}  "
          f"複勝的中率={baseline['複勝的中率(%)']:.2f}%")

    print()
    print("--- (A) レース内順位（pred_win_prob降順の上位K頭）で絞り込んだ場合 ---")
    merged["rank_in_race"] = merged.groupby("race_id")["pred_win_prob"].rank(ascending=False, method="first")
    for k in TOPK_VALUES:
        picks = merged[merged["rank_in_race"] <= k]
        s = summarize(picks)
        print(f"  上位{k}頭: 件数={s['件数']:6,}  複勝的中率={s['複勝的中率(%)']:6.2f}%  "
              f"(ベースライン比 +{s['複勝的中率(%)'] - baseline['複勝的中率(%)']:5.2f}pt)")

    print()
    print("--- (B) 予測確率の絶対しきい値（pred_win_prob >= X）で絞り込んだ場合 ---")
    for th in PROB_THRESHOLDS:
        picks = merged[merged["pred_win_prob"] >= th]
        s = summarize(picks)
        if s["件数"] == 0:
            print(f"  pred_win_prob>={th:.2f}: 件数=0")
            continue
        print(f"  pred_win_prob>={th:.2f}: 件数={s['件数']:6,}  複勝的中率={s['複勝的中率(%)']:6.2f}%  "
              f"(ベースライン比 +{s['複勝的中率(%)'] - baseline['複勝的中率(%)']:5.2f}pt)")

    print()
    print("--- 参考: 上位1頭（◎相当）の複勝的中率を、レース条件別に分解 ---")
    top1 = merged[merged["rank_in_race"] <= 1]
    for label, sub in [("全体", top1),
                        ("8頭以上のレースのみ", top1[top1["field_size"] >= 8]),
                        ("7頭以下のレースのみ", top1[top1["field_size"] < 8])]:
        s = summarize(sub)
        print(f"  {label}: 件数={s['件数']:6,}  複勝的中率={s['複勝的中率(%)']:6.2f}%")


if __name__ == "__main__":
    main()
