CREATE TABLE logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    service TEXT NOT NULL,
    level TEXT NOT NULL,
    endpoint TEXT,
    latency_ms FLOAT,
    status_code INT,
    message TEXT,
    request_id TEXT,
    anomaly_score FLOAT,
    is_anomaly BOOLEAN DEFAULT FALSE
);

CREATE TABLE ground_truth (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    anomaly_type TEXT NOT NULL,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs (timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_anom ON logs (is_anomaly, timestamp);
CREATE INDEX IF NOT EXISTS idx_gt_ts ON ground_truth (timestamp);
