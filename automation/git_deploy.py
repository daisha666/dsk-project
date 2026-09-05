"""
dsk_Project
自動化: docs/（GitHub Pagesアプリ）の自動デプロイ
Version 0.1

PROJECT_EVはアプリ用に別リポジトリ（project-ev-app）を持つため
git_deploy.pyがそちらのディレクトリへcdして別途push していたが、
dsk_Projectはアプリが同一リポジトリの docs/ フォルダなので、
プロジェクトルートで通常のgit add/commit/pushをするだけでよい。
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# pythonw.exe（コンソール無し）からgit.exe（コンソールアプリ）をsubprocessで
# 呼ぶと、Windowsは子プロセス用に新しいコンソールウィンドウを生成するため、
# オッズ自動更新のたびに黒い画面が一瞬表示されてしまう。
#
# 対処の経緯（2026-09-05）: CREATE_NO_WINDOW単体、CREATE_NO_WINDOW+
# STARTUPINFO(SW_HIDE)の組み合わせのいずれも、実機（Windows 11、既定の
# 端末アプリ=Windows Terminal）で画面表示を完全には抑止できなかった
# （STARTUPINFOのSTARTF_USESHOWWINDOW+SW_HIDEは「コンソールを作成してから
# 隠す」動作のため、CREATE_NO_WINDOW＝「そもそも作成しない」と組み合わせると
# 逆に一瞬の生成→非表示という流れになり、それが一瞬のウィンドウ表示として
# 見えていた可能性がある）。STARTUPINFOを外し、CREATE_NO_WINDOWに
# DETACHED_PROCESS（親のコンソールを一切引き継がない）を加えることで
# より強く抑止する
if sys.platform == "win32":
    _NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
else:
    _NO_WINDOW_FLAGS = 0


def _run(args, log):
    result = subprocess.run(
        args, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
        creationflags=_NO_WINDOW_FLAGS, stdin=subprocess.DEVNULL,
    )
    if result.stdout.strip():
        log(result.stdout.strip())
    return result


def deploy_docs(log=print):
    """docs/配下の変更をgit commit & pushする。変更が無ければ何もしない"""
    _run(["git", "add", "docs"], log)

    status = _run(["git", "status", "--porcelain", "--", "docs"], log)
    if not status.stdout.strip():
        log("docs/に変更なし。デプロイをスキップします。")
        return False

    commit = _run(["git", "commit", "-m", "Automated update from watcher.py"], log)
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed: {commit.stderr}")

    push = _run(["git", "push", "origin", "master"], log)
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {push.stderr}")

    log("GitHub Pagesへpush完了")
    return True


if __name__ == "__main__":
    deploy_docs()
