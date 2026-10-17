CREATE TABLE esg.import_batches (
    import_batch_id uuid PRIMARY KEY,
    source_report_id uuid NOT NULL REFERENCES esg.source_report_refs(source_report_id),
    bundle_job_id text NOT NULL,
    bundle_digest char(64) NOT NULL,
    package_id text NOT NULL,
    package_version text NOT NULL,
    package_digest char(64) NOT NULL,
    bundle_ref text NOT NULL,
    importer_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_report_id, bundle_job_id, bundle_digest)
);

ALTER TABLE esg.esg_disclosures ADD COLUMN source_fact_ref text;
ALTER TABLE esg.esg_disclosures ADD COLUMN source_identity jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE esg.esg_disclosures ADD COLUMN source_payload jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX esg_disclosures_source_fact_uk
    ON esg.esg_disclosures (source_report_id, source_fact_ref) WHERE source_fact_ref IS NOT NULL;

CREATE TABLE esg.import_items (
    import_batch_id uuid NOT NULL REFERENCES esg.import_batches(import_batch_id),
    bundle_record_id text NOT NULL,
    disclosure_id uuid NOT NULL REFERENCES esg.esg_disclosures(disclosure_id),
    PRIMARY KEY (import_batch_id, bundle_record_id)
);

CREATE TABLE esg.disclosure_relations (
    relation_id uuid PRIMARY KEY,
    source_disclosure_id uuid NOT NULL REFERENCES esg.esg_disclosures(disclosure_id),
    target_disclosure_id uuid REFERENCES esg.esg_disclosures(disclosure_id),
    relation_type text NOT NULL CHECK (relation_type IN ('operand', 'duplicate_disclosure', 'alternative_display', 'presentation')),
    relation_status text NOT NULL DEFAULT 'candidate' CHECK (relation_status IN ('candidate', 'confirmed', 'rejected')),
    formula_version text,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE esg.review_decisions (
    decision_id uuid PRIMARY KEY,
    disclosure_id uuid REFERENCES esg.esg_disclosures(disclosure_id),
    mapping_id uuid REFERENCES esg.disclosure_standard_mappings(disclosure_standard_mapping_id),
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected', 'review_required')),
    operator_ref text NOT NULL CHECK (length(trim(operator_ref)) > 0),
    reason text NOT NULL CHECK (length(trim(reason)) > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((disclosure_id IS NULL) <> (mapping_id IS NULL))
);

-- An unparsed statement must remain a statement, not invented subject/predicate/object.
ALTER TABLE esg.qualitative_claims ALTER COLUMN subject_text DROP NOT NULL;
ALTER TABLE esg.qualitative_claims ALTER COLUMN predicate_text DROP NOT NULL;
ALTER TABLE esg.qualitative_claims ALTER COLUMN object_text DROP NOT NULL;
ALTER TABLE esg.qualitative_claims ADD COLUMN statement_raw text;
ALTER TABLE esg.qualitative_claims ADD CONSTRAINT qualitative_claims_readable_ck CHECK (
    COALESCE(length(trim(statement_raw)), 0) > 0 OR
    (subject_text IS NOT NULL AND predicate_text IS NOT NULL AND object_text IS NOT NULL)
);

CREATE VIEW esg_api.v_qualitative_statements AS
SELECT d.disclosure_id, d.raw_indicator_name, q.statement_raw, q.summary_text,
       d.period_start, d.period_end, d.quality_status
FROM esg.esg_disclosures d JOIN esg.qualitative_claims q USING (disclosure_id)
WHERE d.quality_status = 'approved';

CREATE VIEW esg_api.v_quantitative_ranges AS
SELECT d.disclosure_id, v.raw_value_text, v.numeric_value,
       v.lower_bound, v.upper_bound, v.precision_qualifier, v.raw_unit
FROM esg.esg_disclosures d JOIN esg.structured_values v USING (disclosure_id)
WHERE d.quality_status = 'approved' AND v.value_type = 'numeric';
