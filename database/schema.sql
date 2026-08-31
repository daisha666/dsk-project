-- ===================================================
-- dsk_Project
-- Database Schema Version 1.0
--
-- PROJECT_EV（daisha666/project-ev-app）のスキーマを踏襲しつつ、
-- 「競馬予想2」の知見（オッズ反映後の最終指数・条件別重み）を
-- 特徴量として明示的に組み込むためのテーブルを追加している。
-- ===================================================

------------------------------------------------------
-- レース情報
------------------------------------------------------
CREATE TABLE IF NOT EXISTS races (

    race_id TEXT PRIMARY KEY,
    race_date TEXT,
    course TEXT,
    round INTEGER,
    race_name TEXT,
    distance INTEGER,
    surface TEXT,
    track_condition TEXT,
    race_class TEXT,
    direction TEXT,
    age_condition TEXT

);

------------------------------------------------------
-- 出走馬情報
------------------------------------------------------
CREATE TABLE IF NOT EXISTS entries (

    race_id TEXT,
    horse_id TEXT,
    horse_number INTEGER,
    frame_number INTEGER,

    horse_name TEXT,
    sex_age TEXT,
    weight REAL,

    jockey TEXT,
    trainer TEXT,

    running_style TEXT,

    odds REAL,
    popularity INTEGER,

    place_odds_low REAL,
    place_odds_high REAL,

    PRIMARY KEY (race_id, horse_id)

);

------------------------------------------------------
-- レース結果
------------------------------------------------------
CREATE TABLE IF NOT EXISTS results (

    race_id TEXT,
    horse_id TEXT,

    finish_position INTEGER,

    finish_time REAL,

    win_odds REAL,

    place_odds REAL,

    passing TEXT,

    last3f REAL,

    PRIMARY KEY (race_id, horse_id)

);

------------------------------------------------------
-- 馬マスター
------------------------------------------------------
CREATE TABLE IF NOT EXISTS horses (

    horse_id TEXT PRIMARY KEY,

    horse_name TEXT,

    sire TEXT,

    damsire TEXT

);

------------------------------------------------------
-- 馬券組合せオッズ（ワイド・馬連）
------------------------------------------------------
CREATE TABLE IF NOT EXISTS combination_odds (

    race_id TEXT,

    bet_type TEXT,

    horse_number_1 INTEGER,
    horse_number_2 INTEGER,

    odds_low REAL,
    odds_high REAL,

    PRIMARY KEY (race_id, bet_type, horse_number_1, horse_number_2)

);

------------------------------------------------------
-- 確定払戻金（的中判定・回収率集計には必ずこちらを使う）
------------------------------------------------------
CREATE TABLE IF NOT EXISTS payouts (

    race_id TEXT,
    bet_type TEXT,
    combination TEXT,

    payout_yen INTEGER,
    popularity INTEGER,

    PRIMARY KEY (race_id, bet_type, combination)

);

------------------------------------------------------
-- 条件別重みマスター（旧condition_masterシート相当）
-- 「競馬場×芝orダ×距離」の組み合わせごとに8項目の重み%を管理する
--
-- 脚質力（開発指示書2.2）だけは他の7項目と異なり、レース内正規化ではなく
-- 「脚質区分（逃げ/先行/差し/追込）ごとの重み値をそのまま代入」するため、
-- 単一のweight_kyakushitsu列ではなく、区分ごとに4列（weight_nige等）を持つ。
------------------------------------------------------
CREATE TABLE IF NOT EXISTS condition_weights (

    course TEXT,
    surface TEXT,
    distance INTEGER,

    weight_agari REAL,          -- 上がり力
    weight_nige REAL,           -- 脚質力（逃げ）
    weight_senko REAL,          -- 脚質力（先行）
    weight_sashi REAL,          -- 脚質力（差し）
    weight_oikomi REAL,         -- 脚質力（追込）
    weight_jockey REAL,         -- 騎手力
    weight_distance REAL,       -- 距離力
    weight_turn REAL,           -- 回り力
    weight_stability REAL,      -- 安定力
    weight_pedigree REAL,       -- 血統力
    weight_trainer REAL,        -- 調教師力

    PRIMARY KEY (course, surface, distance)

);

------------------------------------------------------
-- 特徴量
-- 8項目（素点・レース内Min-Max正規化後・重み適用後）に加え、
-- オッズ由来の特徴量、および素点ベース／オッズ反映後の
-- 両方の順位を明示的に保持する（B案：印はオッズ反映後順位を基準、
-- 素点ベース順位は並行してログに記録し比較検証に使う）
------------------------------------------------------
CREATE TABLE IF NOT EXISTS features (

    race_id TEXT,
    horse_id TEXT,

    -- 8項目（素点、条件別重み適用後）
    agari_power REAL,          -- 上がり力（過去5戦平均3F由来）
    kyakushitsu_power REAL,    -- 脚質力
    jockey_power REAL,         -- 騎手力（騎手複勝率由来）
    distance_power REAL,       -- 距離力（距離複勝率由来）
    turn_power REAL,           -- 回り力（回り複勝率由来）
    stability_power REAL,      -- 安定力（全成績連対率由来）
    pedigree_power REAL,       -- 血統力（種牡馬複勝率由来）
    trainer_power REAL,        -- 調教師力（調教師複勝率由来）

    -- ペースバイアス補正・総合力
    pace_bias_adjustment REAL, -- 脚質別ペースバイアス補正（倍率）
    overall_score REAL,        -- 素点の総合力 = 8項目合計 × (1 + pace_bias_adjustment)

    -- オッズ由来の特徴量
    market_odds REAL,          -- 単勝オッズ
    market_popularity INTEGER, -- 人気
    odds_adjusted_score REAL,  -- オッズ反映後の最終指数 = overall_score * (1/odds) * ODDS_SCORE_MULTIPLIER

    -- レース内順位（両方式を並行記録・比較する）
    raw_rank INTEGER,          -- 素点ベース順位（overall_scoreの降順）
    odds_adjusted_rank INTEGER,-- オッズ反映後順位（odds_adjusted_scoreの降順。印の基準）

    PRIMARY KEY (race_id, horse_id)

);

------------------------------------------------------
-- AI予測結果
------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (

    race_id TEXT,

    horse_id TEXT,

    score REAL,

    probability REAL,

    expected_value REAL,

    rank INTEGER,

    PRIMARY KEY (race_id, horse_id)

);
