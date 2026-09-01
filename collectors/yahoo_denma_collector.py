"""
dsk_Project
Yahoo!スポーツナビ 競馬（sports.yahoo.co.jp/keiba/）: 出馬表（denma） 収集
Version 0.1

対象ページ（2026-08-31にブラウザで実際に構造を確認済み）:
  - race/denma/{race_id} … 出馬表
    列: 枠番 / 馬番 / 馬名(性齢/毛色) / 騎手名(斤量) / 調教師名(所属) /
        父馬名・母馬名(母父馬名) / 馬体重 / 人気(オッズ)

  ※ keiba.yahoo.co.jp は廃止済み。必ず sports.yahoo.co.jp/keiba/ を使うこと。
  ※ 特別登録段階（枠順抽選前）のレースは出馬表テーブル自体が存在しない
    （「特別登録」の見出しのみで馬番・枠番が確定していない）。この場合は
    フェッチしても対象レースが空になるだけで、エラーにはならない設計にしている。

結果が確定済みのレースは yahoo_result_collector.py が別途担当する
（結果テーブルは着順確定後にしか存在しないため、出馬表だけでは分からない
finish_position等はここでは扱わない。resultsテーブルへの保存も行わない）。

保存先: races / horses / entries（results/payoutsは対象外）
"""

import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from collectors.base_collector import BaseCollector
from database.db_manager import DatabaseManager

BASE_URL = "https://sports.yahoo.co.jp"
TOP_PAGE_URL = f"{BASE_URL}/keiba/"

MEETING_ID_PATTERN = re.compile(r"/keiba/race/list/(\d{8})")

TITLE_DATE_PATTERN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
TITLE_COURSE_PATTERN = re.compile(r"）\s*(\S+?)競馬場")

# レース見出し「11R 出石特別 サラ系3歳上 ダート・右 1800m」形式
RACE_HEADER_PATTERN = re.compile(r"^(\d+)R(.+?)(ダート|芝)・(.+?) (\d+)m$")
JUMP_PATTERN = re.compile(r"障害")
AGE_CONDITION_PATTERN = re.compile(r"(\d+歳(?:上)?)")

# 出馬表ページの馬名セル末尾（性齢のみ。毛色・体重は無視。
# 例: "ヴォンドゥ牝3/鹿毛" -> 牝3。結果ページの馬名セル（性齢/馬体重）とは
# 末尾表記が異なるため独立したパターンにしている
HORSE_SUFFIX_PATTERN = re.compile(r"^(牡|牝|セ)\s*(\d+)/")

# 馬体重セル "424(-4)"（未計測時は"計不"等） -> 馬体重のみ抽出
WEIGHT_PATTERN = re.compile(r"^(\d+)")

# 人気(オッズ)セル "5(14.6)" -> 人気・単勝オッズ
POPULARITY_ODDS_PATTERN = re.compile(r"^(\d+)\(([\d.]+)\)$")

# 血統セル "父：X母：Y(母父：Z)"
DENMA_PEDIGREE_PATTERN = re.compile(r"父：(.+?)母：(.+?)\(母父：(.+?)\)")

HORSE_LINK_PATTERN = re.compile(r"/directory/horse/(\d+)/?$")

# オッズページ（race/odds/tfw）の複勝セル "5.1-7.4" -> low, high
PLACE_ODDS_RANGE_PATTERN = re.compile(r"^([\d.]+)-([\d.]+)$")

HORSE_LINK_PATTERN = re.compile(r"/directory/horse/(\d+)/?$")


