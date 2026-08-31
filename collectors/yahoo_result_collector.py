"""
dsk_Project
Yahoo!スポーツナビ 競馬（sports.yahoo.co.jp/keiba/）: 結果・払戻金 収集
Version 0.1

対象ページ（2026-08-31にブラウザで実際に構造を確認済み）:
  - race/index/{race_id} … 結果（着順テーブル）と確定払戻金（同一ページ内）

  ※ keiba.yahoo.co.jp は廃止済み。必ず sports.yahoo.co.jp/keiba/ を使うこと。

保存先: races / horses / entries / results / payouts

出走表（denma）は yahoo_denma_collector.py が別途担当する
（発走前でも取得できる情報と、結果確定後にしか取得できない情報を
分離するため。PROJECT_EVのhistorical_race_collector.py /
upcoming_race_collector.pyの分割と同じ考え方）。

血統（父・母父）backfillについて:
  netkeiba（db.netkeiba.com）は使わない。Yahoo!スポーツナビ競馬の出馬表
  （race/denma/{race_id}）ページ自体に「父：X母：Y(母父：Z)」の形で
  血統が載っており、これはレース終了後もアーカイブとして残り続けることを
  確認済み（同一サイト内で完結するため、netkeibaとのhorse_id突合が
  不要になる利点がある）。ただし本コレクター（collect()）は結果ページ
  （race/index）しか見ないため、ここで新規にhorsesテーブルへ登録される
  馬のsire/damsireはNULLのままになる（yahoo_denma_collector.pyは
  「まだ結果が出ていないレース」だけを対象にしているため、こちらの
  収集対象とは重ならない）。この欠落を埋めるのが
  fetch_denma_pedigrees() / backfill_pedigrees()（PROJECT_EVの
  historical_race_collector.backfill_pedigreesと同じ設計）。
"""

import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.base_collector import BaseCollector
from database.db_manager import DatabaseManager


BASE_URL = "https://sports.yahoo.co.jp"

MEETING_ID_PATTERN = re.compile(r"/keiba/race/list/(\d{8})")
RACE_ID_PATTERN = re.compile(r"/keiba/race/index/(\d{10})$")

TITLE_DATE_PATTERN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
TITLE_COURSE_PATTERN = re.compile(r"）\s*(\S+?)競馬場")

# レース条件行「ダート・左 1200m」形式（h2/条件テキストの中に出現）
SURFACE_PATTERN = re.compile(r"^(芝|ダート)・(\S+?)\s*(\d+)m$")
JUMP_PATTERN = re.compile(r"^障害")
AGE_CONDITION_PATTERN = re.compile(r"^\d+歳(以上)?$")

GRADE_SUFFIXES = ["GIII", "GII", "GI", "JpnIII", "JpnII", "JpnI", "L"]

# タイム "1:11.5" -> 71.5秒
FINISH_TIME_PATTERN = re.compile(r"^(\d+):(\d{2}\.\d)")

# 馬名セル末尾の性齢/馬体重（"牡3/484(+2)" 等。Bはブリンカー）
HORSE_SUFFIX_PATTERN = re.compile(r"^(牡|牝|セ)(\d+)/(\d+)(?:\([+-]?\d+\))?(?:/B)?$")

# 通過順位+上がり3F結合セル "06-0636.3" -> 通過順位 "06-06" + 上がり3F "36.3"
PASSING_LAST3F_PATTERN = re.compile(r"^(\d{2}(?:-\d{2})*)(\d{2}\.\d)$")

# 人気(オッズ)セル "6(19.8)" -> 人気6 オッズ19.8
POPULARITY_ODDS_PATTERN = re.compile(r"^(\d+)\(([\d.]+)\)$")

# 出馬表（denma）ページの血統セル「父：X母：Y(母父：Z)」（血統backfillで使う）
DENMA_PEDIGREE_PATTERN = re.compile(r"父：(.+?)母：(.+?)\(母父：(.+?)\)")

PAYOUT_LABELS = {"単勝", "複勝", "枠連", "馬連", "ワイド", "馬単", "3連複", "3連単"}
UNORDERED_BET_TYPES = {"枠連", "馬連", "ワイド", "3連複"}

HORSE_LINK_PATTERN = re.compile(r"/directory/horse/(\d+)/?$")


