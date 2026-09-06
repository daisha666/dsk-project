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
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# pythonw.exe（コンソール無し）からgit.exe（コンソールアプリ）をsubprocessで
# 呼ぶと、Windowsは子プロセス用に新しいコンソールウィンドウを生成するため、
# オッズ自動更新のたびに黒い画面が一瞬表示されてしまう。
#
# 対処の経緯（2026-09-05）: 以下をこの順に実機（Windows 11、既定の端末アプリ
# =Windows Terminal）で試したが、いずれも画面表示を完全には抑止できなかった。
#   1. CREATE_NO_WINDOW単体
#   2. CREATE_NO_WINDOW + STARTUPINFO(SW_HIDE)
#      （SW_HIDEは「作成してから隠す」動作のため、そもそも作成しない
#      CREATE_NO_WINDOWと矛盾し、かえって一瞬の生成→非表示になっていた
#      可能性がある）
#   3. CREATE_NO_WINDOW | DETACHED_PROCESS
#      （それでも改善せず。DETACHED_PROCESSは、標準出力/エラーをPIPEで
#      リダイレクトする場合〔=capture_output=True。1〜3すべてで併用していた〕
#      と組み合わせるとハンドル継承の都合でgit.exe側が結局コンソールを
#      新規作成してしまう既知の非互換がある）
# そこで、PIPEでのキャプチャ自体をやめ、一時ファイル経由で標準出力/エラーを
# 受け取る方式に変更した（ハンドル継承の問題を避けるため）。フラグは
# CREATE_NO_WINDOW単体に戻す
if sys.platform == "win32":
    _NO_WINDOW_FLAGS = subprocess.CREATE_NO_WINDOW
else:
    _NO_WINDOW_FLAGS = 0


def _run(args, log):
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as out_f, \
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as err_f:
        proc = subprocess.run(
            args, cwd=PROJECT_ROOT, stdout=out_f, stderr=err_f, stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW_FLAGS,
        )
        out_f.seek(0)
        err_f.seek(0)
        result = SimpleNamespace(
            stdout=out_f.read(), stderr=err_f.read(), returncode=proc.returncode,
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
