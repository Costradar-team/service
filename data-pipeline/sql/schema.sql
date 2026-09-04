CREATE TABLE IF NOT EXISTS canonical_item (
  canonical_item_id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(50) NOT NULL,
  PRIMARY KEY (canonical_item_id),
  UNIQUE KEY uq_canonical_item_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS item_subtype (
  subtype_id BIGINT NOT NULL AUTO_INCREMENT,
  canonical_item_id BIGINT NOT NULL,
  name VARCHAR(100) NOT NULL,
  PRIMARY KEY (subtype_id),
  UNIQUE KEY uq_item_subtype_canonical_name (canonical_item_id, name),
  CONSTRAINT fk_item_subtype_canonical_item
    FOREIGN KEY (canonical_item_id) REFERENCES canonical_item (canonical_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS manufacturer (
  manufacturer_id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(100) NOT NULL,
  PRIMARY KEY (manufacturer_id),
  UNIQUE KEY uq_manufacturer_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS product (
  product_id BIGINT NOT NULL AUTO_INCREMENT,
  source_product_name VARCHAR(255) NOT NULL,
  manufacturer_id BIGINT NULL,
  manufacturer_id_for_unique BIGINT
    GENERATED ALWAYS AS (IFNULL(manufacturer_id, -1)) STORED,
  subtype_id BIGINT NOT NULL,
  quantity DECIMAL(10,2) NULL,
  unit VARCHAR(20) NULL,
  PRIMARY KEY (product_id),
  UNIQUE KEY uq_product_source_manufacturer_subtype (
    source_product_name,
    manufacturer_id_for_unique,
    subtype_id
  ),
  CONSTRAINT fk_product_manufacturer
    FOREIGN KEY (manufacturer_id) REFERENCES manufacturer (manufacturer_id),
  CONSTRAINT fk_product_item_subtype
    FOREIGN KEY (subtype_id) REFERENCES item_subtype (subtype_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS retailer (
  retailer_id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(255) NOT NULL,
  PRIMARY KEY (retailer_id),
  UNIQUE KEY uq_retailer_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS region (
  region_id BIGINT NOT NULL AUTO_INCREMENT,
  parent_region_id BIGINT NULL,
  name VARCHAR(50) NOT NULL,
  region_type VARCHAR(20) NOT NULL,
  root_region_name VARCHAR(100)
    GENERATED ALWAYS AS (
      CASE WHEN parent_region_id IS NULL THEN name ELSE NULL END
    ) STORED,
  PRIMARY KEY (region_id),
  UNIQUE KEY uq_region_parent_name (parent_region_id, name),
  UNIQUE KEY uq_region_root_name (root_region_name),
  CONSTRAINT fk_region_parent
    FOREIGN KEY (parent_region_id) REFERENCES region (region_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS store (
  store_id BIGINT NOT NULL AUTO_INCREMENT,
  retailer_id BIGINT NOT NULL,
  name VARCHAR(255) NOT NULL,
  source_store_name VARCHAR(255) NOT NULL,
  store_type VARCHAR(20) NOT NULL,
  store_status VARCHAR(20) NOT NULL DEFAULT 'open',
  match_status VARCHAR(20) NOT NULL DEFAULT 'matched',
  validation_status VARCHAR(20) NOT NULL DEFAULT 'valid',
  region_id BIGINT NULL,
  PRIMARY KEY (store_id),
  UNIQUE KEY uq_store_retailer_source_store_name (retailer_id, source_store_name),
  UNIQUE KEY uq_store_retailer_name (retailer_id, name),
  CONSTRAINT fk_store_retailer
    FOREIGN KEY (retailer_id) REFERENCES retailer (retailer_id),
  CONSTRAINT fk_store_region
    FOREIGN KEY (region_id) REFERENCES region (region_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS price_observation (
  price_observation_id BIGINT NOT NULL AUTO_INCREMENT,
  product_id BIGINT NOT NULL,
  store_id BIGINT NOT NULL,
  survey_date DATE NOT NULL,
  price INT NOT NULL,
  unit_price DECIMAL(10,2) NULL,
  is_sale BOOLEAN NULL,
  is_one_plus_one BOOLEAN NULL,
  PRIMARY KEY (price_observation_id),
  UNIQUE KEY uq_price_observation_product_store_date (
    product_id,
    store_id,
    survey_date
  ),
  KEY idx_price_observation_survey_date (survey_date),
  CONSTRAINT fk_price_observation_product
    FOREIGN KEY (product_id) REFERENCES product (product_id),
  CONSTRAINT fk_price_observation_store
    FOREIGN KEY (store_id) REFERENCES store (store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS retailer_product_listing (
  listing_id BIGINT NOT NULL AUTO_INCREMENT,
  product_id BIGINT NOT NULL,
  retailer_id BIGINT NOT NULL,
  source_product_id VARCHAR(100) NOT NULL,
  source_product_name VARCHAR(255) NOT NULL,
  product_url VARCHAR(1000) NOT NULL,
  PRIMARY KEY (listing_id),
  UNIQUE KEY uq_retailer_listing_source (retailer_id, source_product_id),
  CONSTRAINT fk_retailer_listing_product
    FOREIGN KEY (product_id) REFERENCES product (product_id),
  CONSTRAINT fk_retailer_listing_retailer
    FOREIGN KEY (retailer_id) REFERENCES retailer (retailer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS retailer_price_observation (
  retailer_price_observation_id BIGINT NOT NULL AUTO_INCREMENT,
  listing_id BIGINT NOT NULL,
  collected_at DATETIME NOT NULL,
  price INT NOT NULL,
  promotion_type VARCHAR(30) NULL,
  PRIMARY KEY (retailer_price_observation_id),
  UNIQUE KEY uq_retailer_price_listing_collected (listing_id, collected_at),
  KEY idx_retailer_price_observation_collected_at (collected_at),
  CONSTRAINT fk_retailer_price_observation_listing
    FOREIGN KEY (listing_id) REFERENCES retailer_product_listing (listing_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS kamis_item (
  kamis_item_id BIGINT NOT NULL AUTO_INCREMENT,
  canonical_item_id BIGINT NOT NULL,
  item_category_code VARCHAR(10) NOT NULL,
  item_code VARCHAR(20) NOT NULL,
  item_name VARCHAR(100) NOT NULL,
  kind_code VARCHAR(20) NOT NULL,
  kind_name VARCHAR(100) NOT NULL,
  rank_code VARCHAR(20) NOT NULL,
  rank_name VARCHAR(100) NULL,
  quantity DECIMAL(10,2) NULL,
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

CREATE TABLE IF NOT EXISTS kamis_price_observation (
  kamis_price_observation_id BIGINT NOT NULL AUTO_INCREMENT,
  kamis_item_id BIGINT NOT NULL,
  observed_date DATE NOT NULL,
  price INT NOT NULL,
  unit_price DECIMAL(10,2) NULL,
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

CREATE TABLE IF NOT EXISTS fis_item (
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

CREATE TABLE IF NOT EXISTS fis_price_observation (
  fis_price_observation_id BIGINT NOT NULL AUTO_INCREMENT,
  fis_item_id BIGINT NOT NULL,
  contract_month CHAR(7) NOT NULL,
  trade_date DATE NOT NULL,
  close_price DECIMAL(12,4) NOT NULL,
  unit_price DECIMAL(10,2) NULL,
  change_amount DECIMAL(12,4) NULL,
  change_rate_pct DECIMAL(8,4) NULL,
  converted_price DECIMAL(12,4) NULL,
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
