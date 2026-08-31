"""
dsk_Project
設定ファイル
Version 0.1
"""

from pathlib import Path

# ==========================
# プロジェクトルート
# ==========================
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ==========================
# データフォルダ
# ==========================
DATA_DIR = PROJECT_ROOT / "data"

# ==========================
# データベース
# ==========================
DATABASE_DIR = PROJECT_ROOT / "database"
DATABASE_FILE = DATABASE_DIR / "dsk_project.db"
SCHEMA_FILE = DATABASE_DIR / "schema.sql"

# ==========================
# 出力フォルダ
# ==========================
OUTPUT_DIR = PROJECT_ROOT / "output"

# ==========================
# モデル保存
# ==========================
MODEL_DIR = PROJECT_ROOT / "model"

# ==========================
# オッズ反映後最終指数の係数
# 素点総合力 * (1 / オッズ) * ODDS_SCORE_MULTIPLIER
# 競馬予想2の実績値を初期値とし、Stage2以降でチューニングする
# ==========================
ODDS_SCORE_MULTIPLIER = 8

# ==========================
# 必要なフォルダを作成
# ==========================
for folder in [
    DATA_DIR,
    DATABASE_DIR,
    OUTPUT_DIR,
    MODEL_DIR,
]:
    folder.mkdir(parents=True, exist_ok=True)
