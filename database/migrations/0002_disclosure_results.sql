CREATE TABLE esg.esg_disclosures (
    disclosure_id uuid PRIMARY KEY,
    source_report_id uuid NOT NULL
        REFERENCES esg.source_report_refs(source_report_id),
    concept_id uuid REFERENCES esg.canonical_concepts(concept_id),
    disclosure_type text NOT NULL,
    raw_indicator_name text,
    raw_disclosure_text text NOT NULL,
    period_type text NOT NULL DEFAULT 'duration',
    period_start date,
    period_end date,
    value_role text NOT NULL DEFAULT 'actual',
    data_nature text NOT NULL DEFAULT 'reported',
    mapping_status text NOT NULL DEFAULT 'unmapped',
    quality_status text NOT NULL DEFAULT 'candidate',
    extraction_version text NOT NULL,
    source_ir_run_id text NOT NULL,
    source_ir_revision integer NOT NULL,
    supersedes_disclosure_id uuid
        REFERENCES esg.esg_disclosures(disclosure_id),
    record_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at timestamptz,
    CONSTRAINT esg_disclosures_type_ck
        CHECK (disclosure_type IN ('quantitative', 'categorical', 'qualitative')),
    CONSTRAINT esg_disclosures_period_type_ck
        CHECK (period_type IN ('duration', 'instant', 'target_period', 'unknown')),
    CONSTRAINT esg_disclosures_period_ck
        CHECK (
            period_start IS NULL
            OR period_end IS NULL
            OR period_end >= period_start
        ),
    CONSTRAINT esg_disclosures_value_role_ck
        CHECK (value_role IN ('actual', 'target', 'baseline', 'restated')),
    CONSTRAINT esg_disclosures_data_nature_ck
        CHECK (data_nature IN ('reported', 'estimated', 'calculated')),
    CONSTRAINT esg_disclosures_mapping_status_ck
        CHECK (mapping_status IN ('unmapped', 'mapped', 'ambiguous', 'not_esg')),
    CONSTRAINT esg_disclosures_mapping_concept_ck
        CHECK (mapping_status <> 'mapped' OR concept_id IS NOT NULL),
    CONSTRAINT esg_disclosures_quality_status_ck
        CHECK (
            quality_status IN (
                'candidate',
                'review_required',
                'approved',
                'rejected',
                'superseded'
            )
        ),
    CONSTRAINT esg_disclosures_ir_revision_ck
        CHECK (source_ir_revision > 0),
    CONSTRAINT esg_disclosures_record_version_ck
        CHECK (record_version > 0),
    CONSTRAINT esg_disclosures_not_self_superseding_ck
        CHECK (
            supersedes_disclosure_id IS NULL
            OR supersedes_disclosure_id <> disclosure_id
        )
);

COMMENT ON TABLE esg.esg_disclosures IS
    'One row per scope-specific disclosure fact extracted from a report.';

CREATE TABLE esg.structured_values (
    disclosure_id uuid PRIMARY KEY
        REFERENCES esg.esg_disclosures(disclosure_id) ON DELETE CASCADE,
    value_type text NOT NULL,
    raw_value_text text NOT NULL,
    numeric_value numeric,
    boolean_value boolean,
    enum_code text,
    text_value text,
    lower_bound numeric,
    upper_bound numeric,
    raw_unit text,
    unit_id uuid REFERENCES esg.unit_definitions(unit_id),
    normalized_numeric_value numeric,
    normalized_unit_id uuid REFERENCES esg.unit_definitions(unit_id),
    scale_factor numeric,
    precision_qualifier text,
    normalization_status text NOT NULL DEFAULT 'raw_only',
    normalization_rule_version text,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT structured_values_type_ck
        CHECK (value_type IN ('numeric', 'boolean', 'enum', 'text')),
    CONSTRAINT structured_values_typed_value_ck
        CHECK (
            (
                value_type = 'numeric'
                AND (numeric_value IS NOT NULL OR lower_bound IS NOT NULL OR upper_bound IS NOT NULL)
                AND boolean_value IS NULL
                AND enum_code IS NULL
                AND text_value IS NULL
            )
            OR (
                value_type = 'boolean'
                AND boolean_value IS NOT NULL
                AND numeric_value IS NULL
                AND enum_code IS NULL
                AND text_value IS NULL
            )
            OR (
                value_type = 'enum'
                AND enum_code IS NOT NULL
                AND numeric_value IS NULL
                AND boolean_value IS NULL
                AND text_value IS NULL
            )
            OR (
                value_type = 'text'
                AND text_value IS NOT NULL
                AND numeric_value IS NULL
                AND boolean_value IS NULL
                AND enum_code IS NULL
            )
        ),
    CONSTRAINT structured_values_bounds_ck
        CHECK (
            lower_bound IS NULL
            OR upper_bound IS NULL
            OR upper_bound >= lower_bound
        ),
    CONSTRAINT structured_values_scale_ck
        CHECK (scale_factor IS NULL OR scale_factor > 0),
    CONSTRAINT structured_values_normalization_ck
        CHECK (
            normalization_status IN (
                'raw_only',
                'normalized',
                'not_applicable',
                'failed'
            )
        )
);

