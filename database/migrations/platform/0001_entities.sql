CREATE SCHEMA IF NOT EXISTS plat;
CREATE TABLE plat.entities (
    entity_ref text PRIMARY KEY CHECK (length(trim(entity_ref)) > 0),
    name_zh text NOT NULL CHECK (length(trim(name_zh)) > 0),
    entity_kind text NOT NULL CHECK (entity_kind IN ('group','listed_company','subsidiary','other')),
    industry_code text NOT NULL,
    country_code text NOT NULL,
    is_demo boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE plat.import_receipts (
    receipt_id text PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('imported','failed')),
    import_batch_id uuid REFERENCES esg.import_batches(import_batch_id),
    detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE plat.extraction_scopes (
    import_batch_id uuid PRIMARY KEY REFERENCES esg.import_batches(import_batch_id),
    profile_id text NOT NULL,
    profile_version text NOT NULL,
    profile_digest text NOT NULL,
    dictionary_version text NOT NULL,
    source_datapoint_ids text[] NOT NULL,
    result_state text NOT NULL CHECK (result_state IN ('partial','complete')),
    scope_snapshot jsonb NOT NULL
);
CREATE TABLE plat.coverage_decisions (
    coverage_decision_id uuid PRIMARY KEY,
    entity_ref text NOT NULL REFERENCES plat.entities(entity_ref),
    source_datapoint_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('not_selected','not_extracted','source_unreadable',
        'not_disclosed_in_reviewed_scope','not_applicable','review_required')),
    period_start date NOT NULL,
    period_end date NOT NULL CHECK (period_end >= period_start),
    evidence_scope jsonb NOT NULL,
    operator_ref text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX plat_coverage_lookup ON plat.coverage_decisions(entity_ref,source_datapoint_id,created_at DESC);
