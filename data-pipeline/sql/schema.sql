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

CREATE TABLE IF NOT EXISTS store (
  store_id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(255) NOT NULL,
  PRIMARY KEY (store_id),
  UNIQUE KEY uq_store_name (name)
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
