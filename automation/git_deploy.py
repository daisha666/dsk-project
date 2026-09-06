"""
dsk_Project
自動化: docs/（GitHub Pagesアプリ）の自動デプロイ
Version 0.2

PROJECT_EVはアプリ用に別リポジトリ（project-ev-app）を持つため
git_deploy.pyがそちらのディレクトリへcdして別途push していたが、
dsk_Projectはアプリが同一リポジトリの docs/ フォルダなので、
プロジェクトルートで通常のgit add/commit/pushをするだけでよい。

v0.1ではgit.exeをsubprocessで呼んでいたが、pythonw.exe（コンソール無し）から
コンソールアプリをsubprocessで呼ぶとWindowsが子プロセス用のコンソール
ウィンドウを生成してしまい、CREATE_NO_WINDOW・STARTUPINFO・DETACHED_PROCESS・
PIPEをやめて一時ファイル経由にする等、複数の対処を試みても実機
（Windows 11）で黒い画面の表示を解消しきれなかった（2026-09-05〜06、
README「本番インシデント」参照）。根本対応として、外部プロセスを一切
呼ばないdulwich（純粋Python実装のgitクライアント）に切り替えた。

認証: dulwichはGit Credential Manager等のOS側の資格情報store非対応のため、
GitHub Personal Access Token（fine-grained、対象リポジトリのContents:
Read and writeのみ）をconfig/github_pat.txt（.gitignore対象）に保存し、
push時にHTTPS URLへ埋め込む（https://x-access-token:<PAT>@github.com/...
という、GitHub公式に案内されているPAT認証の標準形式）。
"""

import io
from pathlib import Path

import dulwich.porcelain as porcelain
from dulwich.repo import Repo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
PAT_FILE = PROJECT_ROOT / "config" / "github_pat.txt"
REMOTE_REPO = "daisha666/dsk-project"
BRANCH = "master"


def _read_pat():
    if not PAT_FILE.exists():
        raise RuntimeError(
            f"GitHub Personal Access Tokenが見つかりません: {PAT_FILE}\n"
            "fine-grained PAT（対象リポジトリのContents: Read and write）を作成し、"
            "このファイルに保存してください。"
        )
    return PAT_FILE.read_text(encoding="utf-8").strip()


def deploy_docs(log=print):
    """docs/配下の変更をgit commit & pushする。変更が無ければ何もしない"""
    with Repo(str(PROJECT_ROOT)) as repo:
        porcelain.add(repo, paths=[str(DOCS_DIR)])

        status = porcelain.status(repo)
        staged = status.staged
        if not (staged["add"] or staged["modify"] or staged["delete"]):
            log("docs/に変更なし。デプロイをスキップします。")
            return False

        n_changed = len(staged["add"]) + len(staged["modify"]) + len(staged["delete"])
        log(f"docs/の変更{n_changed}件をコミットします")

        porcelain.commit(repo, message=b"Automated update from watcher.py")

        pat = _read_pat()
        remote_url = f"https://x-access-token:{pat}@github.com/{REMOTE_REPO}.git"
        # pythonw.exe（本番の実行方式）にはsys.stdout/stderrが存在しない
        # （Noneになる）ため、porcelain.pushの既定値（sys.stdout.buffer）を
        # そのまま使うとAttributeErrorになる。常にBytesIO（破棄用の書き込み
        # 先）を明示的に渡す
        try:
            porcelain.push(
                repo, remote_url, f"refs/heads/{BRANCH}".encode(),
                outstream=io.BytesIO(), errstream=io.BytesIO(),
            )
        except Exception as exc:
            raise RuntimeError(f"git push failed: {exc}") from exc

    log("GitHub Pagesへpush完了")
    return True


if __name__ == "__main__":
    deploy_docs()
