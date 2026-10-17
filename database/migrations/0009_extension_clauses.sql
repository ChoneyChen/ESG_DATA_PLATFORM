-- Project extensions have independent identities; official ESRS IDs remain unchanged.
ALTER TABLE esg.metric_definition_versions ALTER COLUMN source_datapoint_id DROP NOT NULL;
ALTER TABLE esg.metric_definition_versions ADD COLUMN clause_kind text NOT NULL DEFAULT 'original_datapoint';
ALTER TABLE esg.metric_definition_versions ADD COLUMN extension_id text;
ALTER TABLE esg.metric_definition_versions ADD CONSTRAINT metric_clause_identity_ck CHECK (
    (clause_kind='original_datapoint' AND source_datapoint_id IS NOT NULL AND extension_id IS NULL) OR
    (clause_kind='project_extension' AND source_datapoint_id IS NULL AND extension_id IS NOT NULL AND length(trim(extension_id))>0)
);
ALTER TABLE esg.dictionary_membership ALTER COLUMN source_datapoint_id DROP NOT NULL;
ALTER TABLE esg.dictionary_membership ADD COLUMN clause_kind text NOT NULL DEFAULT 'original_datapoint';
ALTER TABLE esg.dictionary_membership ADD COLUMN extension_id text;
ALTER TABLE esg.dictionary_membership ADD CONSTRAINT dictionary_clause_identity_ck CHECK (
    (clause_kind='original_datapoint' AND source_datapoint_id IS NOT NULL AND extension_id IS NULL) OR
    (clause_kind='project_extension' AND source_datapoint_id IS NULL AND extension_id IS NOT NULL AND length(trim(extension_id))>0)
);
CREATE UNIQUE INDEX dictionary_extension_identity_uk ON esg.dictionary_membership(dictionary_version,extension_id)
    WHERE extension_id IS NOT NULL;
ALTER TABLE esg.standard_requirements ADD COLUMN clause_kind text NOT NULL DEFAULT 'original_datapoint';
ALTER TABLE esg.standard_requirements ADD COLUMN extension_id text;
ALTER TABLE esg.standard_requirements ADD CONSTRAINT standard_requirement_clause_identity_ck CHECK (
    (clause_kind='original_datapoint' AND extension_id IS NULL) OR
    (clause_kind='project_extension' AND extension_id IS NOT NULL AND length(trim(extension_id))>0 AND requirement_code=extension_id)
);
ALTER TABLE esg.curated_observations ADD COLUMN value_origin text NOT NULL DEFAULT 'reported'
    CHECK (value_origin IN ('reported','derived'));

