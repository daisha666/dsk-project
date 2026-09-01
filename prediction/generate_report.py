"""
dsk_Project
予測結果の静的HTMLサイト生成（PROJECT_EVのprediction/generate_report.py相当）
Version 0.1

これまでdocs/index.html 1ページに全レースをベタ書きしていたのを、PROJECT_EV
（project-ev-app）と同じ階層構造・生成方式に作り直す:

  docs/style.css                       共通CSS（手動管理。ここでは生成しない）
  docs/index.html                      ホーム画面: 開催日一覧（過去分含む。
                                        ただし直近MAX_DATES件のみ表示）
  docs/{race_date}/index.html          その日のレース一覧（競馬場・R番号・
                                        レース名）
  docs/{race_date}/race_{race_id}.html 個別レースの予想画面（従来
                                        docs/index.htmlに1ページで並べていた
                                        内容を、レースごとに1ページとして表示）

PROJECT_EVとの違い:
  PROJECT_EVはoutput/html配下に生成した後、別リポジトリ（project-ev-app）へ
  デプロイする2段階構成。dsk_Projectは元々同一リポジトリのdocs/を直接
  GitHub Pagesとして公開しているため、ここでは直接docs/配下に生成する。

  ホーム画面の表示件数: PROJECT_EVは「開催ブロック」（連続開催日）単位で
  直近2ブロックに絞るが、dsk_Projectはユーザー指定により「直近2週間・
  最大4開催日」というシンプルな件数ベースの絞り込みにする（MAX_DATES=4）。
  一覧から外れた開催日の詳細ページ自体は削除しない（URLを直接指定すれば
  引き続き閲覧できる。PROJECT_EVと同じ方針）。
"""

import re
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from ai.backtest import classify_class_tier
from config.config import PROJECT_ROOT as CONFIG_PROJECT_ROOT

OUTPUT_DIR = CONFIG_PROJECT_ROOT / "docs"

DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ホーム画面（開催日一覧）に表示する最大件数（2週間分・4開催日目安。
# ユーザー確定事項。一覧から外れても詳細ページ自体は残る）
MAX_DATES_ON_HOME = 4

MARK_COLORS = {"◎": "#ff6b6b", "○": "#ffb347", "▲": "#5aa9ff", "△": "#4cd07d", "☆": "#c084fc"}

CLASS_BADGE_LABELS = {"G1": "GI", "G2": "GII", "G3": "GIII", "リステッド": "L"}

# docs/{date}/index.html のオッズ自動更新ステータス表示が読みに行くGAS Webアプリ
# （automation/gas/odds_refresh_webapp.gs）。従来docs/index.htmlに埋め込んでいた
# ものと同じ値
GAS_WEBAPP_URL = "https://script.google.com/macros/s/AKfycbyB1YzzKjF5d4tIn7RXE6TULK4FcsbRm9IuRe-1stCO1pTiRxfrtBEdDISZxCswJLjk/exec"
GAS_SECRET_KEY = "7CGmsbp8IFy6oSbFwIHocp2fXzQ"


def _esc(text):
    return (str(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def page_shell(title, body_html, css_href):
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)}</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body>
{body_html}
</body>
</html>
"""


def race_badge(race_class):
    tier = classify_class_tier(race_class)
    label = CLASS_BADGE_LABELS.get(tier)
    if label:
        return f'<div class="badge">{label}</div>'
    return ""


# ------------------------------------------------------------
# ホーム画面: 開催日一覧
# ------------------------------------------------------------

def scan_available_dates():
    """docs配下にある、予想データが生成済みの開催日（{race_date}/index.htmlが
    存在するディレクトリ）を新しい日付順（降順）で返す。generate_site()が
    どの日付を対象に実行されても、その都度この関数で全体を再スキャンするため、
    ホーム画面のdocs/index.htmlは手動メンテナンス不要で常に最新になる"""
    if not OUTPUT_DIR.exists():
        return []

    dates = []
    for entry in OUTPUT_DIR.iterdir():
        if not entry.is_dir() or not DATE_DIR_PATTERN.match(entry.name):
            continue
        if not (entry / "index.html").exists():
            continue
        race_count = len(list(entry.glob("race_*.html")))
        dates.append((entry.name, race_count))

    return sorted(dates, key=lambda d: d[0], reverse=True)


def render_date_card(race_date, count):
    return f"""
<a href="{race_date}/index.html">
  <div class="date-card">
    <div>
      <div class="date-text">{race_date}</div>
      <div class="count-text">{count}レース</div>
    </div>
    <div class="arrow">›</div>
  </div>
</a>
"""


def render_root_index_page(dates):
    if dates:
        list_html = f'<div class="date-list">{"".join(render_date_card(d, c) for d, c in dates)}</div>'
    else:
        list_html = '<div class="empty-state">開催データがまだありません。開催が近づくと表示されます。</div>'

    body = f"""
<header>
  <h1><span class="logo">🏇</span>dsk_Project 予想</h1>
  <p class="subtitle">オッズを反映した最終指数（AK列相当）で印を決定する投資型競馬AI</p>
</header>
<main class="container">
  {list_html}
