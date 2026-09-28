"""
dsk_Project
特徴量生成: 同日トラックバイアス補正 (bias_front_runner_score / bias_inside_post_score)
Version 1.0（2026-09-07、実装）

開発指示書2.2の元々のTODO「出走馬の脚質構成からハイペース/スローペースを
予想する」は、レース単体の情報だけでは根拠が弱く見送った。代わりに、
最終版(ハイブリッド)のバイアスデータ設計を参考に、「同日・同競馬場で
既に確定したレース結果から、先行有利/内枠有利の傾向をリアルタイムに集計し、
まだ確定していない残りレースの特徴量として使う」方式を採用する
（README「トラックバイアス補正の実装（2026-09-07）」参照）。

overall_score（8項目の合算値。feature_engineering/overall_score.py）には
含めない。理由: この2つは検証時、overall_scoreとは独立したLightGBMの
生特徴量として追加した状態で重要度・AUC・シャープレシオの改善を確認した
（ai/track_bias_experiment.py）。overall_score側の乗算補正
（pace_bias_adjustment列。当面未使用のまま）に混ぜ込む設計にすると、
検証していない別の統合方法になってしまうため、検証した設計をそのまま
本番へ反映する。

計算方法（リーケージ防止: 同日・同競馬場・自分より前のroundの確定済み
結果だけを使う。本番でも、その日の後のレースを予測する時点で先行レースの
結果は既に確定しているため、同じ条件で計算できる）:
  1. 対象レースと同じ(course, race_date)で、round が対象レースより小さく、
     結果が確定している馬をすべて集める。
  2. 各馬の「相対位置」（最終コーナー通過順位を頭数で正規化。0=先頭付近、
     1=最後方付近。kyakushitsu_power.py::compute_relative_positionと同じ式）
     が0.4以下の馬を「先行集団」、それ以外を「後方集団」とする。
  3. bias_front_runner_score = 先行集団の複勝率 - 後方集団の複勝率
     （正の値=今日この馬場は先行有利）
  4. 同様に馬番の頭数内での相対位置（0〜1）が0.5以下の馬を「内枠」、
     それ以外を「外枠」とし、bias_inside_post_score = 内枠複勝率 - 外枠複勝率
  5. 対象データが少なすぎる（前提サンプル数がMIN_PRIOR_SAMPLES未満。
     その日最初のレース等）場合は中立値0とする
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager
from feature_engineering.kyakushitsu_power import parse_passing

FRONT_THRESHOLD = 0.4
MIN_PRIOR_SAMPLES = 6


def _relative_position(passing, field_size):
    if passing is None or (isinstance(passing, float) and pd.isna(passing)):
        return None
    if not field_size or field_size <= 1:
        return None
    positions = parse_passing(passing)
    if not positions:
        return None
    return (positions[-1] - 1) / (field_size - 1)


def _place_threshold(field_size):
    return 3 if field_size >= 8 else 2


class PaceBiasFeatureBuilder:
    """同日・同競馬場の先行有利/内枠有利の傾向を集計し、featuresテーブルへ
    bias_front_runner_score・bias_inside_post_scoreとして保存するクラス"""

    def __init__(self):
        self.db = DatabaseManager()

    def _prior_same_day_stats(self, course, race_date, round_no):
        """対象レースより前のround・同日・同競馬場の確定済み結果から
        (bias_front_runner_score, bias_inside_post_score) を計算する"""
        rows = self.db.fetchall("""
            SELECT e.horse_number, res.finish_position, res.passing,
                   (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = e.race_id) AS field_size
            FROM races r
            JOIN entries e ON e.race_id = r.race_id
            JOIN results res ON res.race_id = r.race_id AND res.horse_id = e.horse_id
            WHERE r.course = ? AND r.race_date = ? AND r.round < ?
              AND res.finish_position IS NOT NULL
        """, (course, race_date, round_no))

        n_front = n_front_placed = 0
        n_back = n_back_placed = 0
        n_inside = n_inside_placed = 0
        n_outside = n_outside_placed = 0

        for horse_number, finish_position, passing, field_size in rows:
            if not field_size or field_size <= 1:
                continue
            threshold = _place_threshold(field_size)
            placed = finish_position is not None and finish_position <= threshold

            rel_pos = _relative_position(passing, field_size)
            if rel_pos is not None:
                if rel_pos <= FRONT_THRESHOLD:
                    n_front += 1
                    n_front_placed += int(placed)
                else:
                    n_back += 1
                    n_back_placed += int(placed)

            post_pct = (horse_number - 1) / (field_size - 1)
            if post_pct <= 0.5:
                n_inside += 1
                n_inside_placed += int(placed)
            else:
                n_outside += 1
                n_outside_placed += int(placed)

        front_score = 0.0
        if (n_front + n_back) >= MIN_PRIOR_SAMPLES and n_front > 0 and n_back > 0:
            front_score = (n_front_placed / n_front) - (n_back_placed / n_back)

        inside_score = 0.0
        if (n_inside + n_outside) >= MIN_PRIOR_SAMPLES and n_inside > 0 and n_outside > 0:
            inside_score = (n_inside_placed / n_inside) - (n_outside_placed / n_outside)

        return front_score, inside_score

    def save_feature(self, race_id, horse_id, front_score, inside_score):
        sql = """
            INSERT INTO features (race_id, horse_id, bias_front_runner_score, bias_inside_post_score)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(race_id, horse_id) DO UPDATE SET
                bias_front_runner_score = excluded.bias_front_runner_score,
                bias_inside_post_score = excluded.bias_inside_post_score
        """
        self.db.execute(sql, (race_id, horse_id, front_score, inside_score))

    def build_for_races(self, race_ids, log=print):
        """指定したrace_idだけについてbias_front_runner_score・
        bias_inside_post_scoreを計算・保存する（新規レース・オッズ自動更新
        サイクルでの再計算、両方から呼ばれる想定。同日の確定済みレースが
        増えるたびに値が変わるため、他の8項目と違い毎回再計算が必要）"""
        if not race_ids:
            log("対象レースなし。トラックバイアス計算はスキップ")
            return {"races": 0, "horses": 0}

        race_rows = self.db.fetchall(f"""
            SELECT race_id, course, race_date, round FROM races
            WHERE race_id IN ({",".join("?" * len(race_ids))})
        """, tuple(race_ids))

        n_horses = 0
        for race_id, course, race_date, round_no in race_rows:
            front_score, inside_score = self._prior_same_day_stats(course, race_date, round_no)

            horse_ids = self.db.fetchall("SELECT horse_id FROM entries WHERE race_id = ?", (race_id,))
            for (horse_id,) in horse_ids:
                self.save_feature(race_id, horse_id, front_score, inside_score)
                n_horses += 1

        log(f"トラックバイアス計算完了: 対象レース数={len(race_rows)} 対象頭数={n_horses}")
        return {"races": len(race_rows), "horses": n_horses}

    def build(self, log=print):
        """全レースを対象にフル再計算する（学習データ用の一括バックフィル）。
        件数が多いためbuild_for_races()のレース毎クエリではなく、ベクトル化した
        集計（pandas）でまとめて計算してから一括保存する"""
        rows = self.db.fetchall("""
            SELECT r.race_id, r.race_date, r.course, r.round,
                   e.horse_id, e.horse_number,
                   res.finish_position, res.passing,
                   (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = r.race_id) AS field_size
            FROM races r
            JOIN entries e ON e.race_id = r.race_id
            LEFT JOIN results res ON res.race_id = r.race_id AND res.horse_id = e.horse_id
            ORDER BY r.course, r.race_date, r.round
        """)
        raw = pd.DataFrame(rows, columns=[
            "race_id", "race_date", "course", "round", "horse_id", "horse_number",
            "finish_position", "passing", "field_size",
        ])

        all_race_horses = raw[["race_id", "horse_id"]].drop_duplicates()

        finished = raw.dropna(subset=["finish_position", "field_size"]).copy()
        finished = finished[finished["field_size"] > 1]
        finished["relative_position"] = finished.apply(
            lambda row: _relative_position(row["passing"], row["field_size"]), axis=1
        )
        finished["post_pct"] = (finished["horse_number"] - 1) / (finished["field_size"] - 1)
        finished["place_threshold"] = finished["field_size"].apply(_place_threshold)
        finished["is_placed"] = finished["finish_position"] <= finished["place_threshold"]
        finished["is_front"] = finished["relative_position"] <= FRONT_THRESHOLD
        finished["is_inside"] = finished["post_pct"] <= 0.5

        round_stats = finished.groupby(["course", "race_date", "round"]).apply(
            lambda g: pd.Series({
                "n_front": (g["relative_position"].notna() & g["is_front"]).sum(),
                "n_front_placed": (g["relative_position"].notna() & g["is_front"] & g["is_placed"]).sum(),
                "n_back": (g["relative_position"].notna() & ~g["is_front"]).sum(),
                "n_back_placed": (g["relative_position"].notna() & ~g["is_front"] & g["is_placed"]).sum(),
                "n_inside": g["is_inside"].sum(),
                "n_inside_placed": (g["is_inside"] & g["is_placed"]).sum(),
                "n_outside": (~g["is_inside"]).sum(),
                "n_outside_placed": (~g["is_inside"] & g["is_placed"]).sum(),
            }),
            include_groups=False,
        ).reset_index()

        round_stats = round_stats.sort_values(["course", "race_date", "round"])
        group_cols = ["course", "race_date"]
        cum_cols = ["n_front", "n_front_placed", "n_back", "n_back_placed",
                    "n_inside", "n_inside_placed", "n_outside", "n_outside_placed"]
        for col in cum_cols:
            round_stats[f"cum_{col}"] = round_stats.groupby(group_cols)[col].cumsum() - round_stats[col]

        front_rate = round_stats["cum_n_front_placed"] / round_stats["cum_n_front"].replace(0, np.nan)
        back_rate = round_stats["cum_n_back_placed"] / round_stats["cum_n_back"].replace(0, np.nan)
        inside_rate = round_stats["cum_n_inside_placed"] / round_stats["cum_n_inside"].replace(0, np.nan)
        outside_rate = round_stats["cum_n_outside_placed"] / round_stats["cum_n_outside"].replace(0, np.nan)

        enough_style = (round_stats["cum_n_front"] + round_stats["cum_n_back"]) >= MIN_PRIOR_SAMPLES
        enough_post = (round_stats["cum_n_inside"] + round_stats["cum_n_outside"]) >= MIN_PRIOR_SAMPLES

        round_stats["bias_front_runner_score"] = np.where(enough_style, (front_rate - back_rate).fillna(0), 0.0)
        round_stats["bias_inside_post_score"] = np.where(enough_post, (inside_rate - outside_rate).fillna(0), 0.0)

        race_round = raw[["race_id", "course", "race_date", "round"]].drop_duplicates()
        race_bias = race_round.merge(
            round_stats[["course", "race_date", "round", "bias_front_runner_score", "bias_inside_post_score"]],
            on=["course", "race_date", "round"], how="left",
        )
        race_bias["bias_front_runner_score"] = race_bias["bias_front_runner_score"].fillna(0.0)
        race_bias["bias_inside_post_score"] = race_bias["bias_inside_post_score"].fillna(0.0)

        merged = all_race_horses.merge(race_bias, on="race_id", how="left")
        merged["bias_front_runner_score"] = merged["bias_front_runner_score"].fillna(0.0)
        merged["bias_inside_post_score"] = merged["bias_inside_post_score"].fillna(0.0)

        # db.execute()は呼び出しごとにDB接続を開閉するため、14万件超の一括保存では
        # 致命的に遅い（8項目パイプラインで過去に判明した問題と同じ）。
        # 1本のコネクション・executemanyでまとめて保存する
        records = list(zip(
            merged["race_id"], merged["horse_id"],
            merged["bias_front_runner_score"].astype(float),
            merged["bias_inside_post_score"].astype(float),
        ))
        conn = self.db.connect()
        try:
            conn.executemany("""
                INSERT INTO features (race_id, horse_id, bias_front_runner_score, bias_inside_post_score)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(race_id, horse_id) DO UPDATE SET
                    bias_front_runner_score = excluded.bias_front_runner_score,
                    bias_inside_post_score = excluded.bias_inside_post_score
            """, records)
            conn.commit()
        finally:
            conn.close()

        n = len(records)
        log(f"トラックバイアス フル再計算完了: 対象件数={n}")
        return {"total": n}


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("PaceBiasFeatureBuilder 実行（フル再計算）")
    print("=" * 40)

    builder = PaceBiasFeatureBuilder()
    builder.build()
