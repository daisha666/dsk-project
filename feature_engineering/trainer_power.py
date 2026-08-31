"""
dsk_Project
特徴量生成: 調教師力 (trainer_power)
Version 0.2

開発指示書 2.2「調教師力」:
  元データ = 調教師複勝率（対象レースより前の、当該調教師の通算複勝率＝3着内率）。
  レース内Min-Max正規化してから condition_weights.weight_trainer を掛ける。
  ロジックはjockey_power.pyと対称。

データソースの方針転換（v0.1からの変更）:
  当初はittai.net（騎手・調教師リーディング）を想定していたが、実際に確認した
  ところ勝率・連対率・3着内率・単回・複回はnote.com有料メンバーシップ限定で、
  無料で取得できるのは名前と内部ランクコードのみ（複勝率の実数値は取れない）
  ことが判明した。そのためittai.netは採用せず、PROJECT_EVの
  feature_engineering/jockey_trainer_stats.pyと同じ方針――自前で収集した
  Yahoo競馬のレース結果（entries/results）から調教師ごとの複勝率を直接計算する
  ――に切り替えた（jockey_trainer_leadingテーブル・ittai_leading_collector.py
  は不要になったため削除済み）。

同日複数レース管理のリーケージ防止はjockey_power.pyと同じ
（common.get_past_races()にtarget_roundを渡し、(race_date, round)の
複合キーで対象レースより前かどうかを判定する）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.common import get_past_races, load_entity_histories


def compute_place_rate(matching):
    """matchingレース群から複勝率（finish_position<=3の割合）を計算する。空ならNone"""
    if not matching:
        return None
    places = sum(1 for p in matching if p["finish_position"] <= 3)
    return places / len(matching)


class TrainerPowerFeatureBuilder:
    """調教師の通算複勝率（自前計算）から調教師力を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_trainer_place_rate(self, histories, trainer, target_race_date, target_round):
        """
        histories: load_entity_histories("trainer")の戻り値
        trainer: 対象レースでの調教師名
        target_race_date, target_round: 対象レースの日付・R番号（同日内の前後判定に使う）
        戻り値: (複勝率 or None, 使用したレース数)
        """
        if not trainer:
            return None, 0

        history = histories.get(trainer, [])
        past_races = get_past_races(history, target_race_date, target_round=target_round)
        matching = [p for p in past_races if p["finish_position"] is not None]

        return compute_place_rate(matching), len(matching)

    def save_feature(self, race_id, horse_id, trainer_power):
        sql = """
            INSERT INTO features (race_id, horse_id, trainer_power)
            VALUES (?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                trainer_power = excluded.trainer_power
        """
        self.db.execute(sql, (race_id, horse_id, trainer_power))

    def fetch_targets(self):
        """entriesテーブルにある全 (race_id, horse_id, race_date, round, trainer) を返す"""
        return self.db.fetchall("""
            SELECT e.race_id, e.horse_id, r.race_date, r.round, e.trainer
            FROM entries e
            JOIN races r ON r.race_id = e.race_id
            ORDER BY r.race_date, r.round
        """)

    def build(self, log=print):
        """TODO: レース内Min-Max正規化・condition_weights適用は8項目が揃ってから
        まとめて実装する（正規化はレース単位でしか行えないため）"""
        raise NotImplementedError("レース内正規化・重み付けロジックを実装する")


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("TrainerPowerFeatureBuilder 読み込み成功")
    print("=" * 40)