class YahooResultCollector(BaseCollector):
    """Yahoo!スポーツナビ 競馬から結果・払戻金を取得し、
    races / horses / entries / results / payouts へ保存するクラス"""

    def __init__(self, sleep_sec=0.6):
        super().__init__()
        self.db = DatabaseManager()
        self.sleep_sec = sleep_sec

        self.stats = {
            "meetings_checked": 0,
            "meetings_matched": 0,
            "races": 0,
            "entries": 0,
            "results": 0,
            "jump_races_skipped": 0,
            "entries_skipped_no_horse_number": 0,
            "errors": [],
        }

    # ------------------------------------------------------------
    # 開催日の発見
    # ------------------------------------------------------------

    def fetch_meeting_ids(self, year, month):
        """月間スケジュールページ（schedule/monthly）から開催日ID（8桁）を収集する"""
        url = f"{BASE_URL}/keiba/schedule/monthly/?year={year}&month={month}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        return set(MEETING_ID_PATTERN.findall(str(soup)))

    def months_between(self, start_date, end_date):
        months = []
        y, m = start_date.year, start_date.month
        while (y, m) <= (end_date.year, end_date.month):
            months.append((y, m))
            m = m + 1 if m < 12 else 1
            if m == 1:
                y += 1
        return months

    def fetch_meeting(self, meeting_id):
        """race/list/{meeting_id} から開催日・競馬場・当日のrace_id一覧を取得する"""
        url = f"{BASE_URL}/keiba/race/list/{meeting_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        title = soup.title.get_text(strip=True) if soup.title else ""
        date_match = TITLE_DATE_PATTERN.search(title)
        if not date_match:
            return None

        year, month, day = (int(v) for v in date_match.groups())
        course_match = TITLE_COURSE_PATTERN.search(title)

        race_ids = sorted({
            m.group(1) for a in soup.find_all("a", href=RACE_ID_PATTERN)
            for m in [RACE_ID_PATTERN.search(a["href"])]
        })

        return {
            "meeting_date": date(year, month, day),
            "course": course_match.group(1) if course_match else None,
            "race_ids": race_ids,
        }

    # ------------------------------------------------------------
    # 結果ページ（race/index）: レース情報
    # ------------------------------------------------------------

    def parse_race_info(self, soup):
        """レース名・距離・馬場種別・回り・馬場状態・クラス・障害判定を抽出する"""
        title_elem = soup.select_one("h2.hr-predictRaceInfo__title")
        race_name_raw = title_elem.get_text(strip=True) if title_elem else ""

        race_name = race_name_raw
        grade = None
        for suffix in GRADE_SUFFIXES:
            if race_name_raw.endswith(suffix):
                grade = suffix
                race_name = race_name_raw[: -len(suffix)]
                break

        distance = surface = direction = track_condition = age_condition = class_text = None
        is_jump = "障害" in race_name_raw

        for elem in soup.select(".hr-predictRaceInfo__text"):
            text = elem.get_text(" ", strip=True)

            if JUMP_PATTERN.match(text):
                is_jump = True
                continue

            surface_match = SURFACE_PATTERN.match(text)
            if surface_match:
                surface, direction, dist_str = surface_match.groups()
                distance = int(dist_str)
                continue

            if text.startswith("馬場"):
                track_condition = text.split("：")[-1].strip()
                continue

            if AGE_CONDITION_PATTERN.match(text):
                age_condition = text
                continue

            # 天気・本賞金・開催回（"3回中京4日"）・日付・発走時刻は特徴量として使わない
            if (text.startswith("天気") or text.startswith("本賞金") or text.endswith("発走")
                    or re.match(r"^\d+回\S+\d+日$", text)
                    or (TITLE_DATE_PATTERN.search(text) and "（" in text)):
                continue

            if class_text is None:
                class_text = text

        race_class = f"{grade} {class_text}".strip() if grade and class_text else (grade or class_text)

        return {
            "race_name": race_name,
            "distance": distance,
            "surface": surface,
            "direction": direction,
            "track_condition": track_condition,
            "race_class": race_class,
            "age_condition": age_condition,
            "is_jump": is_jump,
        }

    # ------------------------------------------------------------
    # 結果ページ（race/index）: 払戻金
    # ------------------------------------------------------------

    def parse_place_payouts(self, soup):
        """複勝の払戻金テーブルから {馬番文字列: 払戻金(円)} を返す（結果テーブルの
        place_odds算出に使う。着順テーブルとは別の払戻金テーブルにある）"""
        payout_table = None
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if rows and any("単勝" in tr.get_text() for tr in rows[:2]):
                payout_table = table
                break

        place_payouts = {}
        if payout_table is None:
            return place_payouts

        current_label = None
        for tr in payout_table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
            if not cells:
                continue

            if cells[0] in PAYOUT_LABELS:
                current_label = cells[0]
                rest = cells[1:]
            else:
                rest = cells

            if current_label == "複勝" and len(rest) >= 2:
                umaban_text, payout_text = rest[0], rest[1]
                payout_match = re.search(r"([\d,]+)円", payout_text)
                if payout_match and umaban_text.isdigit():
                    place_payouts[umaban_text] = int(payout_match.group(1).replace(",", ""))

        return place_payouts

    def parse_payouts(self, soup):
        """結果ページの確定払戻金テーブル（単勝・複勝・枠連・馬連・ワイド・馬単・
        3連複・3連単。ページ上は複数の<table>に分かれて掲載されている）を全件取得する。

        戻り値: {bet_type: [(combination, payout_yen, popularity), ...]}
        combinationは"1"（単勝・複勝）・"1-10"（枠連・馬連・ワイド・馬単）・
        "1-5-10"（3連複・3連単）の形式。着順に意味がある馬単・3連単は表示順
        （1着→2着→3着）のまま保持し、順不同の枠連・馬連・ワイド・3連複は
        馬番の昇順にソートしてから保存する"""
        payouts = {label: [] for label in PAYOUT_LABELS}

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue

            table_text = " ".join(tr.get_text() for tr in rows[:3])
            if not any(label in table_text for label in PAYOUT_LABELS):
                continue

            current_label = None
            for tr in rows:
                cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
                if not cells:
                    continue

                if cells[0] in PAYOUT_LABELS:
                    current_label = cells[0]
                    rest = cells[1:]
                else:
                    rest = cells

                if current_label is None or len(rest) < 2:
                    continue

                combination_text, payout_text = rest[0], rest[1]
                payout_match = re.search(r"([\d,]+)円", payout_text)
                if not payout_match:
                    continue
                payout_yen = int(payout_match.group(1).replace(",", ""))

                if current_label in UNORDERED_BET_TYPES and "-" in combination_text:
                    combination_text = "-".join(sorted(combination_text.split("-"), key=int))

                popularity = None
                if len(rest) >= 3:
                    pop_match = re.search(r"(\d+)", rest[2])
                    if pop_match:
                        popularity = int(pop_match.group(1))

                payouts[current_label].append((combination_text, payout_yen, popularity))

        return payouts

    # ------------------------------------------------------------
    # 結果ページ（race/index）: 着順テーブル
    # ------------------------------------------------------------

    def parse_result_rows(self, soup):
        """着順テーブル（列: 着順/枠番/馬番/馬名/タイム/通過+上がり3F/騎手/人気+オッズ/調教師）
        から entries / horses / results 用の行データを取得する"""
        result_table = None
        for table in soup.find_all("table"):
            header_text = table.find("tr").get_text() if table.find("tr") else ""
            if "着順" in header_text and "馬番" in header_text:
                result_table = table
                break

        if result_table is None:
            return []

        place_payouts = self.parse_place_payouts(soup)

        rows = []
        for tr in result_table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if len(cells) < 9:
                continue

            texts = [c.get_text(strip=True) for c in cells]
            (finish_text, frame_text, umaban_text, horse_cell_text, time_text,
             passing3f_text, jockey_cell_text, pop_odds_text, trainer_cell_text) = texts[:9]

            horse_link = cells[3].find("a", href=True)
            if not horse_link or "/directory/horse/" not in horse_link["href"]:
                continue
            horse_id = horse_link["href"].strip("/").split("/")[-1]
            horse_name = horse_link.get_text(strip=True)

            jockey_link = cells[6].find("a", href=True)
            trainer_link = cells[8].find("a", href=True)
            jockey = jockey_link.get_text(strip=True) if jockey_link else None
            trainer = trainer_link.get_text(strip=True) if trainer_link else None

            sex_age = weight = None
            suffix_match = HORSE_SUFFIX_PATTERN.match(horse_cell_text[len(horse_name):])
            if suffix_match:
                sex, age, weight_str = suffix_match.groups()
                sex_age = f"{sex}{age}"
                weight = float(weight_str)

            frame_number = int(frame_text) if frame_text.isdigit() else None
            horse_number = int(umaban_text) if umaban_text.isdigit() else None
            finish_position = int(finish_text) if finish_text.isdigit() else None

            finish_time = None
            time_match = FINISH_TIME_PATTERN.match(time_text)
            if time_match:
                minutes_str, seconds_str = time_match.groups()
                finish_time = int(minutes_str) * 60 + float(seconds_str)

            popularity = win_odds = None
            pop_odds_match = POPULARITY_ODDS_PATTERN.match(pop_odds_text)
            if pop_odds_match:
                popularity = int(pop_odds_match.group(1))
                win_odds = float(pop_odds_match.group(2))

            passing = last3f = None
            passing_match = PASSING_LAST3F_PATTERN.match(passing3f_text)
            if passing_match:
                passing = passing_match.group(1)
                last3f = float(passing_match.group(2))

            place_odds = None
            if horse_number is not None:
                payout_yen = place_payouts.get(str(horse_number))
                if payout_yen is not None:
                    place_odds = payout_yen / 100

            rows.append({
                "horse_id": horse_id, "horse_name": horse_name,
                "horse_number": horse_number, "frame_number": frame_number,
                "sex_age": sex_age, "weight": weight,
                "jockey": jockey, "trainer": trainer,
                "odds": win_odds, "popularity": popularity,
                "finish_position": finish_position, "finish_time": finish_time,
                "win_odds": win_odds, "place_odds": place_odds,
                "passing": passing, "last3f": last3f,
            })

        return rows

    def fetch_race(self, race_id, meeting_date, course):
        """race/index/{race_id} を取得し、races/entries/horses/results/payouts用の
        行データを返す"""
        url = f"{BASE_URL}/keiba/race/index/{race_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        race_info = self.parse_race_info(soup)

        race_row = {
            "race_id": race_id,
            "race_date": meeting_date.isoformat(),
            "course": course,
            "round": int(race_id[-2:]),
            "race_name": race_info["race_name"],
            "distance": race_info["distance"],
            "surface": race_info["surface"],
            "track_condition": race_info["track_condition"],
            "race_class": race_info["race_class"],
            "direction": race_info["direction"],
            "age_condition": race_info["age_condition"],
            "is_jump": race_info["is_jump"],
        }

        return race_row, self.parse_result_rows(soup), self.parse_payouts(soup)

    # ------------------------------------------------------------
    # 血統backfill（race/denma ページを再取得し、父・母父だけを補完する）
    # ------------------------------------------------------------

    def fetch_denma_pedigrees(self, race_id):
        """race/denma/{race_id}（出馬表。結果確定後もアーカイブされたページが
        参照できる）から {horse_id: (sire, damsire)} を取得する"""
        url = f"{BASE_URL}/keiba/race/denma/{race_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        table = None
        for t in soup.find_all("table"):
            header_text = t.find("tr").get_text() if t.find("tr") else ""
            if "馬番" in header_text and "父" in header_text:
                table = t
                break

        pedigrees = {}
        if table is None:
            return pedigrees

        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue

            horse_link = None
            for c in cells:
                horse_link = c.find("a", href=HORSE_LINK_PATTERN)
                if horse_link:
                    break
            if not horse_link:
                continue
            horse_id = HORSE_LINK_PATTERN.search(horse_link["href"]).group(1)

            pedigree_text = None
            for c in cells:
                cell_text = c.get_text(strip=True)
                if "父：" in cell_text:
                    pedigree_text = cell_text
                    break
            if pedigree_text is None:
                continue

            pedigree_match = DENMA_PEDIGREE_PATTERN.search(pedigree_text)
            if not pedigree_match:
                continue

            sire, _dam, damsire = pedigree_match.groups()
            pedigrees[horse_id] = (sire.strip(), damsire.strip())

        return pedigrees

    def backfill_pedigrees(self, race_ids, log=print):
        """指定したrace_idの出馬表ページから父・母父を取得し、horsesテーブルを更新する。
        対象race_idの絞り込み（例: horses.sire IS NULLな馬が出走したレースだけ）は
        呼び出し側が行う"""
        stats = {"races_checked": 0, "horses_updated": 0, "errors": []}

        for race_id in race_ids:
            stats["races_checked"] += 1

            try:
                pedigrees = self.fetch_denma_pedigrees(race_id)
            except Exception as exc:
                stats["errors"].append(f"race {race_id}: {exc}")
                continue

            for horse_id, (sire, damsire) in pedigrees.items():
                self.save_horse_pedigree(horse_id, sire, damsire)
                stats["horses_updated"] += 1

            log(f"[{race_id}] {len(pedigrees)}頭分の血統を取得")

        return stats

    def fetch_race_ids_missing_pedigree(self):
        """sire/damsireが未取得の馬が出走しているrace_id一覧を返す
        （backfill_pedigreesの対象を絞り込む際に使う）"""
        rows = self.db.fetchall("""
            SELECT DISTINCT e.race_id
            FROM entries e
            JOIN horses h ON h.horse_id = e.horse_id
            WHERE h.sire IS NULL
            ORDER BY e.race_id
        """)
        return [r[0] for r in rows]

    # ------------------------------------------------------------
    # DB保存
    # ------------------------------------------------------------

    def save_race(self, race):
        sql = """
            INSERT OR REPLACE INTO races (
                race_id, race_date, course, round, race_name,
                distance, surface, track_condition, race_class,
                direction, age_condition
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self.db.execute(sql, (
            race["race_id"], race["race_date"], race["course"], race["round"],
            race["race_name"], race["distance"], race["surface"],
            race["track_condition"], race["race_class"], race["direction"],
            race["age_condition"],
        ))

    def save_horse(self, horse_id, horse_name):
        sql = "INSERT OR IGNORE INTO horses (horse_id, horse_name, sire, damsire) VALUES (?, ?, NULL, NULL)"
        self.db.execute(sql, (horse_id, horse_name))

    def save_horse_pedigree(self, horse_id, sire, damsire):
        self.db.execute(
            "UPDATE horses SET sire = ?, damsire = ? WHERE horse_id = ?",
            (sire, damsire, horse_id),
        )

    def save_entry(self, race_id, row):
        sql = """
            INSERT OR REPLACE INTO entries (
                race_id, horse_id, horse_number, frame_number,
                horse_name, sex_age, weight, jockey, trainer,
                odds, popularity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self.db.execute(sql, (
            race_id, row["horse_id"], row["horse_number"], row["frame_number"],
            row["horse_name"], row["sex_age"], row["weight"],
            row["jockey"], row["trainer"], row["odds"], row["popularity"],
        ))

    def save_result(self, race_id, row):
        sql = """
            INSERT OR REPLACE INTO results (
                race_id, horse_id, finish_position, finish_time,
                win_odds, place_odds, passing, last3f
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        self.db.execute(sql, (
            race_id, row["horse_id"], row["finish_position"], row["finish_time"],
            row["win_odds"], row["place_odds"], row["passing"], row["last3f"],
        ))

    def save_payouts(self, race_id, payouts):
        sql = """
            INSERT OR REPLACE INTO payouts (race_id, bet_type, combination, payout_yen, popularity)
            VALUES (?, ?, ?, ?, ?)
        """
        for bet_type, rows in payouts.items():
            for combination, payout_yen, popularity in rows:
                self.db.execute(sql, (race_id, bet_type, combination, payout_yen, popularity))

    # ------------------------------------------------------------
    # 全体オーケストレーション
    # ------------------------------------------------------------

    def collect(self, start_date, end_date, log=print):
        """指定期間の確定済みレース結果・払戻金を取得してDBへ保存する"""

        meeting_ids = set()
        for year, month in self.months_between(start_date, end_date):
            meeting_ids |= self.fetch_meeting_ids(year, month)

        for meeting_id in sorted(meeting_ids):
            self.stats["meetings_checked"] += 1

            try:
                meeting = self.fetch_meeting(meeting_id)
            except Exception as exc:
                self.stats["errors"].append(f"meeting {meeting_id}: {exc}")
                continue

            if meeting is None or not (start_date <= meeting["meeting_date"] <= end_date):
                continue

            self.stats["meetings_matched"] += 1
            log(f"[{meeting['meeting_date'].isoformat()}] {meeting['course']} "
                f"({len(meeting['race_ids'])}レース) 取得中...")

            for race_id in meeting["race_ids"]:
                try:
                    race_row, horse_rows, payouts = self.fetch_race(
                        race_id, meeting["meeting_date"], meeting["course"]
                    )
                except Exception as exc:
                    self.stats["errors"].append(f"race {race_id}: {exc}")
                    continue

                if race_row["is_jump"]:
                    self.stats["jump_races_skipped"] += 1
                    continue

                self.save_race(race_row)
                self.save_payouts(race_id, payouts)
                self.stats["races"] += 1

                for row in horse_rows:
                    self.save_horse(row["horse_id"], row["horse_name"])

                    if row["horse_number"] is None:
                        self.stats["entries_skipped_no_horse_number"] += 1
                    else:
                        self.save_entry(race_id, row)
                        self.stats["entries"] += 1

                    self.save_result(race_id, row)
                    self.stats["results"] += 1

        return self.stats


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("YahooResultCollector 実行")
    print("=" * 40)

    collector = YahooResultCollector()
    result_stats = collector.collect(
        start_date=date.today() - timedelta(days=7),
        end_date=date.today() - timedelta(days=1),
    )

    print()
    for key, value in result_stats.items():
        print(f"{key}: {value}")

    print()
    print("血統backfill中...")
    pedigree_race_ids = collector.fetch_race_ids_missing_pedigree()
    pedigree_stats = collector.backfill_pedigrees(pedigree_race_ids)

    print()
    for key, value in pedigree_stats.items():
        print(f"{key}: {value}")
