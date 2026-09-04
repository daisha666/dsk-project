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
    リーク経路になり得るため、標準のFEATURE_COLUMNS / BASE_FEATURE_COLUMNSには
    含めない（odds_adjusted_rankはオッズそのものから作られる値のため特に注意）

odds_adjusted_score列について（ai/model_a_odds_adjusted_ablation.py用）:
  QUERYにはf.odds_adjusted_score（overall_score×(1/オッズ)×係数、開発指示書2.3の
  「AK列」相当）を含めているが、上記の理由によりFEATURE_COLUMNS /
  BASE_FEATURE_COLUMNSには含めていない。ただしモデルA（市場情報あり）は
  そもそもmarket_oddsを特徴量として持っているため、モデルAに限っては
  odds_adjusted_scoreを追加してもmarket_odds由来の新たなリークにはならない
  （overall_scoreとmarket_oddsという既存の2特徴量から決定的に導出される値を
  明示的な特徴量として渡すだけ）。この検証専用にFEATURE_COLUMNS_A_ODDS_ADJUSTED
  を用意している。
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

# 検証専用: モデルA（市場情報あり）にodds_adjusted_scoreを追加したもの
# （ai/model_a_odds_adjusted_ablation.py参照）
FEATURE_COLUMNS_A_ODDS_ADJUSTED = FEATURE_COLUMNS + ["odds_adjusted_score"]

# 数値特徴量（CATEGORICAL_COLUMNS以外）。全馬分がNULL（枠番確定直後でオッズ・
# 馬体重がまだ収集されていない新規レース等）だと、pd.read_sql_queryの結果が
# 全てNoneの列になりdtype=objectのままになる（数値+Noneの混在ならfloat64に
# 自動で上がるが、全件Noneだとそうならない）。LightGBMはfloat列内のNaNは
# 欠損値として扱えるが、dtype=objectの列は
# 「ValueError: pandas dtypes must be int, float or bool」で拒否するため、
# 明示的にfloat64へキャストしておく（2026-09-04、本番の枠番確定直後の
# 実データで実際に発生）
NUMERIC_FEATURE_COLUMNS = [
    "round", "distance", "horse_number", "frame_number", "weight",
    "overall_score", "market_odds", "market_popularity", "odds_adjusted_score",
]


def _coerce_numeric_dtypes(df):
    for col in NUMERIC_FEATURE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

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
        f.odds_adjusted_score,
        f.stability_power,
        res.finish_position
    FROM entries e
    JOIN races r ON r.race_id = e.race_id
    JOIN horses h ON h.horse_id = e.horse_id
    JOIN features f ON f.race_id = e.race_id AND f.horse_id = e.horse_id
    LEFT JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
    ORDER BY r.race_date, e.race_id, e.horse_number
"""

# build_dataset()と同じ列だが、まだ結果が確定していない（＝これから予測したい）
# レースだけに絞り込む。prediction/predict_race.py専用
UPCOMING_QUERY = """
    SELECT
        e.race_id,
        e.horse_id,
        h.horse_name,
        r.race_date,
        r.course,
        r.round,
        r.race_name,
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
        f.odds_adjusted_score,
        f.raw_rank,
        f.odds_adjusted_rank,
        f.stability_power
    FROM entries e
    JOIN races r ON r.race_id = e.race_id
    JOIN horses h ON h.horse_id = e.horse_id
    JOIN features f ON f.race_id = e.race_id AND f.horse_id = e.horse_id
    WHERE e.race_id NOT IN (
        SELECT DISTINCT race_id FROM results WHERE finish_position IS NOT NULL
    )
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

    df = _coerce_numeric_dtypes(df)
    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")

    df.attrs["excluded_no_result"] = excluded_no_result
    df.attrs["excluded_debut_races"] = excluded_debut_races
    df.attrs["total_races_before_debut_filter"] = total_races_before_debut_filter

    return df


def build_upcoming_dataset(db=None):
    """まだ結果が確定していないレースを、build_dataset()と同じ特徴量の形で
    pandas DataFrameとして返す（labelは無い。予測対象を作るための関数）。
    build_dataset()（学習用）とは異なり、出走馬全員が過去走データなしの
    レース（新馬戦等）も除外しない。表示対象のレース一覧に穴を開けない
    ためで、FEATURE_COLUMNS_A_ODDS_ADJUSTED自体は騎手力・調教師力・血統力等
    馬自身の過去走を必要としない列も含むため予測自体は計算できる（ただし
    信頼度は下がる）。新馬戦・未勝利戦はai/backtest.py::EXCLUDED_CLASS_TIERSに
    より買い目推奨の対象からは別途除外されるため、ここで除いておく必要はない。
    raw_rank・odds_adjusted_rankは特徴量としては使わないが、アプリ表示用に残す
    （prediction/predict_race.py参照）"""
    if db is None:
        db = DatabaseManager()

    conn = db.connect()
    df = pd.read_sql_query(UPCOMING_QUERY, conn)
    conn.close()

    df = df.drop(columns=["stability_power"])
    df = _coerce_numeric_dtypes(df)
    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")

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
