"""
dsk_Project
特徴量重要度分析: 既存8項目 vs 市場情報
Version 0.1

開発指示書の設計では、8項目（上がり力・脚質力・騎手力・距離力・回り力・
安定力・血統力・調教師力）を条件別重みで合算したoverall_score「単体」を
LightGBMへ渡している（ai/build_dataset.py::FEATURE_COLUMNS）。この設計は
「8項目を条件別重みで合成したoverall_scoreそのものの説明力を見る」ことが
目的だが、overall_score単体の重要度が高い/低いと分かっても、内訳の
8項目それぞれがどれだけ効いているか（あるいは効いていないか）は分からない。

本スクリプトは検証専用に、overall_scoreの代わりに8項目の生の列
（features.agari_power等）をそれぞれ独立した特徴量としてLightGBMへ渡し、
市場情報（オッズ・人気）と同じ土俵でgainベースの特徴量重要度を比較する。

overall_scoreは8項目の決定的な線形結合（Σ COALESCE(item,0)。
feature_engineering/overall_score.py参照）であり、8項目と同時に特徴量へ
含めると多重共線性になる（overall_scoreが8項目の情報を丸ごと肩代わりして
しまい、個別項目の重要度が不当に低く出る）ため、この検証専用の特徴量集合
FEATURE_COLUMNS_ITEM_BREAKDOWNからはoverall_scoreを除外している。
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.build_dataset import CATEGORICAL_COLUMNS, MARKET_COLUMNS
from ai.train_model import feature_importance_report, train_model
from database.db_manager import DatabaseManager

POWER_ITEM_COLUMNS = [
    "agari_power", "kyakushitsu_power", "jockey_power", "distance_power",
    "turn_power", "stability_power", "pedigree_power", "trainer_power",
]

ITEM_LABELS = {
    "agari_power": "上がり力", "kyakushitsu_power": "脚質力", "jockey_power": "騎手力",
    "distance_power": "距離力", "turn_power": "回り力", "stability_power": "安定力",
    "pedigree_power": "血統力", "trainer_power": "調教師力",
    "market_odds": "オッズ", "market_popularity": "人気",
}

# 8項目を個別に含み、overall_scoreは含めない（多重共線性を避けるため）
FEATURE_COLUMNS_ITEM_BREAKDOWN = CATEGORICAL_COLUMNS + [
    "round", "distance", "horse_number", "frame_number", "weight",
] + POWER_ITEM_COLUMNS + MARKET_COLUMNS

NUMERIC_COLUMNS = [
    "round", "distance", "horse_number", "frame_number", "weight",
    "market_odds", "market_popularity",
] + POWER_ITEM_COLUMNS

QUERY = f"""
    SELECT
        e.race_id, e.horse_id, r.race_date,
        r.course, r.surface, r.track_condition, r.race_class, r.direction, r.age_condition,
        r.round, r.distance,
        e.horse_number, e.frame_number, e.sex_age, e.weight,
        e.odds AS market_odds, e.popularity AS market_popularity,
        {", ".join(f"f.{col}" for col in POWER_ITEM_COLUMNS)},
        res.finish_position
    FROM entries e
    JOIN races r ON r.race_id = e.race_id
    JOIN features f ON f.race_id = e.race_id AND f.horse_id = e.horse_id
    LEFT JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
    ORDER BY r.race_date, e.race_id, e.horse_number
"""


def build_item_breakdown_dataset(db=None):
    """build_dataset.py::build_dataset()と同じ除外ルール（結果未確定・全馬デビュー戦の
    レースを除外）を適用しつつ、overall_scoreの代わりに8項目の生の列を持つ
    データセットを返す"""
    if db is None:
        db = DatabaseManager()

    conn = db.connect()
    df = pd.read_sql_query(QUERY, conn)
    conn.close()

    df = df[df["finish_position"].notna()].copy()

    race_all_debut = df.groupby("race_id")["stability_power"].transform(lambda s: s.isna().all())
    df = df[~race_all_debut].copy()

    df["label"] = (df["finish_position"] == 1).astype(int)
    df = df.drop(columns=["finish_position"])

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in CATEGORICAL_COLUMNS:
        df[col] = df[col].astype("category")

    return df


def main():
    print("=" * 70)
    print("dsk_Project")
    print("特徴量重要度分析: 既存8項目 vs 市場情報（overall_scoreは除外して比較）")
    print("=" * 70)

    dataset = build_item_breakdown_dataset()
    print(f"データセット件数: {len(dataset)}")
    print(f"期間: {dataset['race_date'].min()} 〜 {dataset['race_date'].max()}")
    print()

    model = train_model(dataset, FEATURE_COLUMNS_ITEM_BREAKDOWN, CATEGORICAL_COLUMNS)
    importances = feature_importance_report(
        model, FEATURE_COLUMNS_ITEM_BREAKDOWN, top_n=len(FEATURE_COLUMNS_ITEM_BREAKDOWN)
    )
    total = importances.sum()

    print(f"{'特徴量':16s}{'重要度(gain)':>16s}{'構成比(%)':>10s}")
    print("-" * 44)
    for col, val in importances.items():
        label = ITEM_LABELS.get(col, col)
        print(f"{label:16s}{val:16,.1f}{val / total * 100:10.2f}")

    print()
    item_total = sum(importances.get(c, 0) for c in POWER_ITEM_COLUMNS)
    market_total = sum(importances.get(c, 0) for c in MARKET_COLUMNS)
    other_total = total - item_total - market_total
    print(f"8項目合計:              {item_total / total * 100:6.2f}%")
    print(f"市場情報合計(オッズ+人気): {market_total / total * 100:6.2f}%")
    print(f"その他(レース条件等):     {other_total / total * 100:6.2f}%")

    print()
    print("--- 8項目のみの内訳（8項目合計を100%として） ---")
    for col in POWER_ITEM_COLUMNS:
        val = importances.get(col, 0)
        pct = val / item_total * 100 if item_total else 0
        print(f"  {ITEM_LABELS[col]:8s}: {pct:6.2f}%")


if __name__ == "__main__":
    main()
