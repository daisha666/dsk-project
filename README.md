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
| データソース | Yahoo競馬（sports.yahoo.co.jp/keiba/）のみ | Yahoo競馬のみ |

詳細な設計方針・検証すべき問いは [docs/dsk_project_spec.md](docs/dsk_project_spec.md) を参照。

> **netkeiba・ittai.netは使わない（開発指示書の原案から変更）**:
> - netkeiba: 血統（父・母父）も過去成績（着順・上がり3F・通過順位）もYahoo競馬自体
>   （出馬表・結果ページ）から既に取得できることが判明したため不採用（horse_idの命名体系が
>   異なる別サイトとの突合を避けられる利点もある）
> - ittai.net: 実際に確認したところ、勝率・連対率・3着内率・単回・複回はnote.com有料
>   メンバーシップ限定で、無料で取得できるのは名前と内部ランクコードのみ（複勝率の実数値は
>   取れない）ため不採用。代わりにPROJECT_EVの`jockey_trainer_stats.py`と同じ方針――自前で
>   収集したYahoo競馬のレース結果から騎手・調教師ごとの複勝率を直接計算する――に切り替えた

## 現在の状況

### Stage1（土台）
- [x] SQLiteスキーマ設計（`database/schema.sql`）
- [x] オッズ反映後の最終指数・順位計算ロジック（`feature_engineering/odds_score.py`）
- [x] Yahoo競馬（sports.yahoo.co.jp/keiba/）データ収集: 結果・払戻金・出馬表・血統backfill
      （`collectors/yahoo_result_collector.py` / `yahoo_denma_collector.py`）
- [x] 条件別重みマスター（`database/condition_weights`、10競馬場×芝/ダート×18距離=360パターンをインポート済み。`database/import_condition_weights.py` / `data/condition_weights.csv`）
- [x] 8項目すべて実装（上がり力・脚質力・騎手力・距離力・回り力・安定力・血統力・調教師力）＋レース内Min-Max正規化・条件別重み付け・`overall_score`算出
      （`feature_engineering/agari_power.py` 等 + `overall_score.py`）
- [ ] ペースバイアス補正（`feature_engineering/pace_bias.py`。現状は常にNULL=補正なしとして扱われる）

### Stage2（AI）
- [x] LightGBM学習（`ai/build_dataset.py` / `ai/train_model.py`）: 単勝（1着=1）モデルを、
      overall_score + market_odds/market_popularity を含む**モデルA**と、市場情報を除いた
      **モデルB**の両方で学習・保存できる
- [x] 日付分割・複数フォールドのウォークフォワード検証（`ai/walk_forward_backtest.py`。フォールドは
      DB内のrace_dateの実際の範囲から動的に生成する。データ期間が伸びたらPROJECT_EVのような
      固定月次フォールドへの切り替えを検討）
- [ ] ハイパーパラメータチューニング

### Stage3（投資AI）
- [ ] 期待値ベースの購入判定・回収率バックテスト

初回検証（2026-08-08〜08-30、4フォールド、2,454件。**1ヶ月弱・小サンプルの参考値**）:
モデルA（市場情報あり）AUC=0.7857、モデルB（市場情報除外）AUC=0.6269（AUC差+0.1588）。
全フォールドで一貫してモデルAが上回った。データ収集期間を伸ばして再検証が必要。

## セットアップ

```bash
pip install -r requirements.txt
python database/create_database.py
python main.py
```
