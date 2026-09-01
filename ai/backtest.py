"""
dsk_Project
Stage3: 期待値ベースの購入判定・回収率バックテスト（モデルA/B比較）
Version 0.1

PROJECT_EVのai/backtest.pyと同じ設計思想（期待値=予測勝率×オッズでの
購入判定、オッズ上限フィルタで大穴バイアスに対処）を踏襲しつつ、以下の
2点を変更している。

(1) モデルA・モデルBの両方で回収率を算出・比較する:
  PROJECT_EVはモデルB（市場情報除外）のみをバックテスト対象にしていた
  （モデルAは市場のインプライド確率とほぼ同じ値を予測するだけで、
  期待値が常に1近辺に張り付き「市場との乖離」を検出できないため）。
  dsk_Projectの目的はまさに「市場情報を明示的に含めた方が回収率で
  優位か」を検証することなので、両モデルを同じ土俵で比較する。
  ただしモデルAはmarket_oddsを特徴量に含んでいるため、モデルA自身の
  pred_win_probが暗黙的にmarket_oddsへ強く依存している点には留意する
  （＝モデルAの「期待値」は市場とほぼ独立ではない可能性がある。これも
  含めて実際の回収率で比較するのが本バックテストの目的）。

(2) 払戻金の計算に確定payoutsテーブルを使う（★最重要）:
  PROJECT_EVは的中時の払戻額を「STAKE × market_odds（発走前オッズの
  スナップショット）」で簡易計算していたが、これは正確な確定払戻額とは
  限らない（オッズは発走直前まで動くため、entries.oddsは購入判定時点の
  スナップショットであり、確定後の実際の払戻額と一致する保証がない。
  database/schema.sqlのpayoutsテーブルのコメント参照）。dsk_Projectでは
  購入判定（EV計算・オッズ上限フィルタ）にはentries.odds（発走前オッズ、
  リーケージ防止のため確定後の情報は使えない）を使うが、的中時の払戻額
  計算には必ずpayoutsテーブル（bet_type='単勝'、combination=馬番、
  payout_yen=100円あたりの確定払戻金）を使う。

ウォークフォワード（拡大窓・複数フォールド）で両モデルを学習・予測し、
フォールドをまたいだ全期間のOOS（Out-of-Sample）予測を集計してから
回収率を計算する（単一の学習/テスト分割よりデータを有効活用でき、
Stage2のwalk_forward_backtest.pyと同じ検証単位で比較できる）。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.build_dataset import BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, FEATURE_COLUMNS, build_dataset
from ai.train_model import evaluate_model, split_by_date, train_model
from ai.walk_forward_backtest import generate_folds
from database.db_manager import DatabaseManager

STAKE = 100  # 単勝1点あたりの購入額（円）。payouts.payout_yenは100円あたりの確定払戻額のため
             # STAKEを変えても実際の払戻額は payout_yen * (STAKE/100) で比例計算する
EV_THRESHOLDS = [1.20, 1.50, 2.00]
ODDS_CAPS = [None, 50, 30]

# クラス帯判定（PROJECT_EVのanalysis/similar_races.py::classify_class_tierと同じロジック。
# races.race_classの表記はdsk_ProjectもYahoo競馬由来で同一フォーマットのため、そのまま流用できる）
EXCLUDED_CLASS_TIERS = {"新馬", "未勝利"}


def classify_class_tier(race_class):
    """race_class（TEXT、表記揺れあり）から大まかなクラス帯を判定する。
    G1/G2/G3/リステッド/オープン/勝ち星クラス/未勝利/新馬 に分類する"""
    if not race_class or (isinstance(race_class, float) and pd.isna(race_class)):
        return None

    if race_class.startswith(("GI ", "JpnI ")):
        return "G1"
    if race_class.startswith(("GII ", "JpnII ")):
        return "G2"
    if race_class.startswith(("GIII ", "JpnIII ")):
        return "G3"
    if race_class.startswith("L "):
        return "リステッド"
    if "3勝クラス" in race_class:
        return "3勝クラス"
    if "2勝クラス" in race_class:
        return "2勝クラス"
    if "1勝クラス" in race_class:
        return "1勝クラス"
    if "新馬" in race_class:
        return "新馬"
    if "未勝利" in race_class:
        return "未勝利"
    if "オープン" in race_class:
        return "オープン"
    return "その他"


def is_class_included(race_class):
    """1勝クラス以上（新馬・未勝利を除く）ならTrue"""
    return classify_class_tier(race_class) not in EXCLUDED_CLASS_TIERS


def select_top1_per_race(candidates):
    """レースごとにexpected_valueが最大の1頭だけを残す
    （同値の場合は予測確率が高い方を採用。それも同値ならDataFrame内の出現順で先頭）"""
    if len(candidates) == 0:
        return candidates
    sorted_c = candidates.sort_values(["expected_value", "pred_win_prob"], ascending=[False, False])
    return sorted_c.drop_duplicates(subset="race_id", keep="first")


def fetch_win_payouts(db=None):
    """確定払戻金テーブル（payouts）から単勝の {(race_id, horse_number): payout_yen} を返す。
    まれに同一race_idに複数行（着差なしの同着等）が入り得るため、
    race_id+horse_number単位で重複があれば最初の1件を使う"""
    if db is None:
        db = DatabaseManager()

    rows = db.fetchall("""
        SELECT race_id, combination, payout_yen
        FROM payouts
        WHERE bet_type = '単勝'
    """)

    payouts = {}
    for race_id, combination, payout_yen in rows:
        try:
            horse_number = int(combination)
        except (TypeError, ValueError):
            continue
        payouts.setdefault((race_id, horse_number), payout_yen)

    return payouts


def attach_confirmed_payout(df, win_payouts):
    """dfの各行（race_id, horse_number）に対応する確定単勝払戻金（100円あたり）を
    confirmed_payout_yen列として追加する。負けた馬（label=0）や、確定払戻データが
    見つからない馬はNaNのままにする（的中扱いの集計にはlabel==1のみを使うため、
    集計結果には影響しない。ただしlabel==1なのにNaNの場合はデータ不整合の兆候
    なので、呼び出し側でチェックする）"""
    df = df.copy()
    df["confirmed_payout_yen"] = df.apply(
        lambda row: win_payouts.get((row["race_id"], int(row["horse_number"]))),
        axis=1,
    )
    return df


def score_fold_with_market(dataset, test_start, test_end, feature_columns, categorical_columns, win_payouts):
    """1フォールド分の学習・予測を行い、EV計算・確定払戻金付きのテスト結果を返す
    （購入判定用の予測はテスト期間より前のデータのみで学習したモデルによるもので、
    データリーケージは無い。expected_valueの計算に使うmarket_oddsは発走前オッズの
    スナップショットで、これも購入判定時点で実際に入手可能な情報のみ）"""
    train_df, test_df = split_by_date(dataset, test_start, test_end)

    if len(train_df) == 0 or len(test_df) == 0:
        return None

    model = train_model(train_df, feature_columns, categorical_columns)
    proba, y_test, auc, logloss = evaluate_model(model, test_df, feature_columns)

    result = test_df.loc[:, [
        "race_id", "horse_id", "horse_name", "race_date", "horse_number",
        "market_odds", "market_popularity", "race_class",
    ]].copy()
    result["label"] = y_test.values
    result["pred_win_prob"] = proba
    result["expected_value"] = result["pred_win_prob"] * result["market_odds"]

    result = attach_confirmed_payout(result, win_payouts)

    missing_payout = result[(result["label"] == 1) & (result["confirmed_payout_yen"].isna())]
    if len(missing_payout) > 0:
        print(f"  警告: 1着なのに確定払戻データが見つからない行が{len(missing_payout)}件あります "
              f"（例: race_id={missing_payout.iloc[0]['race_id']}）。payoutsテーブルの収集漏れの可能性")

    return result, auc, logloss


def simulate(df, ev_threshold, stake=STAKE, odds_cap=None, min_pred_prob=None,
             class_filter=False, top1=False):
    """期待値がev_threshold以上、（odds_capを指定した場合は）market_oddsが
    odds_cap以下、（min_pred_probを指定した場合は）pred_win_probが
    min_pred_prob以上の馬を単勝均等購入した場合の回収成績を計算する。
    購入判定（絞り込み）にはmarket_odds（発走前オッズ）を使うが、的中時の
    払戻額は必ずconfirmed_payout_yen（確定払戻金）を使う。

    class_filter=True: 新馬戦・未勝利戦を除外し、1勝クラス以上のレースだけを
      購入対象にする（PROJECT_EVのai/class_filter_backtest.pyと同じ）。
      モデルの学習データ自体は変えず、あくまで購入判定時の絞り込みとして適用する。
    top1=True: 同一レースで複数の推奨馬（EV閾値・オッズ上限を満たす馬）がいる
      場合、期待値が最大の1頭だけを購入する（PROJECT_EVのai/top1_ev_backtest.py
      と同じ。同値ならpred_win_probが高い方を採用）"""
    candidates = df[df["expected_value"] >= ev_threshold]

    if odds_cap is not None:
        candidates = candidates[candidates["market_odds"] <= odds_cap]

    if min_pred_prob is not None:
        candidates = candidates[candidates["pred_win_prob"] >= min_pred_prob]

    if class_filter:
        candidates = candidates[candidates["race_class"].apply(is_class_included)]

    if top1:
        candidates = select_top1_per_race(candidates)

    buys = candidates

    n_buys = len(buys)
    n_hits = int(buys["label"].sum())
    hit_rate = n_hits / n_buys if n_buys > 0 else np.nan

    hit_rows = buys[buys["label"] == 1]
    # 確定払戻データが欠けている的中馬は払戻0円として扱わず、計算から除外する
    # （回収率を実態より低く見せてしまうバイアスを避けるため。missing件数は
    # score_fold_with_market側で警告済み）
    valid_payout = hit_rows["confirmed_payout_yen"].dropna()

    total_stake = n_buys * stake
    total_payout = (valid_payout * (stake / 100)).sum()
    recovery_rate = total_payout / total_stake * 100 if total_stake > 0 else np.nan

    return {
        "オッズ上限": odds_cap if odds_cap is not None else "なし",
        "EV閾値": ev_threshold,
        "買い件数": n_buys,
        "買い対象レース数": buys["race_id"].nunique(),
        "的中数": n_hits,
        "的中率(%)": hit_rate * 100 if n_buys > 0 else np.nan,
        "総購入額(円)": total_stake,
        "総払戻額(円)": total_payout,
        "回収率(%)": recovery_rate,
    }


def run_model_backtest(dataset, folds, feature_columns, categorical_columns, win_payouts, model_label, log=print):
    """全フォールドを学習・予測し、OOS予測を結合したDataFrameを返す"""
    all_results = []

    for test_start, test_end in folds:
        outcome = score_fold_with_market(
            dataset, test_start, test_end, feature_columns, categorical_columns, win_payouts
        )
        if outcome is None:
            log(f"  [{model_label}] {test_start}〜{test_end}: 学習/テストデータなし、スキップ")
            continue

        result, auc, logloss = outcome
        log(f"  [{model_label}] {test_start}〜{test_end}: テスト={len(result)} AUC={auc:.4f}")
        all_results.append(result)

    if not all_results:
        return pd.DataFrame()

    return pd.concat(all_results, ignore_index=True)


def main():
    print("=" * 40)
    print("dsk_Project")
    print("Stage3 バックテスト: モデルA/B 回収率比較（確定payoutsベース）")
    print("=" * 40)

    dataset = build_dataset()
    folds = generate_folds(dataset)
    win_payouts = fetch_win_payouts()

    print(f"データセット件数: {len(dataset)}  フォールド数: {len(folds)}")
    print(f"確定単勝払戻データ件数: {len(win_payouts)}")

    print()
    print("--- モデルA（市場情報あり）のフォールド別学習・予測 ---")
    df_a = run_model_backtest(dataset, folds, FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルA")

    print()
    print("--- モデルB（市場情報除外）のフォールド別学習・予測 ---")
    df_b = run_model_backtest(dataset, folds, BASE_FEATURE_COLUMNS, CATEGORICAL_COLUMNS, win_payouts, "モデルB")

    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")

    for label, df in [("モデルA（市場情報あり）", df_a), ("モデルB（市場情報除外）", df_b)]:
        print()
        print(f"=== {label}: オッズ上限 × EV閾値 の回収率比較 ===")
        rows = []
        for cap in ODDS_CAPS:
            for th in EV_THRESHOLDS:
                rows.append(simulate(df, th, odds_cap=cap))
        summary = pd.DataFrame(rows)
        print(summary.to_string(index=False))

    print()
    print("=== モデルA vs モデルB（オッズ上限30倍・EV閾値1.20での比較） ===")
    sim_a = simulate(df_a, 1.20, odds_cap=30)
    sim_b = simulate(df_b, 1.20, odds_cap=30)
    print(f"{'':28s}{'買い件数':>10s}{'的中率(%)':>12s}{'回収率(%)':>12s}")
    print(f"{'モデルA(市場情報あり)':28s}{sim_a['買い件数']:10d}{sim_a['的中率(%)']:12.2f}{sim_a['回収率(%)']:12.2f}")
    print(f"{'モデルB(市場情報除外)':28s}{sim_b['買い件数']:10d}{sim_b['的中率(%)']:12.2f}{sim_b['回収率(%)']:12.2f}")


if __name__ == "__main__":
    main()
