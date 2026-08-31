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

        print("Yahoo競馬 出馬表・結果・払戻金・血統backfill取得（netkeiba/ittai.netは不採用）")

        print("騎手力・調教師力の計算（自前計算、正規化・重み付けは未実装）")

        print("オッズ反映後の最終指数・順位計算（odds_score.py）")

        print()

        print("未実装（Stage1 土台のみ）")

        print("-----------------------------------")

        print("上がり力・脚質力・距離力・回り力・安定力・血統力の実装")

        print("8項目のレース内正規化・条件別重み付け（overall_score算出）")

        print("LightGBM学習・ウォークフォワード検証")

        print("期待値ベースの購入判定")

        print("===================================")


if __name__ == "__main__":

    app = DskProject()

    app.banner()
    app.create_folders()
    app.initialize_database()
    app.show_status()
