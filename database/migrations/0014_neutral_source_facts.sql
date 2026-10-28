-- Preserve clause-independent reported cells without turning them into candidates.
ALTER TABLE esg.report_source_fact_snapshots
    ADD COLUMN fact_role text NOT NULL DEFAULT 'clause_source'
    CHECK (fact_role IN ('clause_source','neutral'));
CREATE INDEX report_source_fact_neutral_idx
    ON esg.report_source_fact_snapshots(source_report_id,fact_role,source_ir_run_id);

ALTER TABLE esg.measurement_contexts ADD COLUMN evidence_revision text;
