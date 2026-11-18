-- 1000 设备 × 365 天 × N 个指标,长表存储,每行一个测点
-- ts 为 epoch 秒,基准 2025-01-01T00:00:00Z,步长 86400
CREATE TABLE metrics(
    device_id INTEGER NOT NULL,
    ts        INTEGER NOT NULL,
    metric_id INTEGER NOT NULL,
    value     REAL    NOT NULL
);

-- 设备维度:点查与单设备全量范围查
CREATE INDEX idx_dev_ts ON metrics(device_id, ts);

-- 指标维度:跨设备的时间窗口聚合
CREATE INDEX idx_metric_ts ON metrics(metric_id, ts);
