"""
dsk_Project
特徴量生成: 総合力 (overall_score)
Version 0.1

開発指示書 2.2:
  総合力 = 8項目の合計 ×（1 + 脚質別ペースバイアス補正）

8項目（agari_power, kyakushitsu_power, jockey_power, distance_power,
turn_power, stability_power, pedigree_power, trainer_power）は、いずれも
各自のFeatureBuilder（agari_power.py等）が「レース内Min-Max正規化 ×
condition_weights」を既に適用した値をfeaturesテーブルへ保存している
（脚質力だけはMin-Max正規化を行わず区分ごとの重み値をそのまま使うが、
「重み適用済みの値」という点では他の7項目と同じ）ため、overall_scoreの
算出はこれらを単純合算するだけでよい。

pace_bias_adjustment（ペースバイアス補正）は現時点で未実装
（feature_engineering/pace_bias.py参照）のため常にNULLであり、
NULLは0（補正なし、係数1倍）として扱う。8項目のうち一部がNULL
（該当条件のcondition_weightsが無い、過去走データが無い等）の場合も
同様に0として合算する（＝その項目の寄与が無いだけで、算出自体は続行する）。
新馬戦・データ皆無の馬はoverall_score=0になり得るが、開発指示書の方針上
新馬戦は学習・バックテスト対象から除外するため実害は小さい。

8項目すべての生成（agari_power.py 〜 trainer_power.py の各build()）を
先に実行してから、本モジュールのbuild()を実行すること。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager

POWER_COLUMNS = [
    "agari_power", "kyakushitsu_power", "jockey_power", "distance_power",
    "turn_power", "stability_power", "pedigree_power", "trainer_power",
]


class OverallScoreFeatureBuilder:
    """8項目の合計 ×（1 + pace_bias_adjustment）を計算し、
    featuresテーブルのoverall_score列へ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def build(self, log=print):
        """featuresテーブルの全行についてoverall_scoreを一括更新する"""
        sum_expr = " + ".join(f"COALESCE({col}, 0)" for col in POWER_COLUMNS)

        sql = f"""
            UPDATE features
            SET overall_score = ({sum_expr}) * (1 + COALESCE(pace_bias_adjustment, 0))
        """
        self.db.execute(sql)

        total = self.db.fetchone("SELECT COUNT(*) FROM features")[0]
        log(f"完了: overall_score更新件数={total}")

        return {"total": total}


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("OverallScoreFeatureBuilder 実行")
    print("=" * 40)

    builder = OverallScoreFeatureBuilder()
    builder.build()
