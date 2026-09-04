-- KCA 7 tables. Charset utf8mb4.
-- Source of truth: docs/erd/kca_erd.dbml (GitHub main, retailer 포함).
-- Unique names follow data-pipeline/scripts/load_kca_mysql.py so the load script can upsert.
-- No users, saved_lists, or region. KAMIS/FIS는 002.
--
-- 지금 로컬 테이블은 비어 있다. 옛 store(name만)와 호환되지 않아서 DROP 후 다시 만든다.
-- 적재된 행이 생기면 이 DROP을 그대로 돌리지 않는다.

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS kamis_price_observation;
DROP TABLE IF EXISTS fis_price_observation;
DROP TABLE IF EXISTS kamis_item;
DROP TABLE IF EXISTS fis_item;
DROP TABLE IF EXISTS price_observation;
DROP TABLE IF EXISTS product;
DROP TABLE IF EXISTS store;
DROP TABLE IF EXISTS retailer;
DROP TABLE IF EXISTS item_subtype;
DROP TABLE IF EXISTS manufacturer;
DROP TABLE IF EXISTS canonical_item;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE canonical_item (
  canonical_item_id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(50) NOT NULL,
  PRIMARY KEY (canonical_item_id),
  UNIQUE KEY name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE item_subtype (
  subtype_id BIGINT NOT NULL AUTO_INCREMENT,
  canonical_item_id BIGINT NOT NULL,
  name VARCHAR(100) NOT NULL,
  PRIMARY KEY (subtype_id),
  UNIQUE KEY uq_item_subtype_item_name (canonical_item_id, name),
  CONSTRAINT fk_item_subtype_canonical
    FOREIGN KEY (canonical_item_id) REFERENCES canonical_item (canonical_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE manufacturer (
  manufacturer_id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  PRIMARY KEY (manufacturer_id),
  UNIQUE KEY name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE product (
  product_id BIGINT NOT NULL AUTO_INCREMENT,
  source_product_name VARCHAR(255) NOT NULL,
  manufacturer_id BIGINT NULL,
  subtype_id BIGINT NOT NULL,
  quantity DECIMAL(10, 2) NULL,
  unit VARCHAR(20) NULL,
  PRIMARY KEY (product_id),
  UNIQUE KEY source_product_name (source_product_name),
  CONSTRAINT fk_product_manufacturer
    FOREIGN KEY (manufacturer_id) REFERENCES manufacturer (manufacturer_id),
  CONSTRAINT fk_product_subtype
    FOREIGN KEY (subtype_id) REFERENCES item_subtype (subtype_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE retailer (
  retailer_id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(255) NOT NULL,
  PRIMARY KEY (retailer_id),
  UNIQUE KEY name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE store (
  store_id BIGINT NOT NULL AUTO_INCREMENT,
  retailer_id BIGINT NOT NULL,
  name VARCHAR(255) NOT NULL,
  source_store_name VARCHAR(255) NOT NULL,
  PRIMARY KEY (store_id),
  UNIQUE KEY source_store_name (source_store_name),
  UNIQUE KEY uq_store_retailer_name (retailer_id, name),
  CONSTRAINT fk_store_retailer
    FOREIGN KEY (retailer_id) REFERENCES retailer (retailer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE price_observation (
  price_observation_id BIGINT NOT NULL AUTO_INCREMENT,
  product_id BIGINT NOT NULL,
  store_id BIGINT NOT NULL,
  survey_date DATE NOT NULL,
  price INT NOT NULL,
  unit_price DECIMAL(10, 2) NULL,
  is_sale BOOLEAN NULL,
  is_one_plus_one BOOLEAN NULL,
  PRIMARY KEY (price_observation_id),
  UNIQUE KEY uq_price_product_store_date (product_id, store_id, survey_date),
  CONSTRAINT fk_price_obs_product
    FOREIGN KEY (product_id) REFERENCES product (product_id),
  CONSTRAINT fk_price_obs_store
    FOREIGN KEY (store_id) REFERENCES store (store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
