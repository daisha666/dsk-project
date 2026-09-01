"""
dsk_Project
Google Sheets認証共通ユーティリティ
Version 0.1

PROJECT_EVのprediction/sheets_report.pyと同じ設計・同じサービスアカウント
（project-ev-sheets@...gserviceaccount.com）を流用する。

認証について:
  config/google_credentials.json（サービスアカウントキー、.gitignore対象）を使う。
  個人のGoogle Cloudプロジェクトで作成したサービスアカウントは自分自身のDrive
  ストレージ容量を持たないため、サービスアカウント自身で新規スプレッドシートを
  作成することはできない（Drive API上でファイルオーナーになれずquotaエラーになる）。
  そのためユーザー自身が「dsk_Project」という名前でスプレッドシートを作成し、
  上記サービスアカウントを編集者として共有してもらってから、既存シートを開いて
  読み書きする方式を取る（automation/setup_sheet.py参照）。

get_client()はautomation/sheet_control_panel.py（操作パネル）・
automation/verification_sheet.py（検証結果）の両方から共通利用される。
"""

import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

CREDENTIALS_FILE = PROJECT_ROOT / "config" / "google_credentials.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_client():
    creds = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=SCOPES)
    return gspread.authorize(creds)


if __name__ == "__main__":
    print("=" * 40)
    print("dsk_Project")
    print("sheets_report 読み込み成功")
    print("=" * 40)
