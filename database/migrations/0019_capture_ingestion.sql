-- Independent raw captures. Ingestion is not review approval or publication.
CREATE TABLE esg.capture_exports (
    export_id text PRIMARY KEY,
    input_digest text NOT NULL,
    manifest jsonb NOT NULL,
    receipt jsonb NOT NULL,
    coverage jsonb NOT NULL,
    diagnostics jsonb NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE esg.captured_facts (
    source_fact_id text PRIMARY KEY,
    source_anchor_id text NOT NULL,
    document_id text NOT NULL,
    ir_run_id text NOT NULL,
    ir_revision integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('quantitative','assertion')),
    value_raw text,
    value_numeric numeric,
    value_lower numeric,
    value_upper numeric,
    unit_raw text,
    statement_raw text,
    record_digest text NOT NULL,
    record jsonb NOT NULL,
    CHECK ((kind='quantitative' AND value_raw IS NOT NULL AND statement_raw IS NULL)
        OR (kind='assertion' AND statement_raw IS NOT NULL AND value_raw IS NULL))
);
CREATE INDEX captured_facts_document ON esg.captured_facts(document_id,ir_revision);
CREATE TABLE esg.capture_export_facts (
    export_id text NOT NULL REFERENCES esg.capture_exports(export_id),
    source_fact_id text NOT NULL REFERENCES esg.captured_facts(source_fact_id),
    PRIMARY KEY (export_id,source_fact_id)
);
CREATE TABLE esg.capture_clause_targets (
    export_id text NOT NULL REFERENCES esg.capture_exports(export_id),
    clause_key text NOT NULL,
    clause_digest text NOT NULL,
    contract jsonb NOT NULL,
    PRIMARY KEY (export_id,clause_key)
);
CREATE TABLE esg.capture_clause_relations (
    export_id text NOT NULL,
    source_fact_id text NOT NULL,
    clause_key text NOT NULL,
    relation text NOT NULL CHECK (relation IN ('match','uncertain','different')),
    mapping jsonb NOT NULL,
    PRIMARY KEY (export_id,source_fact_id,clause_key),
    FOREIGN KEY (export_id,source_fact_id) REFERENCES esg.capture_export_facts(export_id,source_fact_id),
    FOREIGN KEY (export_id,clause_key) REFERENCES esg.capture_clause_targets(export_id,clause_key)
);
CREATE TABLE esg.captured_properties (
    source_fact_id text NOT NULL REFERENCES esg.captured_facts(source_fact_id),
    container text NOT NULL CHECK (container IN ('identity','dimension')),
    field_key text NOT NULL,
    reported_state text NOT NULL,
    raw_text text,
    value jsonb,
    evidence jsonb NOT NULL,
    PRIMARY KEY (source_fact_id,container,field_key)
);
CREATE TABLE esg.captured_evidence (
    source_fact_id text NOT NULL REFERENCES esg.captured_facts(source_fact_id),
    container text NOT NULL,
    field_key text NOT NULL DEFAULT '',
    evidence_index integer NOT NULL,
    location jsonb NOT NULL,
    PRIMARY KEY (source_fact_id,container,field_key,evidence_index)
);
CREATE VIEW esg_api.captured_measurements AS
SELECT source_fact_id,document_id,ir_run_id,ir_revision,kind,value_raw,value_numeric::text,
    value_lower::text,value_upper::text,unit_raw,statement_raw FROM esg.captured_facts;
