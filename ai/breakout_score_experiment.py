"""
dsk_Project
②激走スコア（穴馬検出）の試作・検証
Version 0.1

最終版(ハイブリッド)の「激走スコア／激走フラグ」の設計思想（前走からの
上昇度・克服条件など）を参考に、dsk_Project独自の「穴馬になりそうな馬」を
検出するスコアを試作する。dsk_Projectには調教データが無いため、以下3点の
組み合わせで代用する（いずれも対象レースより前の履歴だけを使う、
リーケージ無しの設計）:

  ①着順改善: 直近1走の着順が、その前2〜5走の平均着順よりどれだけ良いか
  ②上がり改善: 直近1走の上がり3F(last3f)が、その前2〜5走の平均上がりより
    どれだけ速いか（タイムは短いほど良いので符号を反転する）
  ③クラス緩和: 直近1走のクラスが、今回のレースのクラスより格上だったか
    （格上のレースを走っていた馬が格下に戻る＝相対的に楽になる）

3つをそれぞれz-score化して合算し、breakout_scoreとする。

検証は2段階:
  (a) この特徴量を単勝モデルに追加した場合の重要度・AUCへの影響
  (b) 既存の「◎(軸)+相手をEV基準で選ぶ」馬連戦略（EV>=1.2、回収率87.3%だが
      不安定と判定済み）に対し、相手選びの基準をbreakout_scoreに変える
      （またはEVと組み合わせる）ことで改善するか
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import classify_class_tier, compute_sharpe_ratio
from ai.build_dataset import CATEGORICAL_COLUMNS, FEATURE_COLUMNS_A_ODDS_ADJUSTED, build_dataset
from ai.train_model import evaluate_model, feature_importance_report, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager
from feature_engineering.common import get_past_races, load_horse_histories

STAKE = 100
CLASS_TIER_ORDER = {
    "新馬": 0, "未勝利": 1, "1勝クラス": 2, "2勝クラス": 3, "3勝クラス": 4,
    "オープン": 5, "リステッド": 6, "G3": 7, "G2": 8, "G1": 9,
}
LOOKBACK_RECENT = 1  # 直近何走を「直近」とみなすか
LOOKBACK_BASELINE = 4  # 直近の前、何走を平均のベースラインにするか


def compute_breakout_scores(db=None):
    """horse_id×target_race_id単位でbreakout_scoreの3成分・合成値を計算する。
    戻り値: DataFrame(race_id, horse_id, finish_improve, last3f_improve, class_relief, breakout_score)"""
    if db is None:
        db = DatabaseManager()

    histories = load_horse_histories(db)
    targets = db.fetchall("""
        SELECT e.race_id, e.horse_id, r.race_date, r.race_class
        FROM entries e
        JOIN races r ON r.race_id = e.race_id
        ORDER BY r.race_date, e.race_id
    """)

    rows = []
    for race_id, horse_id, race_date, race_class in targets:
        history = histories.get(horse_id, [])
        past = get_past_races(history, race_date, limit=LOOKBACK_RECENT + LOOKBACK_BASELINE)
        if len(past) < LOOKBACK_RECENT + 2:  # ベースラインが最低2走は無いと意味が薄い
            rows.append((race_id, horse_id, None, None, None))
            continue

        recent = past[-LOOKBACK_RECENT:]
        baseline = past[:-LOOKBACK_RECENT]

        recent_finish = [r["finish_position"] for r in recent if r["finish_position"] is not None]
        baseline_finish = [r["finish_position"] for r in baseline if r["finish_position"] is not None]
        finish_improve = (np.mean(baseline_finish) - np.mean(recent_finish)) if recent_finish and baseline_finish else None

        recent_last3f = [r["last3f"] for r in recent if r["last3f"] is not None]
        baseline_last3f = [r["last3f"] for r in baseline if r["last3f"] is not None]
        last3f_improve = (np.mean(baseline_last3f) - np.mean(recent_last3f)) if recent_last3f and baseline_last3f else None

        recent_class = recent[-1]["race_class"] if recent else None
        recent_tier = CLASS_TIER_ORDER.get(classify_class_tier(recent_class))
        target_tier = CLASS_TIER_ORDER.get(classify_class_tier(race_class))
        class_relief = (recent_tier - target_tier) if recent_tier is not None and target_tier is not None else None

        rows.append((race_id, horse_id, finish_improve, last3f_improve, class_relief))

    df = pd.DataFrame(rows, columns=["race_id", "horse_id", "finish_improve", "last3f_improve", "class_relief"])

    for col in ["finish_improve", "last3f_improve", "class_relief"]:
        mean, std = df[col].mean(), df[col].std()
        df[f"{col}_z"] = ((df[col] - mean) / std) if std else 0.0
        df[f"{col}_z"] = df[f"{col}_z"].fillna(0.0)

    df["breakout_score"] = df[["finish_improve_z", "last3f_improve_z", "class_relief_z"]].mean(axis=1)
    return df


def check_model_importance(dataset, breakout_df, log=print):
    dataset = dataset.merge(breakout_df[["race_id", "horse_id", "breakout_score"]], on=["race_id", "horse_id"], how="left")
    dataset["breakout_score"] = dataset["breakout_score"].fillna(0)

    feature_columns_with_breakout = FEATURE_COLUMNS_A_ODDS_ADJUSTED + ["breakout_score"]

    print("=" * 70)
    print("(a) breakout_scoreを単勝モデルに追加した場合の重要度・AUC")
    print("=" * 70)
    model_full = train_model(dataset, feature_columns_with_breakout, CATEGORICAL_COLUMNS)
    importances = feature_importance_report(model_full, feature_columns_with_breakout, top_n=len(feature_columns_with_breakout))
    total = importances.sum()
    val = importances.get("breakout_score", 0)
    rank = list(importances.index).index("breakout_score") + 1 if "breakout_score" in importances.index else None
    print(f"  breakout_score: 重要度={val:,.1f}  構成比={val/total*100:.3f}%  順位={rank}/{len(feature_columns_with_breakout)}")

    folds = generate_folds(dataset)
    for label, feature_columns in [("breakout無し（現行）", FEATURE_COLUMNS_A_ODDS_ADJUSTED),
                                    ("breakout有り", feature_columns_with_breakout)]:
        aucs = []
        for test_start, test_end in folds:
            train_df, test_df = split_by_date(dataset, test_start, test_end)
            if len(train_df) == 0 or len(test_df) == 0:
                continue
            model = train_model(train_df, feature_columns, CATEGORICAL_COLUMNS)
            _proba, _y, auc, _logloss = evaluate_model(model, test_df, feature_columns)
            aucs.append(auc)
        print(f"  [{label}] 平均AUC: {np.mean(aucs):.4f}  フォールド別: {[round(a,4) for a in aucs]}")

    return dataset, folds


def umaren_key(a, b):
    return f"{min(a, b)}-{max(a, b)}"


def check_umaren_strategy(dataset, breakout_df, folds, log=print):
    print()
    print("=" * 70)
    print("(b) 馬連: ◎(軸)+相手選びをbreakout_scoreで変えた場合の比較")
    print("=" * 70)

    db = DatabaseManager()
    umaren_payouts = {}
    for race_id, combination, payout_yen in db.fetchall(
        "SELECT race_id, combination, payout_yen FROM payouts WHERE bet_type='馬連'"
    ):
        umaren_payouts.setdefault((race_id, combination), payout_yen)

    finish_rows = db.fetchall("""
        SELECT e.race_id, e.horse_number, e.horse_id, res.finish_position,
               (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = e.race_id) AS field_size
        FROM entries e
        JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
        WHERE res.finish_position IS NOT NULL
    """)
    finish_df = pd.DataFrame(finish_rows, columns=["race_id", "horse_number", "horse_id", "finish_position", "field_size"])

    all_results = []
    for fold_idx, (test_start, test_end) in enumerate(folds):
        train_df, test_df = split_by_date(dataset, test_start, test_end)
        if len(train_df) == 0 or len(test_df) == 0:
            continue
        model = train_model(train_df, FEATURE_COLUMNS_A_ODDS_ADJUSTED, CATEGORICAL_COLUMNS)
        proba, _y, _auc, _logloss = evaluate_model(model, test_df, FEATURE_COLUMNS_A_ODDS_ADJUSTED)
        result = test_df.loc[:, ["race_id", "horse_id", "horse_number", "market_odds"]].copy()
        result["pred_win_prob"] = proba
        result["fold_idx"] = fold_idx
        all_results.append(result)

    oos = pd.concat(all_results, ignore_index=True)
    oos = oos.merge(finish_df, on=["race_id", "horse_number", "horse_id"], how="left")
    oos = oos.merge(breakout_df[["race_id", "horse_id", "breakout_score"]], on=["race_id", "horse_id"], how="left")
    oos["breakout_score"] = oos["breakout_score"].fillna(0)
    oos = oos.dropna(subset=["field_size"])

    n_folds = len(folds)
    strategies = {
        "EV>=1.2（既存・不安定と判定済み）": lambda others: others[others["expected_value"] >= 1.2],
        "breakout上位2頭": lambda others: others.sort_values("breakout_score", ascending=False).iloc[:2],
        "breakout>=1.0(穴オッズ10倍以上)": lambda others: others[(others["breakout_score"] >= 1.0) & (others["market_odds"] >= 10)],
        "EV>=1.0 かつ breakout>=0.5": lambda others: others[(others["expected_value"] >= 1.0) & (others["breakout_score"] >= 0.5)],
    }
    stakes = {k: [0] * n_folds for k in strategies}
    payouts = {k: [0.0] * n_folds for k in strategies}
    bets = {k: [0] * n_folds for k in strategies}
    hits = {k: [0] * n_folds for k in strategies}

    for (race_id, fold_idx), group in oos.groupby(["race_id", "fold_idx"]):
        if group["finish_position"].isna().any():
            continue
        anchor_row = group.loc[group["pred_win_prob"].idxmax()]
        anchor_num = anchor_row["horse_number"]
        others = group[group["horse_number"] != anchor_num].copy()
        if len(others) == 0:
            continue
        others["expected_value"] = others["pred_win_prob"] * others["market_odds"]

        actual_top2 = set(group[group["finish_position"] <= 2]["horse_number"])
        anchor_in_top2 = anchor_num in actual_top2
        actual_partner = (actual_top2 - {anchor_num}) if anchor_in_top2 else set()

        for strat_label, selector in strategies.items():
            partners = selector(others)
            if len(partners) == 0:
                continue
            for _, p in partners.iterrows():
                p_num = p["horse_number"]
                stakes[strat_label][fold_idx] += STAKE
                bets[strat_label][fold_idx] += 1
                hit = anchor_in_top2 and (p_num in actual_partner)
                if hit:
                    hits[strat_label][fold_idx] += 1
                    payout = umaren_payouts.get((race_id, umaren_key(anchor_num, p_num)))
                    if payout is not None:
                        payouts[strat_label][fold_idx] += payout / 100 * STAKE

    for strat_label in strategies:
        recoveries = [payouts[strat_label][i] / stakes[strat_label][i] * 100 if stakes[strat_label][i] > 0 else float("nan")
                      for i in range(n_folds)]
        valid = [r for r in recoveries if not np.isnan(r)]
        total_stake, total_payout = sum(stakes[strat_label]), sum(payouts[strat_label])
        overall = total_payout / total_stake * 100 if total_stake else float("nan")
        sharpe = compute_sharpe_ratio(valid) if len(valid) >= 2 else float("nan")
        print(f"\n  [{strat_label}]")
        print(f"    総購入点数={sum(bets[strat_label]):,}  総的中数={sum(hits[strat_label]):,}  全体回収率={overall:.1f}%")
        print(f"    フォールド別回収率: {[round(r,1) if not np.isnan(r) else None for r in recoveries]}")
        if len(valid) >= 2:
            print(f"    平均={np.mean(valid):.1f}%  標準偏差={np.std(valid, ddof=1):.1f}  シャープレシオ={sharpe:.3f}")


def main():
    print("=" * 70)
    print("dsk_Project")
    print("②激走スコア（穴馬検出）の試作・検証")
    print("=" * 70)

    breakout_df = compute_breakout_scores()
    print(f"breakout_score計算件数: {len(breakout_df)}")
    print(breakout_df["breakout_score"].describe())

    dataset = build_dataset()
    dataset, folds = check_model_importance(dataset, breakout_df)
    check_umaren_strategy(dataset, breakout_df, folds)


if __name__ == "__main__":
    main()
