"""
dsk_Project
Stage2: 学習用データセット作成
Version 0.1

PROJECT_EVのai/build_dataset.pyと同じ設計思想を踏襲しつつ、dsk_Projectの
スキーマ（8項目を条件別重みで合算したoverall_score）に合わせて作り直した。

PROJECT_EVとの最大の違い（本プロジェクトの主眼）:
  PROJECT_EVは近走成績・距離適性・血統成績等を「個別の生の特徴量」として
  LightGBMに渡し、モデル自身に組み合わせ方を学習させる設計。
  dsk_Projectは開発指示書の設計（レース内Min-Max正規化 × 条件別重み ×
  脚質補正）に従って8項目を先に人手でoverall_scoreへ合算しており、
  この「手作りの合成スコア」をLightGBMに渡す。overall_score単体で
  どこまで的中率・回収率を説明できるか、そこにオッズ・人気を明示的に
  加えるとどう変わるかを検証するのがStage2の目的（開発指示書5節の
  検証すべき問い1）。

目的変数 label: 1着なら1、それ以外は0（単勝モデルのみ。複勝は開発指示書の
「必要に応じて」に留まる位置づけのため、まずは単勝に絞る）。

除外する行・レース:
  - results.finish_position が NULL の行（出走取消・競走中止等）
  - 出走馬全員が「過去走データなし」のレース。PROJECT_EVはavg_finish
    （直近5走平均着順）のNULL判定を使っていたが、dsk_Projectの
    overall_scoreは常に数値（欠損項目はCOALESCEで0として合算される。
    feature_engineering/overall_score.py参照）でありNULL判定に使えない
    ため、代わりにstability_power（対象レースより前の全レースを対象に
    した通算連対率。絞り込みが無いため、これがNULLなら対象レース前の
    過去走が1件も無いことを意味する）のNULL判定を使う。

特徴量に含めない列とその理由:
  - horse_name: 識別子であり特徴量ではない（参照用にデータセットには残す）
  - agari_power等、overall_scoreの元になった8項目の個別列: 開発指示書の
    狙いは「8項目を条件別重みで合成したoverall_score」そのものの説明力を
    見ることなので、個別列は特徴量に含めない（将来、個別列も加えた場合との
    比較をしたくなったら別途アブレーション用のFEATURE_COLUMNSを追加する）
  - raw_rank / odds_adjusted_rank: いずれもレース内順位というoverall_score /
    オッズの単なる並べ替えであり、oddsを含むmarket_columnsと同様の
    リーク経路になり得るため、市場情報あり/なしどちらのモデルにも含めない
    （odds_adjusted_rankはオッズそのものから作られる値のため特に注意）
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager

# LightGBMのcategorical_featureとして扱う列
CATEGORICAL_COLUMNS = [
    "course",
    "surface",
    "track_condition",
    "race_class",
    "direction",
    "age_condition",
    "sex_age",
]

# 市場情報（オッズ・人気）列。市場情報あり/なしモデルの切り替えに使う
MARKET_COLUMNS = ["market_odds", "market_popularity"]

# 市場情報を除いた基本特徴量（レース条件・馬の属性・overall_score）
BASE_FEATURE_COLUMNS = CATEGORICAL_COLUMNS + [
    "round",
    "distance",
    "horse_number",
    "frame_number",
    "weight",
    "overall_score",
]

# 市場情報を含めた特徴量（本プロジェクトの主眼となるモデル）
FEATURE_COLUMNS = BASE_FEATURE_COLUMNS + MARKET_COLUMNS

QUERY = """
    SELECT
        e.race_id,
        e.horse_id,
        h.horse_name,
        r.race_date,
        r.course,
        r.round,
        r.distance,
        r.surface,
        r.track_condition,
        r.race_class,
        r.direction,
        r.age_condition,
        e.horse_number,
        e.frame_number,
        e.sex_age,
        e.weight,
        e.odds AS market_odds,
        e.popularity AS market_popularity,
        f.overall_score,
        f.stability_power,
        res.finish_position
    FROM entries e
    JOIN races r ON r.race_id = e.race_id
    JOIN horses h ON h.horse_id = e.horse_id
    JOIN features f ON f.race_id = e.race_id AND f.horse_id = e.horse_id
    LEFT JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
    ORDER BY r.race_date, e.race_id, e.horse_number
"""


def build_dataset(db=None):
    """学習用データセットをpandas DataFrameとして返す（分割は行わない）。
    label: 1着なら1、それ以外は0"""
    if db is None:
        db = DatabaseManager()

    conn = db.connect()
    df = pd.read_sql_query(QUERY, conn)
    conn.close()

    before = len(df)
    df = df[df["finish_position"].notna()].copy()
    excluded_no_result = before - len(df)

    total_races_before_debut_filter = df["race_id"].nunique()
    race_all_debut = df.groupby("race_id")["stability_power"].transform(lambda s: s.isna().all())
    excluded_debut_races = df.loc[race_all_debut, "race_id"].nunique()
    df = df[~race_all_debut].copy()

    df["label"] = (df["finish_position"] == 1).astype(int)
    df = df.drop(columns=["finish_position", "stability_power"])

    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")

    df.attrs["excluded_no_result"] = excluded_no_result
    df.attrs["excluded_debut_races"] = excluded_debut_races
    df.attrs["total_races_before_debut_filter"] = total_races_before_debut_filter

    return df


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("build_dataset 実行")
    print("=" * 40)

    dataset = build_dataset()

    print()
    print(f"データセット件数: {len(dataset)}")
    print(f"除外件数（finish_position NULL）: {dataset.attrs['excluded_no_result']}")
    excluded_races = dataset.attrs["excluded_debut_races"]
    total_races = dataset.attrs["total_races_before_debut_filter"]
    pct = excluded_races / total_races * 100 if total_races else 0
    print(f"除外レース数（全馬過去走データなし）: {excluded_races} / {total_races} ({pct:.2f}%)")
    print(f"陽性件数（1着）: {dataset['label'].sum()} ({dataset['label'].mean() * 100:.2f}%)")
    print(f"期間: {dataset['race_date'].min()} 〜 {dataset['race_date'].max()}")
    print(f"特徴量列数（市場情報あり）: {len(FEATURE_COLUMNS)}  （市場情報なし）: {len(BASE_FEATURE_COLUMNS)}")