class YahooDenmaCollector(BaseCollector):
    """Yahoo!スポーツナビ 競馬の出馬表ページから、まだ結果が確定していない
    レースの出走馬一覧を取得し、races / horses / entries へ保存するクラス"""

    def __init__(self, sleep_sec=0.6):
        super().__init__()
        self.db = DatabaseManager()
        self.sleep_sec = sleep_sec

        self.stats = {
            "meetings_checked": 0,
            "races_checked": 0,
            "races_already_finished": 0,
            "races_no_denma": 0,
            "races_not_ready": 0,
            "races_saved": 0,
            "entries_saved": 0,
            "errors": [],
        }

    # ------------------------------------------------------------
    # 開催日・レース一覧（メタ情報）
    # ------------------------------------------------------------

    def fetch_meeting_ids(self):
        """トップページに現在掲載されている開催日ID（8桁）の一覧を取得する"""
        soup = self.get_html(TOP_PAGE_URL)
        time.sleep(self.sleep_sec)

        return sorted({
            m.group(1) for a in soup.find_all("a", href=MEETING_ID_PATTERN)
            for m in [MEETING_ID_PATTERN.search(a["href"])]
        })

    def fetch_meeting_races(self, meeting_id):
        """race/list/{meeting_id} から日付・競馬場・レース一覧（メタ情報）を取得する。
        まだ発走時刻・馬場状態は確定していないため races.track_condition はNoneのまま保存する"""
        url = f"{BASE_URL}/keiba/race/list/{meeting_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        title = soup.title.get_text(strip=True) if soup.title else ""
        date_match = TITLE_DATE_PATTERN.search(title)
        if not date_match:
            return None

        year, month, day = (int(v) for v in date_match.groups())
        course_match = TITLE_COURSE_PATTERN.search(title)
        course = course_match.group(1) if course_match else None

        races = []
        for header in soup.find_all(["h2", "h3"]):
            text = header.get_text(strip=True)

            if JUMP_PATTERN.search(text):
                continue

            m = RACE_HEADER_PATTERN.match(text)
            if not m:
                continue

            round_no, name, surface, direction, distance = m.groups()

            age_match = AGE_CONDITION_PATTERN.search(name)
            age_condition = age_match.group(1) if age_match else None
            race_class = name.replace("サラ系", "").replace(age_condition or "", "").strip() or None

            races.append({
                "race_id": f"{meeting_id}{int(round_no):02d}",
                "race_date": f"{year:04d}-{month:02d}-{day:02d}",
                "course": course,
                "round": int(round_no),
                "race_name": name,
                "distance": int(distance),
                "surface": surface,
                "track_condition": None,
                "race_class": race_class,
                "direction": direction,
                "age_condition": age_condition,
            })

        return races

    def has_result(self, race_id):
        """race/index/{race_id} に既に着順テーブルがあるかどうかを判定する。
        あれば結果確定済みなのでyahoo_result_collector.pyの担当範囲としてスキップする"""
        url = f"{BASE_URL}/keiba/race/index/{race_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        for table in soup.find_all("table"):
            header_text = table.find("tr").get_text() if table.find("tr") else ""
            if "着順" in header_text and "馬番" in header_text:
                return True
        return False

    # ------------------------------------------------------------
    # 出馬表ページ（race/denma）
    # ------------------------------------------------------------

    def fetch_denma_entries(self, race_id):
        """race/denma/{race_id} から出走馬一覧
        （枠番・馬番・馬名・性齢・騎手・調教師・父・母父・馬体重・人気・単勝オッズ）を取得する。
        特別登録段階（枠順未確定）のレースは出馬表テーブルが無く空リストを返す"""
        url = f"{BASE_URL}/keiba/race/denma/{race_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        table = None
        for t in soup.find_all("table"):
            header_text = t.find("tr").get_text() if t.find("tr") else ""
            if "馬番" in header_text and "馬名" in header_text:
                table = t
                break

        if table is None:
            return []

        entries = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if len(cells) < 8:
                continue

            texts = [c.get_text(strip=True) for c in cells]
            (frame_text, umaban_text, horse_cell_text, _jockey_cell, _trainer_cell,
             pedigree_text, weight_text, pop_odds_text) = texts[:8]

            horse_link = cells[2].find("a", href=True)
            if not horse_link or "/directory/horse/" not in horse_link["href"]:
                continue
            horse_id = horse_link["href"].strip("/").split("/")[-1]
            horse_name = horse_link.get_text(strip=True)

            jockey_link = cells[3].find("a", href=True)
            trainer_link = cells[4].find("a", href=True)
            jockey = jockey_link.get_text(strip=True) if jockey_link else None
            trainer = trainer_link.get_text(strip=True) if trainer_link else None

            sex_age = None
            suffix_match = HORSE_SUFFIX_PATTERN.match(horse_cell_text[len(horse_name):])
            if suffix_match:
                sex, age = suffix_match.groups()
                sex_age = f"{sex}{age}"

            sire = damsire = None
            pedigree_match = DENMA_PEDIGREE_PATTERN.search(pedigree_text)
            if pedigree_match:
                sire, _dam, damsire = (s.strip() for s in pedigree_match.groups())

            weight = None
            weight_match = WEIGHT_PATTERN.match(weight_text)
            if weight_match:
                weight = float(weight_match.group(1))

            popularity = odds = None
            pop_odds_match = POPULARITY_ODDS_PATTERN.match(pop_odds_text)
            if pop_odds_match:
                popularity = int(pop_odds_match.group(1))
                odds = float(pop_odds_match.group(2))

            frame_number = int(frame_text) if frame_text.isdigit() else None
            horse_number = int(umaban_text) if umaban_text.isdigit() else None

            entries.append({
                "horse_id": horse_id, "horse_name": horse_name,
                "horse_number": horse_number, "frame_number": frame_number,
                "sex_age": sex_age, "weight": weight,
                "jockey": jockey, "trainer": trainer,
                "sire": sire, "damsire": damsire,
                "popularity": popularity, "odds": odds,
            })

        return entries

    # ------------------------------------------------------------
    # オッズページ（race/odds/tfw） - 発走前オッズの最新値取得
    # ------------------------------------------------------------

    def fetch_tfw_odds(self, race_id):
        """race/odds/tfw/{race_id}（2026-08-31にブラウザで構造を確認済み。
        列: 枠番/馬番/馬名/単勝/複勝）から
        {horse_id: (win_odds, place_odds_low, place_odds_high)} を取得する。
        automation/odds_refresh_job.py（オッズ自動更新）専用。取消・除外等で
        オッズが「****」表示の馬は取得対象外"""
        url = f"{BASE_URL}/keiba/race/odds/tfw/{race_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        table = None
        for t in soup.find_all("table"):
            header_text = t.find("tr").get_text() if t.find("tr") else ""
            if "単勝" in header_text and "複勝" in header_text and "馬番" in header_text:
                table = t
                break

        odds = {}
        if table is None:
            return odds

        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"])
            if len(cells) < 5:
                continue

            horse_link = None
            for c in cells:
                horse_link = c.find("a", href=HORSE_LINK_PATTERN)
                if horse_link:
                    break
            if not horse_link:
                continue
            horse_id = HORSE_LINK_PATTERN.search(horse_link["href"]).group(1)

            win_text = cells[3].get_text(strip=True)
            place_text = cells[4].get_text(strip=True)

            try:
                win_odds = float(win_text)
            except ValueError:
                win_odds = None

            place_low = place_high = None
            range_match = PLACE_ODDS_RANGE_PATTERN.match(place_text)
            if range_match:
                place_low, place_high = (float(v) for v in range_match.groups())

            odds[horse_id] = (win_odds, place_low, place_high)

        return odds

    def update_entry_odds(self, race_id, horse_id, win_odds, place_low, place_high):
        """entriesの単勝オッズ・複勝オッズ範囲だけを更新する（人気は出馬表側の
        値をそのまま残す。人気順位は複勝オッズ帯より安定しており、tfwページ
        だけを見て毎回書き換えると却って揺れが出るため）"""
        self.db.execute("""
            UPDATE entries SET odds = ?, place_odds_low = ?, place_odds_high = ?
            WHERE race_id = ? AND horse_id = ?
        """, (win_odds, place_low, place_high, race_id, horse_id))

    # ------------------------------------------------------------
    # オッズページ（race/odds/wide） - ワイド発走前オッズの取得
    # ------------------------------------------------------------

    def fetch_wide_odds(self, race_id):
        """race/odds/wide/{race_id}（2026-09-02にブラウザで構造を確認済み）から
        ワイドの発走前オッズを取得する。ページは馬番1（枠番ではなく馬番）ごとに
        別テーブルで、各テーブルの1行目は自分自身の馬番のみのセル（見出し行）、
        以降の行が [相手の馬番, "低-高"倍率] という構成（自分より大きい馬番との
        組み合わせのみを持つ。すなわち全体で上三角のみ、重複や自己ペアは無い）。
        {(horse_number_1, horse_number_2): (odds_low, odds_high)} を返す
        （horse_number_1 < horse_number_2に正規化する）。
        combination_oddsテーブルには複勝・単勝のような取消判定の仕組みが無いため、
        「****」等パース不能なセルは単にスキップする（automation/odds_refresh_job.py
        （オッズ自動更新）専用。当面は蓄積のみで、複勝・ワイドのバックテストは
        データが数ヶ月〜半年分蓄積されてから再検証する予定。README参照）"""
        url = f"{BASE_URL}/keiba/race/odds/wide/{race_id}"
        soup = self.get_html(url)
        time.sleep(self.sleep_sec)

        odds = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue

            header_cells = rows[0].find_all(["th", "td"])
            if len(header_cells) != 1:
                continue
            try:
                horse_number_1 = int(header_cells[0].get_text(strip=True))
            except ValueError:
                continue

            for tr in rows[1:]:
                cells = tr.find_all(["th", "td"])
                if len(cells) != 2:
                    continue
                try:
                    horse_number_2 = int(cells[0].get_text(strip=True))
                except ValueError:
                    continue

                range_match = PLACE_ODDS_RANGE_PATTERN.match(cells[1].get_text(strip=True))
                if not range_match:
                    continue
                odds_low, odds_high = (float(v) for v in range_match.groups())

                key = tuple(sorted((horse_number_1, horse_number_2)))
                odds[key] = (odds_low, odds_high)

        return odds

    def save_wide_odds(self, race_id, wide_odds):
        """fetch_wide_odds()の戻り値をcombination_oddsテーブルへ保存する
        （race_id+bet_type+馬番の組で上書き）"""
        for (horse_number_1, horse_number_2), (odds_low, odds_high) in wide_odds.items():
            self.db.execute("""
                INSERT OR REPLACE INTO combination_odds
                    (race_id, bet_type, horse_number_1, horse_number_2, odds_low, odds_high)
                VALUES (?, 'ワイド', ?, ?, ?, ?)
            """, (race_id, horse_number_1, horse_number_2, odds_low, odds_high))

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

    def save_horse(self, horse_id, horse_name, sire, damsire):
        """horsesテーブルへ保存する。sire/damsireは既存値がNULLの場合のみ更新する"""
        self.db.execute(
            "INSERT OR IGNORE INTO horses (horse_id, horse_name, sire, damsire) VALUES (?, ?, ?, ?)",
            (horse_id, horse_name, sire, damsire),
        )
        if sire or damsire:
            self.db.execute(
                "UPDATE horses SET sire = COALESCE(sire, ?), damsire = COALESCE(damsire, ?) WHERE horse_id = ?",
                (sire, damsire, horse_id),
            )

    def save_entry(self, race_id, entry):
        sql = """
            INSERT OR REPLACE INTO entries (
                race_id, horse_id, horse_number, frame_number,
                horse_name, sex_age, weight, jockey, trainer,
                odds, popularity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self.db.execute(sql, (
            race_id, entry["horse_id"], entry["horse_number"], entry["frame_number"],
            entry["horse_name"], entry["sex_age"], entry["weight"],
            entry["jockey"], entry["trainer"], entry["odds"], entry["popularity"],
        ))

    # ------------------------------------------------------------
    # 全体オーケストレーション
    # ------------------------------------------------------------

    def collect(self, log=print):
        """現在サイトに掲載されている開催日のうち、まだ結果が出ていないレースについて
        出馬表を取得し、races / entries テーブルへ保存する"""
        meeting_ids = self.fetch_meeting_ids()

        for meeting_id in meeting_ids:
            self.stats["meetings_checked"] += 1

            try:
                races = self.fetch_meeting_races(meeting_id)
            except Exception as exc:
                self.stats["errors"].append(f"meeting {meeting_id}: {exc}")
                continue

            if not races:
                continue

            log(f"[{meeting_id}] {races[0]['race_date']} {races[0]['course']} "
                f"({len(races)}レース) 確認中...")

            for race in races:
                self.stats["races_checked"] += 1
                race_id = race["race_id"]

                try:
                    if self.has_result(race_id):
                        self.stats["races_already_finished"] += 1
                        continue
                except Exception as exc:
                    self.stats["errors"].append(f"race {race_id} (result check): {exc}")
                    continue

                try:
                    entries = self.fetch_denma_entries(race_id)
                except Exception as exc:
                    self.stats["errors"].append(f"race {race_id} (denma): {exc}")
                    continue

                if not entries:
                    self.stats["races_no_denma"] += 1
                    continue

                if any(e["horse_number"] is None for e in entries):
                    # 出馬表ページ自体はあるが枠順抽選前（馬番が"-"表記）のレース。
                    # horse_numberはentries側で必須の識別子のため保存を見送り、
                    # 次回以降の収集サイクルで抽選後に改めて保存する
                    self.stats["races_not_ready"] += 1
                    continue

                self.save_race(race)
                self.stats["races_saved"] += 1

                for entry in entries:
                    self.save_horse(entry["horse_id"], entry["horse_name"], entry["sire"], entry["damsire"])
                    self.save_entry(race_id, entry)
                    self.stats["entries_saved"] += 1

                log(f"  [{race_id}] {race['round']}R {race['race_name']}: 出走{len(entries)}頭 保存")

        return self.stats


if __name__ == "__main__":

    print("=" * 40)
    print("dsk_Project")
    print("YahooDenmaCollector 実行")
    print("=" * 40)

    collector = YahooDenmaCollector()
    result_stats = collector.collect()

    print()
    for key, value in result_stats.items():
        print(f"{key}: {value}")
