# dsk_Project

オッズを積極的に特徴量として取り込む投資型競馬AI。SQLite × Python × LightGBM。

並行稼働中の [PROJECT_EV](https://github.com/daisha666/project-ev-app)（単勝回収率91.4%程度で頭打ち）と
同じ方法論（リーケージ防止・ウォークフォワード検証・過剰最適化回避）を踏襲しつつ、
GASベースの「競馬予想2」で得られた知見――**オッズを反映した最終指数が単勝の的中率を大きく上げる**――を
組み込んだ新プロジェクトとして立ち上げた。PROJECT_EVとは別リポジトリ・別スプレッドシート・別アプリで
並行運用し、どちらが優位か回収率で比較検証する。

## PROJECT_EVとの違い

| | PROJECT_EV | dsk_Project |
|---|---|---|
| 市場情報（オッズ）の扱い | モデルのエッジ検証のため除外する方向で検証 | 積極的に特徴量として組み込む方向で検証 |
| 特徴量 | 血統・脚質・近走・TrueSkill等 | 上記に加え、8項目（上がり力・脚質力・騎手力・距離力・回り力・安定力・血統力・調教師力）＋オッズ反映後の最終指数 |
| 印（本命度）の基準 | モデル予測確率 | オッズ反映後の順位（素点ベース順位も並行記録し比較） |
| データソース | netkeiba 等 | Yahoo競馬・netkeiba・ittai.net |

詳細な設計方針・検証すべき問いは [docs/dsk_project_spec.md](docs/dsk_project_spec.md) を参照。

## 現在の状況（Stage1: 土台）

- [x] SQLiteスキーマ設計（`database/schema.sql`）
- [x] オッズ反映後の最終指数・順位計算ロジック（`feature_engineering/odds_score.py`）
- [x] Yahoo競馬（sports.yahoo.co.jp/keiba/）データ収集: 結果・払戻金・出馬表（`collectors/yahoo_result_collector.py` / `yahoo_denma_collector.py`）
- [ ] netkeiba・ittai.netのデータ収集（`collectors/` は骨格のみ）
- [ ] 8項目特徴量の実装（`feature_engineering/` は骨格のみ）
- [ ] LightGBM学習・ウォークフォワード検証（Stage2）
- [ ] 期待値ベースの購入判定（Stage3）

## セットアップ

```bash
pip install -r requirements.txt
python database/create_database.py
python main.py
```
