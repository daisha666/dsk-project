"""
dsk_Project
自動化: docs/（GitHub Pagesアプリ）の自動デプロイ
Version 0.3

PROJECT_EVはアプリ用に別リポジトリ（project-ev-app）を持つため
git_deploy.pyがそちらのディレクトリへcdして別途pushしていたが、
dsk_Projectはアプリが同一リポジトリのdocs/フォルダなので、
プロジェクトルートで通常のgit add/commit/pushをするだけでよい。

v0.1ではgit.exeをsubprocessで呼んでいたが、pythonw.exe（コンソール無し）から
コンソールアプリをsubprocessで呼ぶとWindowsが子プロセス用のコンソール
ウィンドウを生成してしまい、複数の対処を試みても実機（Windows 11）で
黒い画面の表示を解消しきれなかった（2026-09-05〜06、README「本番インシデント」
参照）。根本対応として、外部プロセスを一切呼ばないdulwich（純粋Python実装の
gitクライアント）に切り替えた（v0.2）。

v0.2ではPAT埋め込みのHTTPS URL（https://x-access-token:<PAT>@github.com/...）で
認証していたが、2026-09-08にSSH Deploy Key方式へ移行した（v0.3）。理由:
  - PATは最長1年の有効期限管理・年次更新作業が必要だった
    （README「定期メンテナンス」参照、移行に伴い削除）
  - Deploy Keyはリポジトリ単位で発行でき、既定で有効期限が無いため
    更新作業自体が不要になる
  - dulwich本体の既定のSSHクライアント（SubprocessSSHVendor）はsystemの
    `ssh`コマンドをsubprocessで呼ぶため、これをそのまま使うと黒い画面問題が
    再発しかねない。そのためParamikoSSHVendor（純Python実装。paramikoライブラリ
    を使い外部プロセスを一切呼ばない）を明示的に指定する。ParamikoSSHVendorは
    dulwich本体（pip配布のwheel）には同梱されていないため、dulwichのsdist
    （同一バージョン、contrib/paramiko_vendor.py）からautomation/ssh_vendor.py
    として複製した（改変なし）

認証: config/deploy_key_ed25519（秘密鍵、.gitignore対象）を使う。対応する
公開鍵（config/deploy_key_ed25519.pub）はGitHubリポジトリのSettings >
Deploy keysに登録済み（Allow write access）。ホストキー検証はfail-closed
（paramiko.RejectPolicy）のため、事前に~/.ssh/known_hostsへgithub.comの
ホストキーを登録しておく必要がある（GitHub公式のフィンガープリントと照合済み）。
"""

import io
import sys
from pathlib import Path

import dulwich.client as _dulwich_client
import dulwich.porcelain as porcelain
from dulwich.repo import Repo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from automation.ssh_vendor import ParamikoSSHVendor

# dulwichの既定（SubprocessSSHVendor）はsystemのsshコマンドをsubprocessで呼ぶ。
# ここで明示的にParamikoSSHVendorへ差し替えないと黒い画面問題が再発する
_dulwich_client.get_ssh_vendor = ParamikoSSHVendor

DOCS_DIR = PROJECT_ROOT / "docs"
DEPLOY_KEY_FILE = PROJECT_ROOT / "config" / "deploy_key_ed25519"
REMOTE_REPO = "daisha666/dsk-project"
BRANCH = "master"


def _check_deploy_key():
    if not DEPLOY_KEY_FILE.exists():
        raise RuntimeError(
            f"SSH Deploy Keyの秘密鍵が見つかりません: {DEPLOY_KEY_FILE}\n"
            "config/deploy_key_ed25519（と対応する.pub）を用意し、公開鍵を"
            "GitHubリポジトリのSettings > Deploy keysに登録してください"
            "（Allow write accessを有効にすること）。"
        )


def deploy_docs(log=print):
    """docs/配下の変更をgit commit & pushする。変更が無ければ何もしない"""
    _check_deploy_key()

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

        remote_url = f"git@github.com:{REMOTE_REPO}.git"
        # pythonw.exe（本番の実行方式）にはsys.stdout/stderrが存在しない
        # （Noneになる）ため、porcelain.pushの既定値（sys.stdout.buffer）を
        # そのまま使うとAttributeErrorになる。常にBytesIO（破棄用の書き込み
        # 先）を明示的に渡す
        try:
            porcelain.push(
                repo, remote_url, f"refs/heads/{BRANCH}".encode(),
                outstream=io.BytesIO(), errstream=io.BytesIO(),
                key_filename=str(DEPLOY_KEY_FILE),
            )
        except Exception as exc:
            raise RuntimeError(f"git push failed: {exc}") from exc

    log("GitHub Pagesへpush完了")
    return True


if __name__ == "__main__":
    deploy_docs()
