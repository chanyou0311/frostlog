-- Hourly ambient temperature and humidity per sensor, read straight from R2 with DuckDB.
-- KEY_ID / SECRET: the Mac (read-only) token from `terraform output` in fumo-terraform.
INSTALL httpfs;
LOAD httpfs;

CREATE OR REPLACE SECRET r2 (
    TYPE r2,
    KEY_ID '<frostlog_mac_s3_access_key_id>',
    SECRET '<frostlog_mac_s3_secret_access_key>',
    ACCOUNT_ID '<cloudflare account id>'
);

SELECT
    date_trunc('hour', ts) AS hour,
    sensor,
    round(avg(temp_c), 1) AS temp_c,
    round(avg(humidity_pct), 1) AS humidity_pct,
    count(*) AS readings
FROM read_json_auto('r2://frostlog/ambient/*.jsonl', ignore_errors = true)
GROUP BY 1, 2
ORDER BY 1, 2;
