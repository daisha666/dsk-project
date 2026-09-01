"""
dsk_Project
Google Sheets連携: 共通設定
Version 0.1

SPREADSHEET_IDはユーザーが「dsk_Project」という名前でGoogle Sheetsを作成し、
サービスアカウント（config/google_credentials.jsonのclient_email。
project-ev-sheets@...gserviceaccount.com、PROJECT_EVと共通）を編集者として
共有した後、そのスプレッドシートのURLから取得して埋める
（例: https://docs.google.com/spreadsheets/d/【この部分】/edit）。

サービスアカウントは自分自身のDriveストレージを持たず新規スプレッドシートを
作成できないため（prediction/sheets_report.py参照）、この手順はユーザー側の
一度きりの作業として必須。
"""

SPREADSHEET_ID = "1CtHs765uaLP-E2BnY-CWgDAaR3VVoKt_yiVYaInm0Fs"  # dsk_Project

CONTROL_PANEL_SHEET_NAME = "操作パネル"
VERIFICATION_SHEET_NAME = "検証結果"


def require_spreadsheet_id():
    if not SPREADSHEET_ID:
        raise RuntimeError(
            "automation/sheet_config.py の SPREADSHEET_ID が未設定です。"
            "dsk_Projectという名前でGoogle Sheetsを作成し、"
            "サービスアカウントを編集者として共有した上でIDを設定してください。"
        )
