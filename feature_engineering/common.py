"""
dsk_Project
特徴量生成 共通ユーティリティ
Version 0.1

複数の特徴量カテゴリ（8項目・オッズ反映後スコアなど）で共通して使う
「馬ごと・エンティティごとのレース履歴読み込み」と「データリーケージ防止
（対象レースより前のデータだけに絞り込む）」処理をまとめたモジュール。
PROJECT_EVのfeature_engineering/common.pyと同一設計（テーブル構造が
races / entries / results / horses で共通のため、そのまま踏襲できる）。

データリーケージ防止のルール:
  対象レース（race_id, horse_id）の特徴量を計算する際、その馬の過去レースは
  「races.race_date が対象レースの race_date より厳密に小さい（<）」ものだけを使う。
  対象レース当日・それ以降のレースは一切含めない。

  ただし、騎手・調教師のように「馬単位ではない」履歴（load_entity_histories）
  は、同じ開催日に複数レースへ騎乗・管理することが普通にあるため、
  race_date だけでは同日内の前後関係を区別できない。この場合は
  get_past_races() に target_round を渡し、(race_date, round) の複合キーで
  対象レースより前かどうかを判定する。
"""

import sys
from bisect import bisect_left
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from database.db_manager import DatabaseManager


def load_horse_histories(db=None):
    """馬ごとの全レース履歴を race_date 昇順で読み込む。
    戻り値: {horse_id: [レース情報dictのリスト（race_date昇順）]}"""
    if db is None:
        db = DatabaseManager()

    sql = """
        SELECT
            e.horse_id,
            r.race_id,
            r.race_date,
            r.distance,
            r.course,
            r.direction,
            r.race_class,
            e.weight,
            e.running_style,
            res.finish_position,
            res.last3f,
            res.passing,
            (SELECT COUNT(*) FROM entries e2 WHERE e2.race_id = r.race_id) AS field_size,
            res.finish_time
        FROM entries e
        JOIN races r ON r.race_id = e.race_id
        LEFT JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
        ORDER BY e.horse_id, r.race_date, r.race_id
    """
    rows = db.fetchall(sql)

    histories = {}
    for (horse_id, race_id, race_date, distance, course, direction, race_class, weight,
         running_style, finish_position, last3f, passing, field_size, finish_time) in rows:
        histories.setdefault(horse_id, []).append({
            "race_id": race_id,
            "race_date": race_date,
            "distance": distance,
            "course": course,
            "direction": direction,
            "race_class": race_class,
            "weight": weight,
            "running_style": running_style,
            "finish_position": finish_position,
            "last3f": last3f,
            "passing": passing,
            "field_size": field_size,
            "finish_time": finish_time,
        })

    return histories


def load_entity_histories(entity_column, db=None):
    """騎手・調教師ごとの全騎乗/管理レース履歴を (race_date, round) 昇順で読み込む。
    entity_column: "jockey" または "trainer"（entriesテーブルの列名）
    戻り値: {エンティティ名: [レース情報dictのリスト（(race_date, round)昇順）]}"""
    if entity_column not in ("jockey", "trainer"):
        raise ValueError(f"unsupported entity_column: {entity_column}")

    if db is None:
        db = DatabaseManager()

    sql = f"""
        SELECT
            e.{entity_column} AS entity_name,
            r.race_id,
            r.race_date,
            r.round,
            res.finish_position
        FROM entries e
        JOIN races r ON r.race_id = e.race_id
        LEFT JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
        WHERE e.{entity_column} IS NOT NULL
        ORDER BY e.{entity_column}, r.race_date, r.round
    """
    rows = db.fetchall(sql)

    histories = {}
    for entity_name, race_id, race_date, round_no, finish_position in rows:
        histories.setdefault(entity_name, []).append({
            "race_id": race_id,
            "race_date": race_date,
            "round": round_no,
            "finish_position": finish_position,
        })

    return histories


def load_pedigree_histories(pedigree_column, db=None):
    """血統（父/母父）ごとの産駒レース履歴を (race_date, round) 昇順で読み込む。
    pedigree_column: "sire" または "damsire"（horsesテーブルの列名）
    戻り値: {血統名: [レース情報dictのリスト（(race_date, round)昇順）]}
    各要素に horse_id を含む。対象馬自身の過去走を集計から除外したい場合に
    利用側でフィルタできるようにするため"""
    if pedigree_column not in ("sire", "damsire"):
        raise ValueError(f"unsupported pedigree_column: {pedigree_column}")

    if db is None:
        db = DatabaseManager()

    sql = f"""
        SELECT
            h.{pedigree_column} AS entity_name,
            e.horse_id,
            r.race_id,
            r.race_date,
            r.round,
            res.finish_position
        FROM entries e
        JOIN races r ON r.race_id = e.race_id
        JOIN horses h ON h.horse_id = e.horse_id
        LEFT JOIN results res ON res.race_id = e.race_id AND res.horse_id = e.horse_id
        WHERE h.{pedigree_column} IS NOT NULL
        ORDER BY h.{pedigree_column}, r.race_date, r.round
    """
    rows = db.fetchall(sql)

    histories = {}
    for entity_name, horse_id, race_id, race_date, round_no, finish_position in rows:
        histories.setdefault(entity_name, []).append({
            "horse_id": horse_id,
            "race_id": race_id,
            "race_date": race_date,
            "round": round_no,
            "finish_position": finish_position,
        })

    return histories


