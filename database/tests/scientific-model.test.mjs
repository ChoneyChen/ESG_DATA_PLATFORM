import test from 'node:test';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../runtime/frozen_schema.mjs';
import {applyPlatformMigrations} from '../runtime/connection.mjs';
import {importBundle} from '../scripts/frozen-bundle.mjs';
import {projectClauseVersions, projectObservations, reconcileScientificModel, clauseVersionFromPackage}
  from '../scripts/scientific-model.mjs';

const metadata = {upstream_system: 'science-test', organization_ref: 'science-entity', report_ref: 'science-report',
  report_version_ref: 'science-report-v1', source_artifact_ref: 'science-pdf',
  report_period_start: '2025-01-01', report_period_end: '2025-12-31'};

const CLAUSE = {clause_id: 'esrs.test@1.0.0:E1-6_07', package_id: 'esrs.test', package_version: '1.0.0',
  package_source_digest: '5'.repeat(64),
  source_datapoint_id: 'E1-6_07', metric_id: 'metric-1', contract_digest: '3'.repeat(64),
  labels: {zh: '范围一排放'}, value_type: 'numeric', canonical_unit: 'tCO2e',
  output_scopes: [{scope_id: 'scope-a', label_zh: '年度 · 集团合并', period: 'annual', entity: 'group',
    value_role: 'actual', aggregation: 'total', axes: {}, required: true}],
  evidence: [{field: 'value', level: 'cell', required: true}]};

function bundle() {
  return {root: '/science/bundle', manifest: {job_id: 'science-job'}, bundleDigest: '1'.repeat(64),
    request: {package_id: 'esrs.test', package_version: '1.0.0'},
    standard: {manifest: {standard: {framework_id: 'esrs', framework_version: '2023'}}, compilation: {source_digest: '2'.repeat(64)},
      metrics: [{metric_id: 'metric-1', source_datapoint_id: 'E1-6_07', labels: {zh: '范围一'}}], elements: []},
    sourceMappings: {core_bindings: [{core_record_id: 'obs-1', source_fact_id: 'source-1'}]},
    sourceFacts: {facts: [{source_fact_id: 'source-1', source_key: 'reading-1', document_id: 'doc-1', ir_run_id: 'ir-1', ir_revision: 1,
      source_object_id: 'cell-1', source_row_id: 'row-1', source_row_label: '范围一', kind: 'quantitative', value_raw: '10', unit_raw: 'tCO2e',
      identity_raw: {reporting_period: '2025', reporting_entity: '示例集团'},
      identity_evidence: {reporting_period: [{ir_object_id: 'cell-1', pdf_page_index: 0, evidence_role: 'column_header', raw_text: '2025'}]},
      physical_sources: [{ir_object_id: 'cell-1', pdf_page_index: 0, printed_page_label: '1', bbox: {x0: 1, y0: 1, x1: 2, y1: 2}}],
      interpretation_version: 'test-v1', review_status: 'candidate'}]},
    records: {quantitative_observations: [{observation_id: 'obs-1', metric_id: 'metric-1', value_raw: '10', value_numeric: '10',
      comparator: 'exact', unit_raw: 'tCO2e', review_status: 'pending'}],
      qualitative_assertions: [], reporting_tasks: [], attribute_values: [], dimension_values: [],
      evidence_references: [{evidence_id: 'e-1', source_record_id: 'obs-1', document_id: 'doc-1', ir_run_id: 'ir-1', ir_revision: 1,
        ir_object_id: 'cell-1', pdf_page_index: 0, evidence_excerpt: '10'}]}};
}

async function seedObservation(db) {
  await importBundle(db, bundle(), metadata);
  const mapping = (await db.query(`SELECT m.disclosure_standard_mapping_id AS mapping_id, d.disclosure_id
    FROM esg.disclosure_standard_mappings m JOIN esg.esg_disclosures d USING(disclosure_id) LIMIT 1`)).rows[0];
  const report = (await db.query('SELECT source_report_id, report_ref FROM esg.source_report_refs LIMIT 1')).rows[0];
  assert.ok(mapping && report, 'import must produce a disclosure mapping and a report identity');
  await db.query(`INSERT INTO esg.metric_definition_versions(metric_id,definition_version,dictionary_version,
      source_datapoint_id,readiness,definition,definition_digest)
    VALUES('metric-1','1.0.0','test-dict','E1-6_07','ready','{}'::jsonb,$1) ON CONFLICT DO NOTHING`, ['c'.repeat(64)]);
  const definition = (await db.query(`SELECT metric_id, definition_version FROM esg.metric_definition_versions
    WHERE metric_id='metric-1' LIMIT 1`)).rows[0];
  assert.ok(definition, 'the fixture must register the metric definition');
  await db.query(`INSERT INTO esg.measurement_contexts(context_id,mapping_id,revision,entity_ref,period_start,period_end,
      boundary_code,value_role,dimensions,reported_unit,operator_ref,reason)
    VALUES('11111111-1111-5111-a111-111111111111',$1,1,'science-entity','2025-01-01','2025-12-31','group','actual',
      '{"aggregation_role":"total"}','tCO2e','tester','fixture')`, [mapping.mapping_id]);
  await db.query(`INSERT INTO esg.curated_observations(observation_id,metric_id,definition_version,identity_key,entity_ref,
      period_start,period_end,boundary_code,value_role,dimensions,canonical_unit,numeric_value,comparison_eligibility,
      comparison_reasons,normalization_rule_version)
    VALUES('22222222-2222-5222-a222-222222222222','metric-1',$1,$2,'science-entity','2025-01-01','2025-12-31','group',
      'actual','{"aggregation_role":"total"}','tCO2e',10,'comparable','[]','fixture-rules')`,
    [definition.definition_version, 'b'.repeat(64)]);
  await db.query(`INSERT INTO esg.observation_sources(observation_id,context_id,disclosure_id,mapping_id,source_snapshot)
    VALUES('22222222-2222-5222-a222-222222222222','11111111-1111-5111-a111-111111111111',$1,$2,$3)`,
    [mapping.disclosure_id, mapping.mapping_id, JSON.stringify({raw_value_text: '10', raw_unit: 'tCO2e',
      source_fact_ref: 'source-fact-1', report_title_snapshot: 'science-report',
      identity_evidence: {reporting_period: [{ir_object_id: 'cell-1', pdf_page_index: 0, raw_text: '2025'}]}})]);
  await db.query(`INSERT INTO esg.report_publications(source_report_id,status,publication_version,published_at)
    VALUES($1,'published',1,CURRENT_TIMESTAMP)`, [report.source_report_id]);
  await db.query(`INSERT INTO esg.report_publication_members(source_report_id,publication_version,observation_id)
    VALUES($1,1,'22222222-2222-5222-a222-222222222222')`, [report.source_report_id]);
  return report;
}

