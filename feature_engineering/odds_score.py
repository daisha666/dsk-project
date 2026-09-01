"""
dsk_Project
特徴量生成: オッズ反映後の最終指数 (odds_adjusted_score) と
            素点ベース／オッズ反映後の両順位 (raw_rank / odds_adjusted_rank)
Version 0.1

本プロジェクトの核心（開発指示書 2.3・2.4）。
「競馬予想2」のAK列の数式をそのまま踏襲する:

    odds_adjusted_score = overall_score * (1 / odds) * ODDS_SCORE_MULTIPLIER

    - overall_score: 8項目の合計 ×（1 + pace_bias_adjustment）（素点の総合力、AC列相当）
    - odds: 単勝オッズ（低い＝人気馬ほど倍率が大きくなる設計）
    - ODDS_SCORE_MULTIPLIER: config.config 参照（初期値8、Stage2以降でチューニング対象）

B案（開発指示書2.4）:
    印（◎○▲△☆）は odds_adjusted_rank（オッズ反映後の順位）を基準とする。
    raw_rank（素点ベースの順位）も並行して記録し、どちらの方式が実際に
    回収率が良いか継続比較できるようにする。

このモジュールは features テーブルに overall_score・market_odds が
既に保存されている（＝8項目の特徴量生成が完了している）ことを前提とする。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from config.config import ODDS_SCORE_MULTIPLIER
from database.db_manager import DatabaseManager


class OddsScoreFeatureBuilder:
    """overall_scoreとオッズから、オッズ反映後の最終指数・素点/オッズ反映後の
    両順位を計算し、featuresテーブルへ保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def compute_odds_adjusted_score(self, overall_score, odds):
        """overall_score * (1/odds) * ODDS_SCORE_MULTIPLIER を返す。
        overall_scoreまたはoddsが無い（None・0以下）場合はNone"""
        if overall_score is None or odds is None or odds <= 0:
            return None
        return overall_score * (1 / odds) * ODDS_SCORE_MULTIPLIER

    def compute_ranks(self, race_entries):
        """
        race_entries: 同一レース内の [(horse_id, overall_score, odds_adjusted_score), ...]
        戻り値: {horse_id: {"raw_rank": int|None, "odds_adjusted_rank": int|None}}

        値がNoneの馬（データ不足）は順位付けの対象外（rank=None）とする。
        """
        by_overall = sorted(
            [e for e in race_entries if e[1] is not None],
            key=lambda e: e[1],
            reverse=True,
        )
        by_odds_adjusted = sorted(
            [e for e in race_entries if e[2] is not None],
            key=lambda e: e[2],
            reverse=True,
        )

        ranks = {e[0]: {"raw_rank": None, "odds_adjusted_rank": None} for e in race_entries}

        for i, (horse_id, _, _) in enumerate(by_overall, start=1):
            ranks[horse_id]["raw_rank"] = i

        for i, (horse_id, _, _) in enumerate(by_odds_adjusted, start=1):
            ranks[horse_id]["odds_adjusted_rank"] = i

        return ranks

    def save_feature(self, race_id, horse_id, odds_adjusted_score, raw_rank, odds_adjusted_rank):
        sql = """
            INSERT INTO features (
                race_id, horse_id, odds_adjusted_score, raw_rank, odds_adjusted_rank
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                odds_adjusted_score = excluded.odds_adjusted_score,
                raw_rank = excluded.raw_rank,
                odds_adjusted_rank = excluded.odds_adjusted_rank
        """
        self.db.execute(sql, (race_id, horse_id, odds_adjusted_score, raw_rank, odds_adjusted_rank))

    def fetch_race_ids(self):
        """overall_scoreが計算済みのrace_id一覧を返す"""
        rows = self.db.fetchall("""
            SELECT DISTINCT race_id FROM features WHERE overall_score IS NOT NULL
        """)
        return [r[0] for r in rows]

    def fetch_race_entries(self, race_id):
        """対象レースの [(horse_id, overall_score, market_odds), ...] を返す。
        オッズはentries.odds（Yahoo競馬の収集元。collectors/yahoo_result_collector.py /
        yahoo_denma_collector.pyが保存）から取得する。features.market_oddsという同名の
        列がスキーマ上は存在するが、これを書き込むbuilderが無く常にNULLのため
        使ってはいけない（バグの温床になっていた。ai/build_dataset.pyも同じ理由で
        entries.oddsを直接使っている）"""
        return self.db.fetchall("""
            SELECT f.horse_id, f.overall_score, e.odds
            FROM features f
            JOIN entries e ON e.race_id = f.race_id AND e.horse_id = f.horse_id
            WHERE f.race_id = ?
        """, (race_id,))

    def build(self, log=print):
        """overall_score計算済みの全レースについて、オッズ反映後スコア・両順位を計算・保存する"""
        race_ids = self.fetch_race_ids()

        for race_id in race_ids:
            rows = self.fetch_race_entries(race_id)

            race_entries = []
            for horse_id, overall_score, odds in rows:
                odds_adjusted_score = self.compute_odds_adjusted_score(overall_score, odds)
                race_entries.append((horse_id, overall_score, odds_adjusted_score))

            ranks = self.compute_ranks(race_entries)

            for horse_id, overall_score, odds_adjusted_score in race_entries:
                self.save_feature(
                    race_id, horse_id, odds_adjusted_score,
                    ranks[horse_id]["raw_rank"], ranks[horse_id]["odds_adjusted_rank"],
                )

        log(f"完了: 対象レース数={len(race_ids)}")

        return {"total_races": len(race_ids)}


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("OddsScoreFeatureBuilder 実行")
    print("=" * 40)

    builder = OddsScoreFeatureBuilder()
    builder.build()