CREATE TABLE esg.extension_proposals (
    proposal_id text PRIMARY KEY CHECK (length(trim(proposal_id))>0),
    extension_id text NOT NULL,
    definition_version text NOT NULL,
    definition_digest char(64) NOT NULL,
    proposal jsonb NOT NULL CHECK (proposal->>'status'='proposed'),
    submitted_by text NOT NULL CHECK (length(trim(submitted_by))>0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE esg.extension_definition_decisions (
    decision_order bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    approval_id text PRIMARY KEY CHECK (length(trim(approval_id))>0),
    proposal_id text NOT NULL REFERENCES esg.extension_proposals(proposal_id),
    extension_id text NOT NULL,
    definition_version text NOT NULL,
    definition_digest char(64) NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved','rejected')),
    actor_ref text NOT NULL CHECK (length(trim(actor_ref))>0),
    reason text NOT NULL CHECK (length(trim(reason))>0),
    decided_at timestamptz NOT NULL,
    authority_roles jsonb NOT NULL CHECK (jsonb_typeof(authority_roles)='array' AND authority_roles @> '["approve_extension_definition"]'),
    receipt jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE esg.extension_releases (
    extension_id text NOT NULL,
    definition_version text NOT NULL,
    release_digest char(64) NOT NULL UNIQUE,
    definition_digest char(64) NOT NULL,
    approval_id text NOT NULL REFERENCES esg.extension_definition_decisions(approval_id),
    parent_package_id text NOT NULL,
    parent_package_version text NOT NULL,
    parent_source_digest char(64) NOT NULL,
    release jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (extension_id,definition_version)
);
CREATE TABLE esg.extension_derivation_requests (
    request_id uuid PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE CHECK (length(trim(idempotency_key))>0),
    payload_digest char(64) NOT NULL,
    calculation_digest char(64) NOT NULL UNIQUE,
    payload jsonb NOT NULL,
    input_dataset_version bigint NOT NULL REFERENCES esg.dataset_versions(dataset_version),
    extension_id text NOT NULL,
    definition_version text NOT NULL,
    release_digest char(64) NOT NULL,
    rule_id text NOT NULL,
    rule_digest char(64) NOT NULL,
    target_metric_id text NOT NULL,
    target_definition_version text NOT NULL,
    requested_by text NOT NULL CHECK (length(trim(requested_by))>0),
    status text NOT NULL CHECK (status IN ('blocked','candidate_created')),
    reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    evaluated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (extension_id,definition_version) REFERENCES esg.extension_releases(extension_id,definition_version),
    FOREIGN KEY (target_metric_id,target_definition_version) REFERENCES esg.metric_definition_versions(metric_id,definition_version)
);
CREATE TABLE esg.extension_derivation_idempotency_keys (
    idempotency_key text PRIMARY KEY CHECK (length(trim(idempotency_key))>0),
    request_id uuid NOT NULL REFERENCES esg.extension_derivation_requests(request_id),
    payload_digest char(64) NOT NULL,
    requested_by text NOT NULL CHECK (length(trim(requested_by))>0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE esg.extension_derived_candidates (
    candidate_id uuid PRIMARY KEY,
    request_id uuid NOT NULL UNIQUE REFERENCES esg.extension_derivation_requests(request_id),
    metric_id text NOT NULL,
    definition_version text NOT NULL,
    value_origin text NOT NULL DEFAULT 'derived' CHECK (value_origin='derived'),
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status='pending'),
    numeric_value numeric NOT NULL,
    canonical_unit text NOT NULL,
    reviewed_axes jsonb NOT NULL CHECK (jsonb_typeof(reviewed_axes)='object'),
    comparison_eligibility text NOT NULL CHECK (comparison_eligibility IN ('comparable','not_comparable')),
    comparison_reasons jsonb NOT NULL,
    lineage_snapshot jsonb NOT NULL,
    lineage_digest char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (metric_id,definition_version) REFERENCES esg.metric_definition_versions(metric_id,definition_version)
);
CREATE TABLE esg.extension_operand_lineage (
    candidate_id uuid NOT NULL REFERENCES esg.extension_derived_candidates(candidate_id),
    operand_id text NOT NULL,
    observation_id uuid NOT NULL REFERENCES esg.curated_observations(observation_id),
    input_dataset_version bigint NOT NULL,
    platform_metric_id text NOT NULL,
    metric_definition_version text NOT NULL,
    standard_metric_id text NOT NULL,
    source_datapoint_id text NOT NULL,
    context_id uuid NOT NULL REFERENCES esg.measurement_contexts(context_id),
    context_revision integer NOT NULL CHECK (context_revision>0),
    disclosure_id uuid NOT NULL REFERENCES esg.esg_disclosures(disclosure_id),
    mapping_id uuid NOT NULL REFERENCES esg.disclosure_standard_mappings(disclosure_standard_mapping_id),
    fact_review_decision_id uuid NOT NULL REFERENCES esg.review_decisions(decision_id),
    mapping_review_decision_id uuid NOT NULL REFERENCES esg.review_decisions(decision_id),
    original_numeric_value numeric NOT NULL,
    original_canonical_unit text NOT NULL,
    converted_numeric_value numeric NOT NULL,
    canonical_unit text NOT NULL,
    normalization_rule_version text NOT NULL REFERENCES esg.normalization_rule_versions(rule_version),
    normalization_rule_digest char(64) NOT NULL,
    reviewed_axes jsonb NOT NULL,
    operand_snapshot jsonb NOT NULL,
    PRIMARY KEY (candidate_id,operand_id),
    UNIQUE (candidate_id,observation_id),
    FOREIGN KEY (input_dataset_version,observation_id) REFERENCES esg.dataset_membership(dataset_version,observation_id),
    FOREIGN KEY (platform_metric_id,metric_definition_version) REFERENCES esg.metric_definition_versions(metric_id,definition_version)
);
CREATE TABLE esg.extension_candidate_reviews (
    review_order bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    decision_id uuid PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES esg.extension_derived_candidates(candidate_id),
    decision text NOT NULL CHECK (decision IN ('approved','rejected','review_required')),
    operator_ref text NOT NULL CHECK (length(trim(operator_ref))>0),
    reason text NOT NULL CHECK (length(trim(reason))>0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE esg.derived_observation_lineage (
    observation_id uuid PRIMARY KEY REFERENCES esg.curated_observations(observation_id),
    candidate_id uuid NOT NULL REFERENCES esg.extension_derived_candidates(candidate_id),
    calculation_review_decision_id uuid NOT NULL REFERENCES esg.extension_candidate_reviews(decision_id),
    lineage_snapshot jsonb NOT NULL,
    lineage_digest char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE esg.derived_publication_states (
    candidate_id uuid PRIMARY KEY REFERENCES esg.extension_derived_candidates(candidate_id),
    review_status text NOT NULL,
    publication_status text NOT NULL CHECK (publication_status IN ('not_published','blocked','published','withdrawn')),
    reason_codes jsonb NOT NULL,
    observation_id uuid REFERENCES esg.curated_observations(observation_id),
    evaluated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX extension_decision_latest_idx ON esg.extension_definition_decisions(extension_id,definition_version,decision_order DESC);
CREATE INDEX derived_review_latest_idx ON esg.extension_candidate_reviews(candidate_id,review_order DESC);
CREATE INDEX extension_requests_dataset_idx ON esg.extension_derivation_requests(input_dataset_version,status);
-- Cross-row authority is checked by the database as well as by the API worker.
CREATE FUNCTION esg.check_extension_operand_lineage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected_dataset bigint; stored record; pair record; conversion jsonb; expected_value numeric; declared jsonb;
BEGIN
    SELECT r.input_dataset_version INTO expected_dataset FROM esg.extension_derivation_requests r
      JOIN esg.extension_derived_candidates c USING(request_id) WHERE c.candidate_id=NEW.candidate_id;
    IF NEW.input_dataset_version IS DISTINCT FROM expected_dataset THEN RAISE EXCEPTION 'operand dataset differs from request'; END IF;
    SELECT item INTO declared FROM esg.extension_derived_candidates c,
      jsonb_array_elements(c.lineage_snapshot->'operands') AS item WHERE c.candidate_id=NEW.candidate_id AND item->>'operand_id'=NEW.operand_id;
    IF declared IS NULL OR declared->>'observation_id' IS DISTINCT FROM NEW.observation_id::text
      OR declared->>'metric_id' IS DISTINCT FROM NEW.platform_metric_id OR declared->>'standard_metric_id' IS DISTINCT FROM NEW.standard_metric_id
      OR declared->>'context_id' IS DISTINCT FROM NEW.context_id::text OR declared->>'fact_review_decision_id' IS DISTINCT FROM NEW.fact_review_decision_id::text
      OR declared->>'mapping_review_decision_id' IS DISTINCT FROM NEW.mapping_review_decision_id::text
      OR declared->'reviewed_axes' IS DISTINCT FROM NEW.reviewed_axes OR (declared->>'numeric_value')::numeric IS DISTINCT FROM NEW.converted_numeric_value
      OR declared->>'canonical_unit' IS DISTINCT FROM NEW.canonical_unit THEN RAISE EXCEPTION 'operand differs from frozen calculation receipt'; END IF;
    SELECT o.*,md.definition,md.source_datapoint_id AS original_id,os.source_snapshot,c.revision AS context_revision,
      c.entity_ref AS context_entity_ref,c.period_start::text AS context_period_start,c.period_end::text AS context_period_end,
      c.boundary_code AS context_boundary_code,c.value_role AS context_value_role,c.measurement_method AS context_method,c.dimensions AS context_dimensions,
      n.definition AS normalization_definition,n.rule_digest INTO stored
      FROM esg.observation_sources os JOIN esg.curated_observations o USING(observation_id)
      JOIN esg.metric_definition_versions md USING(metric_id,definition_version)
      JOIN esg.measurement_contexts c USING(context_id)
      JOIN esg.normalization_rule_versions n ON n.rule_version=o.normalization_rule_version
      WHERE os.observation_id=NEW.observation_id AND os.context_id=NEW.context_id
        AND os.disclosure_id=NEW.disclosure_id AND os.mapping_id=NEW.mapping_id;
    IF NOT FOUND OR NEW.platform_metric_id IS DISTINCT FROM stored.metric_id
      OR NEW.metric_definition_version IS DISTINCT FROM stored.definition_version
      OR NEW.standard_metric_id IS DISTINCT FROM stored.definition->'standard_binding'->>'metric_id'
      OR NEW.source_datapoint_id IS DISTINCT FROM stored.original_id
      OR NEW.context_revision IS DISTINCT FROM stored.context_revision
      OR NEW.original_numeric_value IS DISTINCT FROM stored.numeric_value
      OR NEW.original_canonical_unit IS DISTINCT FROM stored.canonical_unit
      OR NEW.normalization_rule_version IS DISTINCT FROM stored.normalization_rule_version
      OR NEW.normalization_rule_digest IS DISTINCT FROM stored.rule_digest
      OR stored.source_snapshot->>'fact_review_decision_id' IS DISTINCT FROM NEW.fact_review_decision_id::text
      OR stored.source_snapshot->>'mapping_review_decision_id' IS DISTINCT FROM NEW.mapping_review_decision_id::text
      OR NEW.operand_snapshot->'source_snapshot' IS DISTINCT FROM stored.source_snapshot
      OR NOT EXISTS(SELECT 1 FROM esg.review_decisions WHERE decision_id=NEW.fact_review_decision_id AND disclosure_id=NEW.disclosure_id AND decision='approved')
      OR NOT EXISTS(SELECT 1 FROM esg.review_decisions WHERE decision_id=NEW.mapping_review_decision_id AND mapping_id=NEW.mapping_id AND decision='approved') THEN
        RAISE EXCEPTION 'operand lineage differs from authoritative source';
    END IF;
    FOR pair IN SELECT * FROM jsonb_each_text(NEW.reviewed_axes) LOOP
      IF pair.value IS DISTINCT FROM (CASE pair.key
        WHEN 'entity_ref' THEN stored.context_entity_ref WHEN 'period_start' THEN stored.context_period_start
        WHEN 'period_end' THEN stored.context_period_end WHEN 'boundary_code' THEN stored.context_boundary_code
        WHEN 'value_role' THEN stored.context_value_role WHEN 'measurement_method' THEN stored.context_method
        ELSE stored.context_dimensions->>pair.key END) THEN RAISE EXCEPTION 'operand axis differs from reviewed context: %',pair.key; END IF;
    END LOOP;
    SELECT rule INTO conversion FROM jsonb_array_elements(stored.normalization_definition->'conversions') AS rule
      WHERE rule->>'canonical_unit'=NEW.canonical_unit AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(rule->'aliases') AS alias
        WHERE regexp_replace(alias,'[[:space:]]','','g')=regexp_replace(stored.canonical_unit,'[[:space:]]','','g')) LIMIT 1;
    IF conversion IS NULL THEN RAISE EXCEPTION 'operand target unit conversion missing'; END IF;
    expected_value:=NEW.original_numeric_value*(conversion->>'multiplier')::numeric/(conversion->>'divisor')::numeric;
    IF NEW.converted_numeric_value IS DISTINCT FROM expected_value THEN RAISE EXCEPTION 'operand converted value differs from exact rule'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER validate_extension_operand_lineage BEFORE INSERT ON esg.extension_operand_lineage FOR EACH ROW EXECUTE FUNCTION esg.check_extension_operand_lineage();
CREATE FUNCTION esg.check_derived_observation_lineage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate record; observation record;
BEGIN
    SELECT * INTO candidate FROM esg.extension_derived_candidates WHERE candidate_id=NEW.candidate_id;
    SELECT * INTO observation FROM esg.curated_observations WHERE observation_id=NEW.observation_id;
    IF observation.value_origin<>'derived' OR observation.metric_id<>candidate.metric_id OR observation.definition_version<>candidate.definition_version
      OR observation.numeric_value IS DISTINCT FROM candidate.numeric_value OR observation.canonical_unit<>candidate.canonical_unit
      OR observation.entity_ref IS DISTINCT FROM candidate.reviewed_axes->>'entity_ref'
      OR observation.period_start::text IS DISTINCT FROM candidate.reviewed_axes->>'period_start'
      OR observation.period_end::text IS DISTINCT FROM candidate.reviewed_axes->>'period_end'
      OR observation.boundary_code IS DISTINCT FROM candidate.reviewed_axes->>'boundary_code'
      OR observation.value_role IS DISTINCT FROM candidate.reviewed_axes->>'value_role'
      OR observation.dimensions IS DISTINCT FROM candidate.reviewed_axes-ARRAY['entity_ref','period_start','period_end','boundary_code','value_role','measurement_method']
      OR NEW.lineage_snapshot IS DISTINCT FROM candidate.lineage_snapshot OR NEW.lineage_digest<>candidate.lineage_digest
      OR NOT EXISTS(SELECT 1 FROM esg.extension_candidate_reviews WHERE decision_id=NEW.calculation_review_decision_id
        AND candidate_id=NEW.candidate_id AND decision='approved') THEN RAISE EXCEPTION 'derived observation lineage differs from approved candidate'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER validate_derived_observation_lineage BEFORE INSERT ON esg.derived_observation_lineage FOR EACH ROW EXECUTE FUNCTION esg.check_derived_observation_lineage();
CREATE TRIGGER immutable_extension_proposal BEFORE UPDATE OR DELETE ON esg.extension_proposals FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_extension_definition_decision BEFORE UPDATE OR DELETE ON esg.extension_definition_decisions FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_extension_release BEFORE UPDATE OR DELETE ON esg.extension_releases FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_extension_candidate BEFORE UPDATE OR DELETE ON esg.extension_derived_candidates FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_extension_derivation_key BEFORE UPDATE OR DELETE ON esg.extension_derivation_idempotency_keys FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_extension_operand_lineage BEFORE UPDATE OR DELETE ON esg.extension_operand_lineage FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_extension_candidate_review BEFORE UPDATE OR DELETE ON esg.extension_candidate_reviews FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE TRIGGER immutable_derived_observation_lineage BEFORE UPDATE OR DELETE ON esg.derived_observation_lineage FOR EACH ROW EXECUTE FUNCTION esg.reject_snapshot_mutation();
CREATE FUNCTION esg.protect_derivation_request() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR NEW.request_id<>OLD.request_id OR NEW.idempotency_key<>OLD.idempotency_key
       OR NEW.payload_digest<>OLD.payload_digest OR NEW.payload<>OLD.payload
       OR NEW.calculation_digest<>OLD.calculation_digest
       OR NEW.input_dataset_version<>OLD.input_dataset_version OR NEW.release_digest<>OLD.release_digest
       OR NEW.extension_id<>OLD.extension_id OR NEW.definition_version<>OLD.definition_version
       OR NEW.rule_id<>OLD.rule_id OR NEW.rule_digest<>OLD.rule_digest
       OR NEW.target_metric_id<>OLD.target_metric_id OR NEW.target_definition_version<>OLD.target_definition_version
       OR NEW.requested_by<>OLD.requested_by OR NEW.created_at<>OLD.created_at THEN
        RAISE EXCEPTION 'immutable derivation request identity';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_derivation_request_identity BEFORE UPDATE OR DELETE ON esg.extension_derivation_requests FOR EACH ROW EXECUTE FUNCTION esg.protect_derivation_request();
ALTER TABLE esg.outbox_events DROP CONSTRAINT outbox_events_event_type_check;
ALTER TABLE esg.outbox_events ADD CONSTRAINT outbox_events_event_type_check
    CHECK (event_type IN ('review','context','dictionary','normalization','extension_definition','extension_derivation','extension_review'));
