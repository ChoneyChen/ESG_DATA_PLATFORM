-- Scientific model: clause versions and observations are first-class rows.
-- Legacy candidate tables stay authoritative until reconciliation proves equal.

CREATE TABLE IF NOT EXISTS esg.clause_versions (
    clause_version_id text PRIMARY KEY,
    package_id text NOT NULL,
    package_version text NOT NULL,
    source_datapoint_id text NOT NULL,
    metric_id text NOT NULL,
    contract_digest text NOT NULL,
    label_zh text,
    value_type text NOT NULL CHECK (value_type IN ('numeric', 'statement')),
    canonical_unit text,
    scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    acceptance_state text NOT NULL DEFAULT 'untested'
        CHECK (acceptance_state IN ('untested', 'accepted', 'rejected', 'trialed', 'publishable', 'retired')),
    projected_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (package_id, package_version, source_datapoint_id)
);

CREATE TABLE IF NOT EXISTS esg.scientific_observation_model (
    observation_id uuid PRIMARY KEY,
    entity_ref text NOT NULL,
    clause_version_id text NOT NULL REFERENCES esg.clause_versions(clause_version_id),
    report_ref text NOT NULL,
    period_start date,
    period_end date,
    period_kind text,
    value_numeric numeric,
    value_lower numeric,
    value_upper numeric,
    value_text text,
    canonical_unit text,
    value_role text,
    measurement_method text,
    aggregation_role text,
    comparison_eligibility text,
    publication_state text NOT NULL DEFAULT 'draft'
        CHECK (publication_state IN ('draft', 'published', 'withdrawn')),
    publication_version integer,
    source_observation_id uuid REFERENCES esg.curated_observations(observation_id),
    projected_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS scientific_observation_entity_clause
    ON esg.scientific_observation_model (entity_ref, clause_version_id);
CREATE INDEX IF NOT EXISTS scientific_observation_publication
    ON esg.scientific_observation_model (publication_state, publication_version);

CREATE TABLE IF NOT EXISTS esg.observation_dimensions (
    observation_id uuid NOT NULL REFERENCES esg.scientific_observation_model(observation_id) ON DELETE CASCADE,
    axis text NOT NULL,
    code text NOT NULL,
    printed_label text,
    evidence_ref text,
    PRIMARY KEY (observation_id, axis)
);

CREATE TABLE IF NOT EXISTS esg.observation_evidence (
    evidence_id bigserial PRIMARY KEY,
    observation_id uuid NOT NULL REFERENCES esg.scientific_observation_model(observation_id) ON DELETE CASCADE,
    field text NOT NULL,
    ir_object_id text,
    page_index integer,
    bbox jsonb,
    quote text,
    source_fact_ref text
);
CREATE INDEX IF NOT EXISTS observation_evidence_observation ON esg.observation_evidence (observation_id);

CREATE TABLE IF NOT EXISTS esg.observation_model_sources (
    observation_id uuid NOT NULL REFERENCES esg.scientific_observation_model(observation_id) ON DELETE CASCADE,
    report_ref text NOT NULL,
    raw_value text,
    raw_unit text,
    raw_label text,
    source_fact_ref text,
    disclosure_id uuid,
    mapping_id uuid,
    context_id uuid,
    source_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (observation_id, report_ref, disclosure_id)
);

CREATE TABLE IF NOT EXISTS esg.coverage_decisions (
    coverage_id bigserial PRIMARY KEY,
    entity_ref text,
    clause_version_id text REFERENCES esg.clause_versions(clause_version_id),
    report_ref text,
    period_start date,
    period_end date,
    state text NOT NULL CHECK (state IN ('covered', 'unconfirmed', 'out_of_scope', 'missing',
                                         'not_covered', 'unmapped', 'insufficient_evidence', 'confirmed_not_disclosed')),
    scope_id text,
    reason text,
    decided_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS coverage_clause_report ON esg.coverage_decisions (clause_version_id, report_ref);

-- Read models. Column names and order match the legacy views so a consumer can
-- switch with a table-name change only.
CREATE OR REPLACE VIEW esg_api.scientific_clause_versions AS
SELECT clause_version_id, package_id, package_version, source_datapoint_id, metric_id,
       contract_digest, label_zh, value_type, canonical_unit, scopes, evidence,
       status, acceptance_state
FROM esg.clause_versions;

CREATE OR REPLACE VIEW esg_api.scientific_observations_v2 AS
SELECT o.observation_id, o.clause_version_id, c.metric_id, c.package_id, c.package_version,
       o.entity_ref, o.report_ref, o.period_start, o.period_end, o.period_kind,
       o.value_numeric, o.value_lower, o.value_upper, o.value_text, o.canonical_unit,
       o.value_role, o.measurement_method, o.aggregation_role, o.comparison_eligibility,
       o.publication_state, o.publication_version, o.source_observation_id, o.projected_at
FROM esg.scientific_observation_model o
JOIN esg.clause_versions c USING (clause_version_id);

CREATE OR REPLACE VIEW esg_api.scientific_observation_dimensions_v2 AS
SELECT observation_id, axis AS dimension_key, code AS dimension_value, printed_label, evidence_ref
FROM esg.observation_dimensions;

CREATE OR REPLACE VIEW esg_api.scientific_observation_evidence_v2 AS
SELECT observation_id, field AS field_key, ir_object_id, page_index, bbox, quote, source_fact_ref
FROM esg.observation_evidence;

CREATE OR REPLACE VIEW esg_api.scientific_observation_sources_v2 AS
SELECT observation_id, report_ref, raw_value, raw_unit, raw_label, source_fact_ref,
       disclosure_id, mapping_id, context_id, source_snapshot
FROM esg.observation_model_sources;

CREATE OR REPLACE VIEW esg_api.scientific_coverage_decisions_v2 AS
SELECT coverage_id, entity_ref, clause_version_id, report_ref, period_start, period_end,
       state, scope_id, reason, decided_at
FROM esg.coverage_decisions;
