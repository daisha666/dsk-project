"""
dsk_Project
脚質推定ロジックの診断（調査専用・本番コードは変更しない）
Version 0.1

feature_engineering/kyakushitsu_power.pyの脚質推定は、1走ごとに
「その走で記録された全コーナー通過順位の平均」を頭数で正規化して
relative_position（0=先頭付近, 1=最後方付近）を求め、直近5走の平均を
固定しきい値（0.15/0.40/0.70）で4区分に分類している。

①現行方式の実際の分布（区分の偏り）を確認する。
②「全コーナー平均」ではなく「最終コーナーのみ」を使う代替方式を試し、
  分布・区分ごとの実際の勝率/複勝率の違い（区分が意味のある差を
  作れているか）を比較する。
③固定しきい値の代わりに分位点（quartile）ベースのしきい値にした場合の
  分布バランスも参考として確認する。
"""

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.kyakushitsu_power import (
    NIGE_MAX, SASHI_MAX, SENKO_MAX, classify_running_style, parse_passing,
)

QUERY = """
    SELECT res.race_id, res.horse_id, res.passing, res.finish_position,
           (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = res.race_id) AS field_size
    FROM results res
    WHERE res.passing IS NOT NULL AND res.finish_position IS NOT NULL
"""


def relative_position_avg_corners(positions, field_size):
    """現行方式: 記録された全コーナーの平均通過順位を使う"""
    if not positions or not field_size or field_size <= 1:
        return None
    avg_position = sum(positions) / len(positions)
    return (avg_position - 1) / (field_size - 1)


def relative_position_final_corner(positions, field_size):
    """代替方式: 最終コーナー（直線に入る直前）の通過順位だけを使う。
    最終コーナーの位置取りの方が、道中の順位より実際の脚質（差し・追込か
    どうか）を反映しやすいという競馬の一般的な見方に基づく"""
    if not positions or not field_size or field_size <= 1:
        return None
    return (positions[-1] - 1) / (field_size - 1)


def main():
    db = DatabaseManager()
    rows = db.fetchall(QUERY)
    print(f"対象走数: {len(rows)}")

    records = []
    for race_id, horse_id, passing, finish_position, field_size in rows:
        positions = parse_passing(passing)
        if not positions:
            continue
        rel_avg = relative_position_avg_corners(positions, field_size)
        rel_final = relative_position_final_corner(positions, field_size)
        records.append({
            "race_id": race_id, "horse_id": horse_id,
            "finish_position": finish_position, "field_size": field_size,
            "n_corners": len(positions),
            "rel_avg": rel_avg, "rel_final": rel_final,
        })

    df = pd.DataFrame(records)
    print(f"パース成功走数: {len(df)}")
    print(f"コーナー記録数の内訳: {dict(Counter(df['n_corners']))}")

    print()
    print("=== ①現行方式（全コーナー平均）の relative_position 分布 ===")
    print(df["rel_avg"].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]))

    print()
    print("=== ②代替方式（最終コーナーのみ）の relative_position 分布 ===")
    print(df["rel_final"].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]))

    def style_summary(col, label):
        styles = df[col].apply(classify_running_style)
        counts = styles.value_counts()
        total = counts.sum()
        print(f"\n--- {label}: 区分ごとの件数・構成比・勝率・複勝率（1走単位、直近5走平均ではなく単走ベースの参考値） ---")
        for style in ["逃げ", "先行", "差し", "追込"]:
            mask = styles == style
            n = mask.sum()
            if n == 0:
                print(f"  {style}: 0件")
                continue
            win_rate = (df.loc[mask, "finish_position"] == 1).mean() * 100
            place_rate = (df.loc[mask, "finish_position"] <= 3).mean() * 100
            print(f"  {style}: {n:7d}件 ({n / total * 100:5.1f}%)  勝率={win_rate:5.2f}%  複勝率={place_rate:5.2f}%")

    style_summary("rel_avg", "①現行方式（全コーナー平均、しきい値0.15/0.40/0.70）")
    style_summary("rel_final", "②代替方式（最終コーナーのみ、しきい値0.15/0.40/0.70）")

    print()
    print("=== ③参考: 現行方式(rel_avg)を分位点ベースのしきい値にした場合の分布バランス ===")
    q = df["rel_avg"].quantile([0.15, 0.55, 0.85]).values
    print(f"分位点しきい値（15%/55%/85%）: {q.round(3).tolist()}  ※逃げ・追込を少数派、先行・差しを多数派とする現行の意図に近い比率で設定")

    def classify_quantile(x):
        if pd.isna(x):
            return None
        if x <= q[0]:
            return "逃げ"
        if x <= q[1]:
            return "先行"
        if x <= q[2]:
            return "差し"
        return "追込"

    styles_q = df["rel_avg"].apply(classify_quantile)
    counts_q = styles_q.value_counts()
    total_q = counts_q.sum()
    for style in ["逃げ", "先行", "差し", "追込"]:
        n = counts_q.get(style, 0)
        print(f"  {style}: {n:7d}件 ({n / total_q * 100:5.1f}%)")


if __name__ == "__main__":
    main()
