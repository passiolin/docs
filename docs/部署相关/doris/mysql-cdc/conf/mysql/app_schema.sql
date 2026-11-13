-- 单机与 MGR 共用的业务表结构;MGR 场景把库名换成 app_mgr 再执行。
CREATE DATABASE IF NOT EXISTS app_db
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE app_db;

CREATE TABLE IF NOT EXISTS customers (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(64) NOT NULL,
  city VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS products (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  category VARCHAR(64) NOT NULL,
  price DECIMAL(10,2) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS orders (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  customer_id BIGINT NOT NULL,
  status TINYINT NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  KEY idx_created (created_at),
  KEY idx_customer (customer_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS order_items (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  order_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  qty INT NOT NULL,
  price DECIMAL(10,2) NOT NULL,
  KEY idx_order (order_id)
) ENGINE=InnoDB;

-- 单机场景用 EVENT 每秒推进;MGR 场景改为外部 writer(见 exporter/mgr_heartbeat.sh)
CREATE TABLE IF NOT EXISTS sync_heartbeat (
  id TINYINT PRIMARY KEY,
  ts DATETIME(3) NOT NULL,
  seq BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB;

INSERT INTO sync_heartbeat VALUES (1, NOW(3), 1)
  ON DUPLICATE KEY UPDATE ts=NOW(3), seq=seq+1;

CREATE EVENT IF NOT EXISTS ev_heartbeat ON SCHEDULE EVERY 1 SECOND
  DO UPDATE sync_heartbeat SET ts=NOW(3), seq=seq+1 WHERE id=1;
