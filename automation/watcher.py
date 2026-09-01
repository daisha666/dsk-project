"""
dsk_Project
自動化: Googleスプレッドシート「操作パネル」ポーリング実行スクリプト
Version 0.1

PROJECT_EVのautomation/watcher.pyと同じ設計（pythonw.exe対策・2種類の
エラー記録先・チェックON検知→実行→自動OFF）を踏襲した簡略版。

使い方:
    python automation/watcher.py         1回だけポーリングして終了
                                          （Windowsタスクスケジューラでの
                                          定期起動を想定）
    python automation/watcher.py --loop  常駐して自身で5分おきにポーリングし続ける
"""

import argparse
import io
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

if sys.stdout is None:
    sys.stdout = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")
if sys.stderr is None:
    sys.stderr = io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from automation.denma_predict_job import run_denma_predict_job
from automation.odds_refresh_job import run_odds_refresh_job
from automation.result_verify_job import run_result_verify_job
from automation.sheet_control_panel import (
    ensure_control_panel,
    get_sheet,
    read_jobs,
    set_job_done,
    set_job_running,
)

POLL_INTERVAL_SEC = 300

JOB_RUNNERS = {
    "denma_predict": run_denma_predict_job,
    "odds_refresh": run_odds_refresh_job,
    "result_verify": run_result_verify_job,
}

LOG_DIR = PROJECT_ROOT / "output" / "collection_logs"
LOG_FILE = LOG_DIR / "watcher_log.txt"

ERROR_LOG_DIR = PROJECT_ROOT / "automation" / "logs"
ERROR_LOG_FILE = ERROR_LOG_DIR / "watcher_error.log"


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"

    try:
        print(line, flush=True)
    except Exception:
        pass

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_error(context, error_text):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n{'=' * 70}\n[{timestamp}] {context}\n{'-' * 70}\n{error_text}\n"

    try:
        ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass

    log(f"エラーを記録しました: {context}（詳細は {ERROR_LOG_FILE} を参照）")


def poll_once():
    try:
        sh = get_sheet()
        ws = ensure_control_panel(sh, log=log)
        jobs = read_jobs(ws)
    except Exception:
        log_error("操作パネルへの接続に失敗しました（シートへのエラー記録は不可）",
                   traceback.format_exc())
        return

    any_ran = False
    for job in jobs:
        if not job["checked"]:
            continue

        any_ran = True
        start = datetime.now()
        set_job_running(ws, job, log=log)

        try:
            message = JOB_RUNNERS[job["key"]](log=log)
            set_job_done(ws, job, start, message, success=True, log=log)
        except Exception as exc:
            log_error(f"[{job['label']}] 処理中にエラーが発生しました", traceback.format_exc())
            error_msg = f"{type(exc).__name__}: {exc}"
            set_job_done(ws, job, start, error_msg, success=False, log=log)

    if not any_ran:
        log("チェック済みの処理なし")


def main():
    parser = argparse.ArgumentParser(description="操作パネルをポーリングして自動処理を実行する")
    parser.add_argument("--loop", action="store_true", help="常駐して繰り返しポーリングする")
    args = parser.parse_args()

    if args.loop:
        log(f"常駐モード開始（{POLL_INTERVAL_SEC}秒間隔）")
        while True:
            try:
                poll_once()
            except Exception:
                log_error("ポーリング処理全体で予期しないエラーが発生しました", traceback.format_exc())
            time.sleep(POLL_INTERVAL_SEC)
    else:
        try:
            poll_once()
        except Exception:
            log_error("ポーリング処理全体で予期しないエラーが発生しました", traceback.format_exc())
            sys.exit(1)


if __name__ == "__main__":
    main()
