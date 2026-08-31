import os
from datetime import datetime

from database.db_manager import DatabaseManager


APP_NAME = "dsk_Project"
VERSION = "0.1"


class DskProject:

    def __init__(self):

        self.db = DatabaseManager()

    def banner(self):

        print("=" * 60)
        print(f" {APP_NAME} Version {VERSION}")
        print(" オッズ反映型・投資回収率重視AIシステム")
        print("=" * 60)

    def create_folders(self):

        folders = [

            "data",
            "docs",
            "output",

            "collectors",

            "database",

            "feature_engineering",

            "model",

            "config",

        ]

        print("必要フォルダを確認しています...")

        for folder in folders:

            os.makedirs(folder, exist_ok=True)

        print("フォルダ確認完了")

    def initialize_database(self):

        print()

        print("SQLite初期化中...")

        self.db.initialize()

        print("SQLite初期化完了")

    def show_status(self):

        print()

        print("起動日時 :", datetime.now())

        print()

        print("dsk_Project 起動成功")

        print()

        print("===================================")

        print("現在利用可能")

        print("-----------------------------------")

        print("SQLite")

        print("フォルダ管理")

        print()

        print("未実装（Stage1 土台のみ）")

        print("-----------------------------------")

        print("Yahoo競馬 レース一覧・出馬表・結果取得")

        print("netkeiba 血統・過去成績取得")

        print("ittai.net 騎手・調教師リーディング取得")

        print("8項目特徴量生成（上がり力・脚質力・騎手力・距離力・回り力・安定力・血統力・調教師力）")

        print("オッズ反映後の最終指数・順位計算（odds_score.py は実装済み。8項目の上流特徴量待ち）")

        print("LightGBM学習・ウォークフォワード検証")

        print("期待値ベースの購入判定")

        print("===================================")


if __name__ == "__main__":

    app = DskProject()

    app.banner()
    app.create_folders()
    app.initialize_database()
    app.show_status()
