CREATE SCHEMA IF NOT EXISTS esg;
CREATE SCHEMA IF NOT EXISTS esg_api;

CREATE TABLE esg.source_report_refs (
    source_report_id uuid PRIMARY KEY,
    upstream_system text NOT NULL,
    organization_ref text NOT NULL,
    report_ref text NOT NULL,
    report_version_ref text NOT NULL,
    source_artifact_ref text NOT NULL,
    source_sha256 char(64),
    report_title_snapshot text,
    report_period_start date,
    report_period_end date,
    publication_date date,
    language_code text NOT NULL DEFAULT 'und',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT source_report_refs_version_uk
        UNIQUE (upstream_system, report_version_ref),
    CONSTRAINT source_report_refs_period_ck
        CHECK (
            report_period_start IS NULL
            OR report_period_end IS NULL
            OR report_period_end >= report_period_start
        ),
    CONSTRAINT source_report_refs_sha256_ck
        CHECK (
            source_sha256 IS NULL
            OR source_sha256 ~ '^[0-9a-fA-F]{64}$'
        )
);

COMMENT ON TABLE esg.source_report_refs IS
    'Thin references to upstream report, organization, version and artifact IDs; not a report registry.';

CREATE TABLE esg.unit_definitions (
    unit_id uuid PRIMARY KEY,
    unit_code text NOT NULL UNIQUE,
    symbol text,
    unit_family text NOT NULL,
    canonical_unit_id uuid REFERENCES esg.unit_definitions(unit_id),
    multiplier_to_canonical numeric,
    offset_to_canonical numeric NOT NULL DEFAULT 0,
    definition text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT unit_definitions_status_ck
        CHECK (status IN ('proposed', 'active', 'deprecated')),
    CONSTRAINT unit_definitions_multiplier_ck
        CHECK (multiplier_to_canonical IS NULL OR multiplier_to_canonical > 0)
);

CREATE TABLE esg.canonical_concepts (
    concept_id uuid PRIMARY KEY,
    concept_code text NOT NULL UNIQUE,
    concept_name_zh text NOT NULL,
    concept_name_en text,
    category text NOT NULL,
    disclosure_kind text NOT NULL,
    quantity_kind text,
    default_unit_family text,
    aggregation_type text NOT NULL DEFAULT 'non_additive',
    definition text NOT NULL,
    status text NOT NULL DEFAULT 'proposed',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT canonical_concepts_category_ck
        CHECK (category IN ('E', 'S', 'G', 'CROSS')),
    CONSTRAINT canonical_concepts_kind_ck
        CHECK (disclosure_kind IN ('quantitative', 'categorical', 'qualitative', 'mixed')),
    CONSTRAINT canonical_concepts_aggregation_ck
        CHECK (aggregation_type IN ('additive', 'semi_additive', 'non_additive', 'not_applicable')),
    CONSTRAINT canonical_concepts_status_ck
        CHECK (status IN ('proposed', 'active', 'deprecated'))
);

CREATE TABLE esg.concept_synonyms (
    concept_synonym_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    concept_id uuid NOT NULL REFERENCES esg.canonical_concepts(concept_id) ON DELETE CASCADE,
    language_code text NOT NULL DEFAULT 'zh',
    synonym text NOT NULL,
    synonym_type text NOT NULL DEFAULT 'alias',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT concept_synonyms_type_ck
        CHECK (synonym_type IN ('alias', 'abbreviation', 'legacy', 'report_phrase')),
    CONSTRAINT concept_synonyms_uk
        UNIQUE (concept_id, language_code, synonym)
);

CREATE TABLE esg.standard_frameworks (
    framework_id uuid PRIMARY KEY,
    framework_code text NOT NULL UNIQUE,
    framework_name text NOT NULL,
    issuing_body text,
    jurisdiction text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT standard_frameworks_status_ck
        CHECK (status IN ('proposed', 'active', 'deprecated'))
);

CREATE TABLE esg.framework_versions (
    framework_version_id uuid PRIMARY KEY,
    framework_id uuid NOT NULL REFERENCES esg.standard_frameworks(framework_id),
    version_label text NOT NULL,
    effective_from date,
    effective_to date,
    source_uri text,
    source_hash text,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT framework_versions_uk
        UNIQUE (framework_id, version_label),
    CONSTRAINT framework_versions_period_ck
        CHECK (
            effective_from IS NULL
            OR effective_to IS NULL
            OR effective_to >= effective_from
        ),
    CONSTRAINT framework_versions_status_ck
        CHECK (status IN ('draft', 'active', 'superseded', 'withdrawn'))
);

CREATE TABLE esg.standard_requirements (
    requirement_id uuid PRIMARY KEY,
    framework_version_id uuid NOT NULL
        REFERENCES esg.framework_versions(framework_version_id),
    requirement_code text NOT NULL,
    requirement_title text NOT NULL,
    requirement_text text NOT NULL,
    parent_requirement_id uuid REFERENCES esg.standard_requirements(requirement_id),
    requirement_type text NOT NULL DEFAULT 'disclosure',
    expected_value_type text,
    measurement_conditions jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT standard_requirements_code_uk
        UNIQUE (framework_version_id, requirement_code),
    CONSTRAINT standard_requirements_type_ck
        CHECK (requirement_type IN ('section', 'disclosure', 'datapoint', 'guidance')),
    CONSTRAINT standard_requirements_value_type_ck
        CHECK (
            expected_value_type IS NULL
            OR expected_value_type IN ('numeric', 'boolean', 'enum', 'text', 'mixed')
        ),
    CONSTRAINT standard_requirements_status_ck
        CHECK (status IN ('active', 'superseded', 'withdrawn'))
);

CREATE INDEX source_report_refs_organization_idx
    ON esg.source_report_refs (organization_ref);

CREATE INDEX source_report_refs_report_idx
    ON esg.source_report_refs (report_ref);

CREATE INDEX concept_synonyms_synonym_idx
    ON esg.concept_synonyms (synonym);

CREATE INDEX standard_requirements_parent_idx
    ON esg.standard_requirements (parent_requirement_id);

