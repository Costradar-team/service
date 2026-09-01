-- KAMIS + FIS tables. Charset utf8mb4.
-- Source of truth: docs/erd/kca_erd.dbml
-- Unique names follow data-pipeline/sql/schema.sql so load scripts can upsert.
-- Requires canonical_item (001_kca_schema.sql). Does not drop KCA/retailer tables.
-- 행이 생기면 이 DROP을 그대로 돌리지 않는다.

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS kamis_price_observation;
DROP TABLE IF EXISTS kamis_item;
DROP TABLE IF EXISTS fis_price_observation;
DROP TABLE IF EXISTS fis_item;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE kamis_item (
  kamis_item_id BIGINT NOT NULL AUTO_INCREMENT,
  canonical_item_id BIGINT NOT NULL,
  item_category_code VARCHAR(10) NOT NULL,
  item_code VARCHAR(20) NOT NULL,
  item_name VARCHAR(100) NOT NULL,
  kind_code VARCHAR(20) NOT NULL,
  kind_name VARCHAR(100) NOT NULL,
  rank_code VARCHAR(20) NOT NULL,
  rank_name VARCHAR(100) NULL,
  quantity DECIMAL(10, 2) NULL,
  unit VARCHAR(20) NULL,
  PRIMARY KEY (kamis_item_id),
  UNIQUE KEY uq_kamis_item_codes (
    item_category_code,
    item_code,
    kind_code,
    rank_code
  ),
  CONSTRAINT fk_kamis_item_canonical_item
    FOREIGN KEY (canonical_item_id) REFERENCES canonical_item (canonical_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE kamis_price_observation (
  kamis_price_observation_id BIGINT NOT NULL AUTO_INCREMENT,
  kamis_item_id BIGINT NOT NULL,
  observed_date DATE NOT NULL,
  price INT NOT NULL,
  unit_price DECIMAL(10, 2) NULL,
  scope_name VARCHAR(50) NOT NULL,
  PRIMARY KEY (kamis_price_observation_id),
  UNIQUE KEY uq_kamis_price_observation_grain (
    kamis_item_id,
    observed_date,
    scope_name
  ),
  KEY idx_kamis_price_observation_observed_date (observed_date),
  CONSTRAINT fk_kamis_price_observation_kamis_item
    FOREIGN KEY (kamis_item_id) REFERENCES kamis_item (kamis_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fis_item (
  fis_item_id BIGINT NOT NULL AUTO_INCREMENT,
  canonical_item_id BIGINT NOT NULL,
  item_key VARCHAR(50) NOT NULL,
  cmdt_id VARCHAR(30) NOT NULL,
  cmdt_se_cd VARCHAR(20) NOT NULL,
  item_name VARCHAR(100) NOT NULL,
  price_unit VARCHAR(30) NOT NULL,
  converted_unit VARCHAR(30) NULL,
  relation_type VARCHAR(30) NOT NULL,
  PRIMARY KEY (fis_item_id),
  UNIQUE KEY uq_fis_item_item_key (item_key),
  UNIQUE KEY uq_fis_item_source_code (cmdt_se_cd, cmdt_id),
  CONSTRAINT fk_fis_item_canonical_item
    FOREIGN KEY (canonical_item_id) REFERENCES canonical_item (canonical_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE fis_price_observation (
  fis_price_observation_id BIGINT NOT NULL AUTO_INCREMENT,
  fis_item_id BIGINT NOT NULL,
  contract_month CHAR(7) NOT NULL,
  trade_date DATE NOT NULL,
  close_price DECIMAL(12, 4) NOT NULL,
  unit_price DECIMAL(10, 2) NULL,
  change_amount DECIMAL(12, 4) NULL,
  change_rate_pct DECIMAL(8, 4) NULL,
  converted_price DECIMAL(12, 4) NULL,
  PRIMARY KEY (fis_price_observation_id),
  UNIQUE KEY uq_fis_price_item_contract_trade_date (
    fis_item_id,
    contract_month,
    trade_date
  ),
  KEY idx_fis_price_observation_trade_date (trade_date),
  CONSTRAINT fk_fis_price_observation_fis_item
    FOREIGN KEY (fis_item_id) REFERENCES fis_item (fis_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
