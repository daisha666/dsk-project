"""
dsk_Project
条件別重みマスター（condition_weights）インポート
Version 0.1

data/condition_weights.csv（271行、旧「競馬予想2」condition_masterシート相当）を
condition_weightsテーブルへ投入する。CSVの「条件キー」列（例:"東京芝1000"）は
競馬場名・芝orダート・距離の3つに分解してスキーマ（course, surface, distance）に
合わせる。

サーフェス表記の変換:
  CSVは「芝」「ダ」で区別しているが、races.surfaceはYahoo競馬側の表記に合わせて
  「芝」「ダート」を使っている（collectors/yahoo_result_collector.pyのSURFACE_PATTERN
  参照）。異なる表記のままではfeature_engineering側でraces.surfaceと突合できないため、
  「ダ」は「ダート」に変換して保存する。

列の対応（開発指示書2.2の8項目のうち、脚質力だけは正規化せず脚質区分ごとの
重み値をそのまま使うため、CSV側も差し/先行/逃げ/追込の4列に分かれている）:
  CSV列      -> condition_weightsの列
  上がり重み  -> weight_agari
  差し重み    -> weight_sashi
  先行重み    -> weight_senko
  逃げ重み    -> weight_nige
  追込重み    -> weight_oikomi
  騎手重み    -> weight_jockey
  距離重み    -> weight_distance
  回り重み    -> weight_turn
  安定重み    -> weight_stability
  血統重み    -> weight_pedigree
  調教師重み  -> weight_trainer
"""

import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from config.config import DATA_DIR
from database.db_manager import DatabaseManager

CSV_PATH = DATA_DIR / "condition_weights.csv"

CONDITION_KEY_PATTERN = re.compile(
    r"^(東京|中山|阪神|京都|中京|新潟|福島|小倉|札幌|函館)(芝|ダ)(\d+)$"
)

SURFACE_MAP = {"芝": "芝", "ダ": "ダート"}


def parse_condition_key(condition_key):
    """"東京芝1000" -> ("東京", "芝", 1000) / "中山ダ1800" -> ("中山", "ダート", 1800)"""
    match = CONDITION_KEY_PATTERN.match(condition_key)
    if not match:
        raise ValueError(f"条件キーの形式が不正: {condition_key!r}")

    course, surface_raw, distance_str = match.groups()
    return course, SURFACE_MAP[surface_raw], int(distance_str)


def load_rows(csv_path=CSV_PATH):
    """CSVを読み込み、condition_weightsへのINSERT用パラメータのリストを返す"""
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for line in reader:
            course, surface, distance = parse_condition_key(line["条件キー"])

            rows.append((
                course, surface, distance,
                float(line["上がり重み"]),
                float(line["逃げ重み"]),
                float(line["先行重み"]),
                float(line["差し重み"]),
                float(line["追込重み"]),
                float(line["騎手重み"]),
                float(line["距離重み"]),
                float(line["回り重み"]),
                float(line["安定重み"]),
                float(line["血統重み"]),
                float(line["調教師重み"]),
            ))

    return rows


def import_condition_weights(csv_path=CSV_PATH, log=print):
    db = DatabaseManager()
    rows = load_rows(csv_path)

    sql = """
        INSERT INTO condition_weights (
            course, surface, distance,
            weight_agari, weight_nige, weight_senko, weight_sashi, weight_oikomi,
            weight_jockey, weight_distance, weight_turn, weight_stability,
            weight_pedigree, weight_trainer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(course, surface, distance) DO UPDATE SET
            weight_agari = excluded.weight_agari,
            weight_nige = excluded.weight_nige,
            weight_senko = excluded.weight_senko,
            weight_sashi = excluded.weight_sashi,
            weight_oikomi = excluded.weight_oikomi,
            weight_jockey = excluded.weight_jockey,
            weight_distance = excluded.weight_distance,
            weight_turn = excluded.weight_turn,
            weight_stability = excluded.weight_stability,
            weight_pedigree = excluded.weight_pedigree,
            weight_trainer = excluded.weight_trainer
    """
    for row in rows:
        db.execute(sql, row)

    log(f"投入完了: {len(rows)}行")
    return {"rows_imported": len(rows)}


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("condition_weights インポート")
    print("=" * 40)

    import_condition_weights()
