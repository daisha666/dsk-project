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
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(args, log):
    result = subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8")
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
