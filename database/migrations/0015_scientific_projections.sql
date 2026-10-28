-- Read-only scientific projections; immutable facts remain in their source tables.
CREATE VIEW esg_api.scientific_observations AS
SELECT o.observation_id,o.metric_id,o.definition_version,o.entity_ref,o.period_start,o.period_end,
  o.boundary_code,o.value_role,o.measurement_method,o.dimensions,o.canonical_unit,
  o.numeric_value,o.lower_bound,o.upper_bound,o.precision_qualifier,o.statement,
  o.comparison_eligibility,o.comparison_reasons,o.value_origin,o.created_at
FROM esg.curated_observations o;

CREATE VIEW esg_api.scientific_observation_dimensions AS
SELECT observation_id, key AS dimension_key, value #>> '{}' AS dimension_value
FROM esg.curated_observations o, LATERAL jsonb_each(o.dimensions);

CREATE VIEW esg_api.scientific_observation_evidence AS
SELECT os.observation_id,os.context_id,os.disclosure_id,os.mapping_id,
  evidence.key AS field_key, item.value AS evidence
FROM esg.observation_sources os,
  LATERAL jsonb_each(COALESCE(os.source_snapshot->'identity_evidence','{}'::jsonb)) evidence,
  LATERAL jsonb_array_elements(evidence.value) item;

CREATE VIEW esg_api.scientific_observation_sources AS
SELECT os.observation_id,os.context_id,os.disclosure_id,os.mapping_id,
  os.source_snapshot->>'raw_value_text' AS raw_value_text,
  os.source_snapshot->>'raw_unit' AS raw_unit,
  os.source_snapshot->>'source_fact_ref' AS source_fact_ref,
  os.source_snapshot->>'source_artifact_ref' AS source_artifact_ref,
  os.source_snapshot->>'source_sha256' AS source_sha256,
  os.source_snapshot->>'report_title_snapshot' AS report_title,
  os.source_snapshot->'evidence' AS physical_evidence
FROM esg.observation_sources os;

CREATE VIEW esg_api.scientific_metric_definitions AS
SELECT metric_id,definition_version,dictionary_version,source_datapoint_id,
  clause_kind,extension_id,readiness,definition,definition_digest
FROM esg.metric_definition_versions;

CREATE VIEW esg_api.scientific_report_publication_members AS
SELECT source_report_id,publication_version,observation_id
FROM esg.report_publication_members;

CREATE VIEW esg_api.scientific_coverage_decisions AS
SELECT s.snapshot_id,s.source_fact_ref,s.source_ir_run_id,s.source_ir_revision,
  r.report_ref,s.identity_raw,
  CASE WHEN COALESCE(jsonb_array_length(link.core_bindings),0)=0 THEN 'unmapped' ELSE 'covered' END AS coverage_state,
  COALESCE(link.core_bindings,'[]'::jsonb) AS core_bindings
FROM esg.report_source_fact_snapshots s
JOIN esg.source_report_refs r USING(source_report_id)
LEFT JOIN esg.import_source_facts link ON link.snapshot_id=s.snapshot_id
WHERE s.fact_role='neutral';
