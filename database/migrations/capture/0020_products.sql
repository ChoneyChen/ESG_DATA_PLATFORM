-- New products consume immutable captured exports and frozen single-clause definitions.
-- Installed only by explicit capture management; no old dictionary dependency.
CREATE TABLE esg.capture_projection_attempts (
    export_id text NOT NULL REFERENCES esg.capture_exports(export_id),
    transform_version text NOT NULL,
    status text NOT NULL CHECK(status IN ('completed','failed')),
    error_code text,
    retryable boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(export_id,transform_version)
);
CREATE TABLE esg.capture_projections (
    projection_id text PRIMARY KEY,
    export_id text NOT NULL REFERENCES esg.capture_exports(export_id),
    input_digest text NOT NULL,
    transform_version text NOT NULL,
    content_digest text NOT NULL,
    summary jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(export_id,transform_version)
);
CREATE TABLE esg.capture_product_definitions (
    clause_digest text PRIMARY KEY,
    clause_key text NOT NULL,
    title text NOT NULL,
    contract jsonb NOT NULL
);
CREATE TABLE esg.capture_observations (
    observation_id text PRIMARY KEY,
    source_fact_id text NOT NULL REFERENCES esg.captured_facts(source_fact_id),
    occurrence_id text NOT NULL,
    clause_key text,
    clause_digest text REFERENCES esg.capture_product_definitions(clause_digest),
    relation text NOT NULL CHECK(relation IN ('match','uncertain','different')),
    comparison_key text,
    record jsonb NOT NULL,
    CHECK((clause_key IS NULL)=(clause_digest IS NULL))
);
CREATE INDEX capture_observations_occurrence ON esg.capture_observations(occurrence_id);
CREATE TABLE esg.capture_projection_members (
    projection_id text NOT NULL REFERENCES esg.capture_projections(projection_id),
    observation_id text NOT NULL REFERENCES esg.capture_observations(observation_id),
    PRIMARY KEY(projection_id,observation_id)
);
CREATE TABLE esg.capture_restatements (
    link_id text PRIMARY KEY,
    newer_observation_id text NOT NULL REFERENCES esg.capture_observations(observation_id),
    older_observation_id text NOT NULL REFERENCES esg.capture_observations(observation_id),
    reason text NOT NULL,
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(newer_observation_id,older_observation_id),
    CHECK(newer_observation_id<>older_observation_id)
);
CREATE TABLE esg.capture_dataset_versions (
    dataset_version_id text PRIMARY KEY,
    title text NOT NULL,
    content_digest text NOT NULL,
    policy jsonb NOT NULL,
    snapshot jsonb NOT NULL,
    state text NOT NULL CHECK(state IN ('published','withdrawn')),
    revision integer NOT NULL DEFAULT 1,
    published_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE esg.capture_dataset_projections (
    dataset_version_id text NOT NULL REFERENCES esg.capture_dataset_versions(dataset_version_id),
    projection_id text NOT NULL REFERENCES esg.capture_projections(projection_id),
    PRIMARY KEY(dataset_version_id,projection_id)
);
CREATE TABLE esg.capture_dataset_members (
    dataset_version_id text NOT NULL REFERENCES esg.capture_dataset_versions(dataset_version_id),
    observation_id text NOT NULL REFERENCES esg.capture_observations(observation_id),
    reason text NOT NULL,
    PRIMARY KEY(dataset_version_id,observation_id)
);
CREATE TABLE esg.capture_dataset_events (
    event_id text PRIMARY KEY,
    dataset_version_id text NOT NULL REFERENCES esg.capture_dataset_versions(dataset_version_id),
    revision integer NOT NULL,
    kind text NOT NULL CHECK(kind IN ('published','withdrawn')),
    actor text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset_version_id,revision)
);