</main>
<footer>
  <p>素点順位＝overall_scoreのレース内順位（オッズ反映前） / オッズ後順位＝odds_adjusted_scoreのレース内順位（印の基準）</p>
  <p><a href="https://github.com/daisha666/dsk-project" style="color:var(--accent)">daisha666/dsk-project</a></p>
</footer>
"""
    return page_shell("dsk_Project 予想 | 開催日一覧", body, css_href="style.css")


def build_root_index(log=print):
    """docs/index.html（ホーム画面）を、その時点でdocs配下に存在する開催日一覧
    から再生成する。generate_site()の最後に必ず呼ぶため、実行するたびに
    自動的に最新の状態になる。表示は直近MAX_DATES_ON_HOME件のみ（それより
    古い開催日の詳細ページ自体は削除しない。URLを直接指定すれば閲覧できる）"""
    all_dates = scan_available_dates()
    dates = all_dates[:MAX_DATES_ON_HOME]
    index_html = render_root_index_page(dates)
    (OUTPUT_DIR / "index.html").write_text(index_html, encoding="utf-8")
    log(f"ホーム画面（docs/index.html）更新: {len(dates)}開催日分を表示（全{len(all_dates)}開催日中、直近{MAX_DATES_ON_HOME}件のみ）")
    return dates


# ------------------------------------------------------------
# その日のレース一覧
# ------------------------------------------------------------

def render_race_nav_card(race_id, course, round_no, race_name, race_class):
    return f"""
<a href="race_{race_id}.html">
  <div class="race-nav-card">
    {race_badge(race_class)}
    <div class="course">{_esc(course)}</div>
    <div class="round">{round_no:.0f}R</div>
    <div class="name">{_esc(race_name)}</div>
  </div>
</a>
"""


AUTOREFRESH_SCRIPT = f"""
<script>
const GAS_WEBAPP_URL = "{GAS_WEBAPP_URL}";
const GAS_SECRET_KEY = "{GAS_SECRET_KEY}";
const AUTOREFRESH_POLL_MS = 60000;

async function pollAutoRefreshStatus() {{
  const bar = document.getElementById("autorefresh-bar");
  if (!GAS_WEBAPP_URL) {{
    return;
  }}

  try {{
    const url = `${{GAS_WEBAPP_URL}}?key=${{encodeURIComponent(GAS_SECRET_KEY)}}&action=autorefresh_status`;
    const res = await fetch(url, {{ cache: "no-store" }});
    const data = await res.json();

    if (data.status !== "ok") {{
      bar.innerHTML = `<span>オッズ自動更新: 状態取得エラー</span>`;
      return;
    }}

    const label = data.autoRefreshOn ? "🟢 オッズ自動更新: 稼働中" : "⚪ オッズ自動更新: 停止中";
    const updated = data.lastUpdatedAt ? `（最終更新 ${{data.lastUpdatedAt}}）` : "";
    bar.innerHTML = `<span>${{label}}${{updated}}</span>`;
  }} catch (e) {{
    bar.innerHTML = `<span>オッズ自動更新: 状態取得エラー</span>`;
  }}
}}

pollAutoRefreshStatus();
if (GAS_WEBAPP_URL) {{
  setInterval(pollAutoRefreshStatus, AUTOREFRESH_POLL_MS);
}}
</script>
"""


def render_date_index_page(race_date, race_infos):
    """race_infosは{race_id, course, round, race_name, race_class}の辞書のリスト
    （course, roundの昇順。fetch_races_for_date()参照）"""
    cards = "".join(
        render_race_nav_card(r["race_id"], r["course"], r["round"], r["race_name"], r["race_class"])
        for r in race_infos
    )

    body = f"""
<header class="date-top-header">
  <h1><span class="logo">🏇</span>dsk_Project 予想</h1>
  <p class="subtitle">{race_date}</p>
  <div class="settings-bar" id="autorefresh-bar" style="justify-content:center;"></div>
</header>
<main class="container">
  <a class="back-link" href="../index.html">← 開催日一覧に戻る</a>
  <div class="date-title">{race_date} 開催レース（{len(race_infos)}レース）</div>
  <div class="race-grid">{cards}</div>
</main>
<footer>
  <p><a href="https://github.com/daisha666/dsk-project" style="color:var(--accent)">daisha666/dsk-project</a></p>
</footer>
{AUTOREFRESH_SCRIPT}
"""
    return page_shell(f"{race_date} 開催一覧 | dsk_Project 予想", body, css_href="../style.css")


# ------------------------------------------------------------
# レース予想（詳細）ページ
# ------------------------------------------------------------

def render_horse_row(h):
    raw_rank = "-" if pd.isna(h["raw_rank"]) else f"{h['raw_rank']:.0f}"
    odds_rank = "-" if pd.isna(h["odds_adjusted_rank"]) else f"{h['odds_adjusted_rank']:.0f}"
    odds = "-" if pd.isna(h["market_odds"]) else f"{h['market_odds']:.1f}"
    popularity = "-" if pd.isna(h["market_popularity"]) else f"{h['market_popularity']:.0f}"
    pred_pct = "-" if pd.isna(h["pred_win_prob"]) else f"{h['pred_win_prob'] * 100:.1f}%"
    ev = "-" if pd.isna(h["expected_value"]) else f"{h['expected_value']:.2f}"
    mark_color = MARK_COLORS.get(h["mark"], "inherit")
    row_class = "recommended-row" if h["is_recommended"] else ""
    badge = '<span class="badge-buy">買い</span>' if h["is_recommended"] else ""

    return f"""
