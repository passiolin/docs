-- 多库/多实例验证用的最小 schema。
-- 实例 A(121)上执行 mdb_a / mdb_b;实例 B(132)上执行 app_db。

-- ============ 实例 A:mdb_a / mdb_b ============
CREATE DATABASE IF NOT EXISTS mdb_a
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS mdb_b
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

USE mdb_a;
CREATE TABLE orders (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  amount DECIMAL(12,2) NOT NULL,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB;
CREATE TABLE sync_heartbeat (
  id TINYINT PRIMARY KEY,
  ts DATETIME(3) NOT NULL,
  seq BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB;
INSERT INTO sync_heartbeat VALUES (1, NOW(3), 1);
CREATE EVENT ev_heartbeat ON SCHEDULE EVERY 1 SECOND
  DO UPDATE sync_heartbeat SET ts=NOW(3), seq=seq+1 WHERE id=1;

USE mdb_b;
CREATE TABLE orders (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  amount DECIMAL(12,2) NOT NULL,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB;
CREATE TABLE sync_heartbeat (
  id TINYINT PRIMARY KEY,
  ts DATETIME(3) NOT NULL,
  seq BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB;
INSERT INTO sync_heartbeat VALUES (1, NOW(3), 1);
CREATE EVENT ev_heartbeat ON SCHEDULE EVERY 1 SECOND
  DO UPDATE sync_heartbeat SET ts=NOW(3), seq=seq+1 WHERE id=1;

-- ============ 实例 B:app_db(简化版) ============
CREATE DATABASE IF NOT EXISTS app_db
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE app_db;
CREATE TABLE orders (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  customer_id BIGINT NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB;
CREATE TABLE sync_heartbeat (
  id TINYINT PRIMARY KEY,
  ts DATETIME(3) NOT NULL,
  seq BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB;
INSERT INTO sync_heartbeat VALUES (1, NOW(3), 1);
CREATE EVENT ev_heartbeat ON SCHEDULE EVERY 1 SECOND
  DO UPDATE sync_heartbeat SET ts=NOW(3), seq=seq+1 WHERE id=1;
