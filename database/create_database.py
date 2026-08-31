"""
dsk_Project
データベース作成
Version 0.1

schema.sql を単一の情報源とし、テーブル定義の重複を避ける
（PROJECT_EVではcreate_database.pyとschema.sqlが別々に定義され、
featuresテーブルの列がずれてしまった反省を踏まえた構成）。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from config.config import DATABASE_FILE
from database.db_manager import DatabaseManager


def create_database():

    db = DatabaseManager()
    db.initialize()

    print("=" * 40)
    print("dsk_Project データベース完成")
    print(DATABASE_FILE)
    print("=" * 40)


if __name__ == "__main__":
    create_database()