<tr class="{row_class}">
  <td class="mark" style="color:{mark_color}">{h["mark"] or ""}</td>
  <td>{h["horse_number"]:.0f}番 {_esc(h["horse_name"])}</td>
  <td>{raw_rank}</td>
  <td>{odds_rank}</td>
  <td>{odds}</td>
  <td>{popularity}</td>
  <td>{pred_pct}</td>
  <td>{ev}</td>
  <td>{badge}</td>
</tr>
"""


def render_race_detail_page(race_info, horses, settings, generated_at):
    """race_infoは1レース分の{race_date, course, round, surface, distance, race_class}。
    horsesはそのレースの出走馬（DataFrame。odds_adjusted_rank昇順ソート済みを想定）"""
    rows = "".join(render_horse_row(h) for _, h in horses.iterrows())

    settings_html = "".join([
        f'<span>EV閾値 &gt;= {settings["ev_threshold"]}</span>',
        f'<span>オッズ上限 {settings["odds_cap"]}倍</span>',
        f'<span>{"1勝クラス以上限定" if settings["class_filter"] else "全クラス対象"}</span>',
        f'<span>更新: {generated_at}</span>',
    ])

    body = f"""
<header>
  <div class="settings-bar">{settings_html}</div>
</header>
<main class="container">
  <a class="back-link" href="index.html">← {race_info["race_date"]} の開催一覧に戻る</a>
  <div class="race-card">
    <div class="race-header">
      <div class="race-title">{_esc(race_info["race_name"])}（{_esc(race_info["course"])} {race_info["round"]:.0f}R）</div>
      <div class="race-meta">{race_info["race_date"]}　{_esc(race_info["surface"])}{race_info["distance"]:.0f}m　{_esc(race_info["race_class"])}</div>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>印</th><th>馬</th><th>素点順位</th><th>オッズ後順位</th>
            <th>オッズ</th><th>人気</th><th>予測勝率</th><th>EV</th><th></th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </div>
</main>
<footer>
  <p>素点順位＝overall_scoreのレース内順位（オッズ反映前） / オッズ後順位＝odds_adjusted_scoreのレース内順位（印の基準）</p>
</footer>
"""
    return page_shell(f"{race_info['race_name']} | dsk_Project 予想", body, css_href="../style.css")


# ------------------------------------------------------------
# 生成本体
# ------------------------------------------------------------

def write_race_page(race_id, race_info, horses, settings, generated_at, date_dir):
    race_html = render_race_detail_page(race_info, horses, settings, generated_at)
    path = date_dir / f"race_{race_id}.html"
    path.write_text(race_html, encoding="utf-8")
    return path


def write_date_index(race_date, race_infos, date_dir):
    index_html = render_date_index_page(race_date, race_infos)
    path = date_dir / "index.html"
    path.write_text(index_html, encoding="utf-8")
    return path


def generate_site(scored, settings, generated_at, log=print):
    """score_upcoming_races()が返すscored（DataFrame。0行でも可）から、
    docs/{race_date}/index.html・docs/{race_date}/race_{race_id}.htmlを
    開催日・レースごとに生成し、最後にホーム画面（docs/index.html）を
    直近開催日一覧で再構築する。settingsは
    {"ev_threshold":..., "odds_cap":..., "class_filter":...}"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if len(scored) == 0:
        log("予測対象レースがありません（生成対象なし。ホーム画面のみ再構築します）")
        dates = build_root_index(log=log)
        return dates

    n_dates = 0
    n_races = 0
    for race_date, date_group in scored.groupby("race_date"):
        date_dir = OUTPUT_DIR / str(race_date)
        date_dir.mkdir(parents=True, exist_ok=True)

        race_infos = []
        for race_id, race_group in date_group.groupby("race_id"):
            info = race_group.iloc[0]
            race_info = {
                "race_date": info["race_date"],
                "course": info["course"],
                "round": info["round"],
                "race_name": info["race_name"],
                "surface": info["surface"],
                "distance": info["distance"],
                "race_class": info["race_class"],
            }
            horses = race_group.sort_values("odds_adjusted_rank", na_position="last")
            write_race_page(race_id, race_info, horses, settings, generated_at, date_dir)
            race_infos.append({
                "race_id": race_id,
                "course": info["course"],
                "round": info["round"],
                "race_name": info["race_name"],
                "race_class": info["race_class"],
            })
            n_races += 1

        race_infos.sort(key=lambda r: (r["course"], r["round"]))
        write_date_index(race_date, race_infos, date_dir)
        n_dates += 1

    log(f"生成完了: {n_dates}開催日・{n_races}レース分の予想ページを更新")

    dates = build_root_index(log=log)
    return dates
