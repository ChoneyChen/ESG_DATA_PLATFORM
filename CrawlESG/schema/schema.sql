-- ============================================================================
--  ESEF / ESG 索引层 schema (SQLite)
--  执行: sqlite3 esef_index.db < schema.sql
-- ============================================================================
PRAGMA foreign_keys = ON;

-- ----------------------------- 维度 / 受控词表 -----------------------------
CREATE TABLE IF NOT EXISTS entity (
    lei            TEXT PRIMARY KEY,
    entity_name    TEXT NOT NULL,
    company_alias  TEXT,                 -- 旧 company_id / 路透代码等别名
    country        TEXT,
    industry_code  TEXT,                 -- 派生(NACE/行业代码), 不手工打标
    industry_name  TEXT
);

CREATE TABLE IF NOT EXISTS exchange (
    exchange_id    TEXT PRIMARY KEY,     -- LSE / ESEF-GB ...
    exchange_name  TEXT,
    country        TEXT
);

CREATE TABLE IF NOT EXISTS esrs_datapoint (
    esrs_code      TEXT PRIMARY KEY,     -- E1-6 / E1-5 / S1 / G1 ...
    topic_code     TEXT,                 -- E1 / E3 / S1 / G1 (派生主题)
    topic_name_en  TEXT,
    standard       TEXT DEFAULT 'ESRS',
    name_en        TEXT,
    name_zh        TEXT,                 -- 显示别名, 不作键
    default_unit   TEXT
);

-- ----------------------------- 原始申报 (不去重) ---------------------------
CREATE TABLE IF NOT EXISTS report_raw (
    fxo_id              TEXT PRIMARY KEY,
    lei                 TEXT NOT NULL REFERENCES entity(lei),
    period_end          TEXT NOT NULL,            -- YYYY-MM-DD
    country             TEXT,
    report_type         TEXT,                     -- ESEF / UKSEF (格式)
    seq                 INTEGER,
    sha256              TEXT NOT NULL,
    error_count         INTEGER,
    warning_count       INTEGER,
    inconsistency_count INTEGER,
    processed           TEXT,
    date_added          TEXT,
    package_url         TEXT,
    json_url            TEXT,
    viewer_url          TEXT,
    api_id              TEXT
);
CREATE INDEX IF NOT EXISTS ix_raw_lei_pe ON report_raw(lei, period_end);
CREATE INDEX IF NOT EXISTS ix_raw_sha     ON report_raw(sha256);

-- ----------------------------- 主报告 (去重后) -----------------------------
CREATE TABLE IF NOT EXISTS report (
    report_id    TEXT PRIMARY KEY,                -- 代理键 R000001
    fxo_id       TEXT NOT NULL UNIQUE REFERENCES report_raw(fxo_id),
    lei          TEXT NOT NULL REFERENCES entity(lei),
    exchange_id  TEXT REFERENCES exchange(exchange_id),
    period_end   TEXT NOT NULL,
    report_year  INTEGER GENERATED ALWAYS AS (CAST(substr(period_end,1,4) AS INTEGER)) STORED,
    country      TEXT,
    report_type  TEXT,
    language     TEXT,
    sha256       TEXT NOT NULL UNIQUE,
    esg_flag     INTEGER DEFAULT 0,               -- 锚点检测: 含ESRS声明
    package_url  TEXT,
    json_url     TEXT,
    viewer_url   TEXT,
    kodo_key     TEXT,                            -- 对象在七牛Kodo桶内的路径(权威存储位置); 上传后回填
    n_versions   INTEGER DEFAULT 1,
    UNIQUE(lei, period_end)
);
CREATE INDEX IF NOT EXISTS ix_report_cy ON report(country, report_year);

-- ----------------------------- 抽取指标值 ----------------------------------
CREATE TABLE IF NOT EXISTS indicator_value (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id      TEXT NOT NULL REFERENCES report(report_id),
    esrs_code      TEXT NOT NULL REFERENCES esrs_datapoint(esrs_code),
    raw_label      TEXT,
    value_num      REAL,
    unit           TEXT,
    scope          TEXT,
    fiscal_year    INTEGER,
    source         TEXT,                           -- xbrl-json / text-anchor
    confidence     TEXT,                           -- high / medium
    page_or_anchor TEXT,
    UNIQUE(report_id, esrs_code, scope, unit)
);

-- ----------------------------- 覆盖率视图 ----------------------------------
CREATE VIEW IF NOT EXISTS v_coverage AS
SELECT country, report_year AS fiscal_year, COUNT(*) AS collected_Y
FROM report
GROUP BY country, report_year;

-- ESRS 受控词表种子数据 (与 esg_extract_pilot.py 的 ANCHORS 对齐)
INSERT OR IGNORE INTO esrs_datapoint (esrs_code, topic_code, topic_name_en, name_en, name_zh, default_unit) VALUES
 ('E1-5','E1','Climate change','Energy consumption and mix','能源消耗与结构','MWh'),
 ('E1-6','E1','Climate change','Gross Scope 1/2/3 GHG emissions','GHG排放(Scope1/2/3)','tCO2e'),
 ('E1-7','E1','Climate change','GHG removals','GHG移除',NULL),
 ('E1-8','E1','Climate change','Internal carbon pricing','内部碳价',NULL),
 ('E3-4','E3','Water','Water consumption','水资源消耗','m3'),
 ('S1','S1','Own workforce','Total number of employees','劳动力总数',NULL),
 ('G1','G1','Business conduct','Confirmed incidents of corruption','腐败事件',NULL);
