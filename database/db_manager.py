"""
dsk_Project
データベース管理
Version 0.1
"""

import sqlite3
import sys
from pathlib import Path

# dsk_Projectフォルダをpythonの検索対象に追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from config.config import DATABASE_FILE, SCHEMA_FILE


class DatabaseManager:
    """SQLiteデータベース管理クラス"""

    def __init__(self):
        self.db_file = DATABASE_FILE

    def connect(self):
        """データベースへ接続"""
        return sqlite3.connect(self.db_file)

    def initialize(self):
        """schema.sqlを実行してテーブルを作成"""
        schema_sql = Path(SCHEMA_FILE).read_text(encoding="utf-8")

        conn = self.connect()
        cursor = conn.cursor()
        cursor.executescript(schema_sql)
        conn.commit()
        conn.close()

    def execute(self, sql, params=None):
        """INSERT・UPDATE・DELETE"""
        conn = self.connect()
        cursor = conn.cursor()

        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

        conn.commit()
        conn.close()

    def fetchall(self, sql, params=None):
        """SELECT（複数件取得）"""
        conn = self.connect()
        cursor = conn.cursor()

        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

        rows = cursor.fetchall()
        conn.close()

        return rows

    def fetchone(self, sql, params=None):
        """SELECT（1件取得）"""
        conn = self.connect()
        cursor = conn.cursor()

        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

        row = cursor.fetchone()
        conn.close()

        return row


if __name__ == "__main__":
    db = DatabaseManager()
    print("====================================")
    print("DatabaseManager 読み込み成功")
    print(db.db_file)
    print("====================================")