CREATE TABLE esg.disclosure_dimensions (
    disclosure_dimension_id uuid PRIMARY KEY,
    disclosure_id uuid NOT NULL
        REFERENCES esg.esg_disclosures(disclosure_id) ON DELETE CASCADE,
    dimension_key text NOT NULL,
    ordinal integer NOT NULL DEFAULT 1,
    raw_value text NOT NULL,
    canonical_value text,
    normalization_status text NOT NULL DEFAULT 'raw_only',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT disclosure_dimensions_ordinal_ck
        CHECK (ordinal > 0),
    CONSTRAINT disclosure_dimensions_status_ck
        CHECK (
            normalization_status IN (
                'raw_only',
                'normalized',
                'ambiguous',
                'rejected'
            )
        ),
    CONSTRAINT disclosure_dimensions_uk
        UNIQUE (disclosure_id, dimension_key, ordinal)
);

CREATE TABLE esg.qualitative_claims (
    disclosure_id uuid PRIMARY KEY
        REFERENCES esg.esg_disclosures(disclosure_id) ON DELETE CASCADE,
    claim_type text NOT NULL,
    subject_text text NOT NULL,
    predicate_text text NOT NULL,
    object_text text NOT NULL,
    claim_status text NOT NULL DEFAULT 'stated',
    effective_date date,
    target_date date,
    frequency_text text,
    qualifiers jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary_text text,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT qualitative_claims_type_ck
        CHECK (
            claim_type IN (
                'policy',
                'governance',
                'target',
                'risk_opportunity',
                'process',
                'methodology',
                'assurance',
                'incident_compliance',
                'other'
            )
        ),
    CONSTRAINT qualitative_claims_status_ck
        CHECK (
            claim_status IN (
                'stated',
                'implemented',
                'planned',
                'partial',
                'not_disclosed',
                'unknown'
            )
        )
);

CREATE TABLE esg.disclosure_sources (
    disclosure_source_id uuid PRIMARY KEY,
    disclosure_id uuid NOT NULL
        REFERENCES esg.esg_disclosures(disclosure_id) ON DELETE CASCADE,
    source_role text NOT NULL DEFAULT 'primary',
    page_number integer NOT NULL,
    printed_page_label text,
    source_node_id text,
    source_quote text NOT NULL,
    bbox jsonb,
    ordinal integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT disclosure_sources_role_ck
        CHECK (source_role IN ('primary', 'context', 'unit', 'footnote', 'verification')),
    CONSTRAINT disclosure_sources_page_ck
        CHECK (page_number > 0),
    CONSTRAINT disclosure_sources_ordinal_ck
        CHECK (ordinal > 0),
    CONSTRAINT disclosure_sources_uk
        UNIQUE (disclosure_id, source_role, ordinal)
);

CREATE TABLE esg.disclosure_standard_mappings (
    disclosure_standard_mapping_id uuid PRIMARY KEY,
    disclosure_id uuid NOT NULL
        REFERENCES esg.esg_disclosures(disclosure_id) ON DELETE CASCADE,
    requirement_id uuid NOT NULL
        REFERENCES esg.standard_requirements(requirement_id),
    mapping_relation text NOT NULL,
    satisfaction_status text NOT NULL,
    mapping_reason text NOT NULL,
    review_status text NOT NULL DEFAULT 'review_required',
    mapper_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT disclosure_standard_mappings_relation_ck
        CHECK (
            mapping_relation IN (
                'exact',
                'broader',
                'narrower',
                'partial',
                'conditional',
                'alternative_measure',
                'not_equivalent'
            )
        ),
    CONSTRAINT disclosure_standard_mappings_satisfaction_ck
        CHECK (
            satisfaction_status IN (
                'supports',
                'partially_supports',
                'not_sufficient',
                'not_applicable',
                'unknown'
            )
        ),
    CONSTRAINT disclosure_standard_mappings_review_ck
        CHECK (review_status IN ('approved', 'review_required', 'rejected')),
    CONSTRAINT disclosure_standard_mappings_uk
        UNIQUE (disclosure_id, requirement_id, mapper_version)
);

CREATE INDEX esg_disclosures_report_idx
    ON esg.esg_disclosures (source_report_id);

CREATE INDEX esg_disclosures_concept_period_idx
    ON esg.esg_disclosures (concept_id, period_end);

CREATE INDEX esg_disclosures_quality_idx
    ON esg.esg_disclosures (quality_status, disclosure_type);

CREATE INDEX esg_disclosures_ir_revision_idx
    ON esg.esg_disclosures (source_ir_run_id, source_ir_revision);

CREATE INDEX structured_values_normalized_idx
    ON esg.structured_values (normalized_unit_id, normalized_numeric_value)
    WHERE value_type = 'numeric';

CREATE INDEX disclosure_dimensions_lookup_idx
    ON esg.disclosure_dimensions (dimension_key, canonical_value, raw_value);

CREATE INDEX disclosure_sources_page_idx
    ON esg.disclosure_sources (disclosure_id, page_number);

CREATE INDEX disclosure_standard_mappings_requirement_idx
    ON esg.disclosure_standard_mappings (requirement_id, review_status);