test('scientific model projects clauses and observations, then reconciles with legacy tables', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    await applyPlatformMigrations(db);
    await projectClauseVersions(db, [CLAUSE]);
    const report = await seedObservation(db);
    const projection = await projectObservations(db);
    assert.equal(projection.projected, 1);
    assert.deepEqual(projection.unmapped_metrics, []);
    const observation = (await db.query(`SELECT * FROM esg.scientific_observation_model
      WHERE observation_id='22222222-2222-5222-a222-222222222222'`)).rows[0];
    assert.equal(observation.report_ref, report.report_ref);
    assert.equal(observation.clause_version_id, CLAUSE.clause_id);
    assert.equal(observation.publication_state, 'published');
    assert.equal(observation.publication_version, 1);
    assert.equal(observation.aggregation_role, 'total');
    const dimensions = (await db.query('SELECT * FROM esg.observation_dimensions')).rows;
    assert.deepEqual(dimensions.map(row => [row.axis, row.code]), [['aggregation_role', 'total']]);
    const evidence = (await db.query('SELECT * FROM esg.observation_evidence')).rows;
    assert.equal(evidence.length, 1);
    assert.equal(evidence[0].field, 'reporting_period');
    assert.equal(evidence[0].page_index, 0);
    const sources = (await db.query('SELECT * FROM esg.observation_model_sources')).rows;
    assert.equal(sources[0].raw_value, '10');
    const reconciliation = await reconcileScientificModel(db);
    assert.equal(reconciliation.ok, true, JSON.stringify(reconciliation));
  } finally { await db.close(); }
});

test('reconciliation reports a value that differs from the legacy observation', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    await applyPlatformMigrations(db);
    await projectClauseVersions(db, [CLAUSE]);
    await seedObservation(db);
    await projectObservations(db);
    // Legacy observations are immutable; the projection is what may drift.
    await db.query(`UPDATE esg.scientific_observation_model SET value_numeric=11
      WHERE observation_id='22222222-2222-5222-a222-222222222222'`);
    const reconciliation = await reconcileScientificModel(db);
    assert.equal(reconciliation.ok, false);
    assert.deepEqual(reconciliation.value_mismatches, ['22222222-2222-5222-a222-222222222222']);
  } finally { await db.close(); }
});

test('a clause version may not change content while its package is unchanged', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    await applyPlatformMigrations(db);
    await projectClauseVersions(db, [CLAUSE]);
    // Same package, recompiled projection: allowed and updated in place.
    await projectClauseVersions(db, [{...CLAUSE, contract_digest: '4'.repeat(64)}]);
    const after = (await db.query(`SELECT contract_digest FROM esg.clause_versions
      WHERE clause_version_id='esrs.test@1.0.0:E1-6_07'`)).rows[0];
    assert.equal(after.contract_digest, '4'.repeat(64));
    // A different package under the same clause id is a different clause.
    await assert.rejects(() => projectClauseVersions(db, [{...CLAUSE, contract_digest: '6'.repeat(64),
      package_source_digest: '7'.repeat(64)}]), /content differs from its digest/);
  } finally { await db.close(); }
});

test('an observation whose metric has no clause version is reported, not invented', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    await applyPlatformMigrations(db);
    await seedObservation(db);
    const projection = await projectObservations(db);
    assert.equal(projection.projected, 0);
    assert.deepEqual(projection.unmapped_metrics, ['metric-1']);
  } finally { await db.close(); }
});

test('clause projection rejects an incomplete clause', () => {
  assert.throws(() => clauseVersionFromPackage({...CLAUSE, canonical_unit: null}), /canonical unit/);
  assert.throws(() => clauseVersionFromPackage({...CLAUSE, value_type: 'unknown'}), /invalid value type/);
});