def get_past_races(history, target_race_date, target_round=None, limit=None):
    """
    history: ある馬（または騎手・調教師）の全レース履歴
              （race_date昇順、target_roundを使う場合は(race_date, round)昇順のリスト）
    target_race_date: 対象レースの race_date（このレース自体・それ以降は絶対に含めない）
    target_round: 対象レースの round。
              None（デフォルト）の場合は従来通り race_date だけで判定する
              （馬は1日1走なのでこれで安全）。
              値を渡した場合は (race_date, round) の複合キーで判定し、同じ
              race_date内でも target_round より前のレースだけに絞り込む
              （騎手・調教師のように同日複数レースがあり得る場合に使う）。
    limit: 直近何件に絞るか（Noneなら絞り込まず全件を返す）
    """
    if target_round is None:
        keys = [h["race_date"] for h in history]
        cutoff_key = target_race_date
    else:
        keys = [(h["race_date"], h["round"]) for h in history]
        cutoff_key = (target_race_date, target_round)

    cutoff = bisect_left(keys, cutoff_key)
    past = history[:cutoff]

    if limit is not None:
        past = past[-limit:]

    return past


def fetch_targets(db=None):
    """entriesテーブルにある全 (race_id, horse_id, race_date) を返す"""
    if db is None:
        db = DatabaseManager()

    return db.fetchall("""
        SELECT e.race_id, e.horse_id, r.race_date
        FROM entries e
        JOIN races r ON r.race_id = e.race_id
        ORDER BY r.race_date, e.race_id
    """)


def group_by_race(rows):
    """先頭要素がrace_idのタプル列を、race_id単位でグループ化する
    （rowsは事前にrace_id順にソートされている前提。呼び出し元のSQLは
    いずれもORDER BY race_dateまたはrace_idしているため保証される）。
    戻り値: {race_id: [row, ...]}"""
    groups = {}
    for row in rows:
        groups.setdefault(row[0], []).append(row)
    return groups


def normalize_min_max(values, invert=False):
    """8項目のうち脚質力以外で使う「レース内Min-Max正規化」。
    values: {horse_id: 素点(float) or None}（Noneはデータ欠損＝正規化対象外）
    invert: Trueなら値が小さいほど高スコア（0に近い方が1に近くなる）になるよう
            反転する（上がり力＝タイムは速い＝小さい方が良いため使う）。
            距離力・回り力・安定力・血統力・騎手力・調教師力は複勝率/連対率
            そのもの（大きい方が良い）なのでinvert=False。
    戻り値: {horse_id: 正規化後の値(0.0~1.0) or None}
            同一レース内の全馬が同値（max==min）の場合は差がつかないため
            全馬0.5とする。有効値が1件も無ければ全馬Noneを返す。
    """
    valid = {h: v for h, v in values.items() if v is not None}
    if not valid:
        return {h: None for h in values}

    lo, hi = min(valid.values()), max(valid.values())

    result = {}
    for horse_id, v in values.items():
        if v is None:
            result[horse_id] = None
            continue
        if hi == lo:
            result[horse_id] = 0.5
            continue
        frac = (v - lo) / (hi - lo)
        result[horse_id] = (1 - frac) if invert else frac

    return result


def load_condition_weights(db=None):
    """condition_weightsの全行を読み込む。
    戻り値: {(course, surface, distance): {"agari":.., "nige":.., "senko":..,
             "sashi":.., "oikomi":.., "jockey":.., "distance":.., "turn":..,
             "stability":.., "pedigree":.., "trainer":..}}"""
    if db is None:
        db = DatabaseManager()

    rows = db.fetchall("""
        SELECT course, surface, distance,
               weight_agari, weight_nige, weight_senko, weight_sashi, weight_oikomi,
               weight_jockey, weight_distance, weight_turn, weight_stability,
               weight_pedigree, weight_trainer
        FROM condition_weights
    """)

    weights = {}
    for (course, surface, distance, w_agari, w_nige, w_senko, w_sashi, w_oikomi,
         w_jockey, w_distance, w_turn, w_stability, w_pedigree, w_trainer) in rows:
        weights[(course, surface, distance)] = {
            "agari": w_agari,
            "nige": w_nige, "senko": w_senko, "sashi": w_sashi, "oikomi": w_oikomi,
            "jockey": w_jockey, "distance": w_distance, "turn": w_turn,
            "stability": w_stability, "pedigree": w_pedigree, "trainer": w_trainer,
        }

    return weights
