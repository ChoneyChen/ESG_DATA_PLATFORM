-- All materialized report sources survive independently of standard mapping.
-- The same anchor can have another semantic interpretation without overwriting
-- a prior snapshot or changing the existing selected-fact collision contract.
CREATE TABLE esg.report_source_fact_snapshots (
    snapshot_id uuid PRIMARY KEY,
    source_report_id uuid NOT NULL REFERENCES esg.source_report_refs(source_report_id),
    source_fact_ref text NOT NULL CHECK (length(trim(source_fact_ref)) > 0),
    snapshot_digest char(64) NOT NULL CHECK (snapshot_digest ~ '^[0-9a-f]{64}$'),
    document_id text NOT NULL CHECK (length(trim(document_id)) > 0),
    source_ir_run_id text NOT NULL CHECK (length(trim(source_ir_run_id)) > 0),
    source_ir_revision integer NOT NULL CHECK (source_ir_revision > 0),
    kind text NOT NULL CHECK (kind IN ('quantitative','assertion')),
    source_row_label text,
    value_raw text,
    statement_raw text,
    unit_raw text,
    identity_raw jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(identity_raw) = 'object'),
    physical_sources jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(physical_sources) = 'array'),
    quality_status text NOT NULL CHECK (quality_status IN ('grounded','needs_confirmation','unbound')),
    provenance_status text NOT NULL CHECK (provenance_status IN ('physical','unbound')),
    identity_basis text NOT NULL CHECK (identity_basis IN ('frozen_manifest','evidence_transition','legacy_single_evidence')),
    snapshot_payload jsonb NOT NULL CHECK (jsonb_typeof(snapshot_payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_report_id, source_fact_ref, snapshot_digest)
);
CREATE TABLE esg.import_source_facts (
    import_batch_id uuid NOT NULL REFERENCES esg.import_batches(import_batch_id),
    source_fact_ref text NOT NULL CHECK (length(trim(source_fact_ref)) > 0),
    snapshot_id uuid NOT NULL REFERENCES esg.report_source_fact_snapshots(snapshot_id),
    core_bindings jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(core_bindings) = 'array'),
    PRIMARY KEY (import_batch_id, source_fact_ref)
);
CREATE INDEX report_source_fact_ir_idx ON esg.report_source_fact_snapshots(source_ir_run_id,source_ir_revision);
CREATE INDEX report_source_fact_report_idx ON esg.report_source_fact_snapshots(source_report_id,created_at,snapshot_id);
CREATE INDEX import_source_fact_snapshot_idx ON esg.import_source_facts(snapshot_id);
CREATE TRIGGER immutable_report_source_fact BEFORE UPDATE OR DELETE ON esg.report_source_fact_snapshots
    FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_import_source_fact BEFORE UPDATE OR DELETE ON esg.import_source_facts
    FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
