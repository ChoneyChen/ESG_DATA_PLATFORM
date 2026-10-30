import test from 'node:test';
import assert from 'node:assert/strict';
import { PGlite } from '@electric-sql/pglite';
import { applyMigrations } from '../runtime/frozen_schema.mjs';
import {importBundle} from '../scripts/frozen-bundle.mjs';
import {reviewRecord} from '../scripts/review-decisions.mjs';

const metadata = { upstream_system: 'test', organization_ref: 'org-explicit', report_ref: 'report-1',
  report_version_ref: 'report-v1', source_artifact_ref: 'pdf-explicit' };
function bundle(overrides = {}) {
  const result = { root: '/fixture/bundle', manifest: { job_id: 'fixture-job' }, bundleDigest: '1'.repeat(64),
    request: { package_id: 'esrs.test', package_version: '2.0.0' },
    standard: { manifest: { standard: { framework_id: 'esrs', framework_version: '2023' } },
      compilation: { source_digest: '2'.repeat(64) },
      metrics: [{ metric_id: 'metric-1', source_datapoint_id: 'E1-6_07', labels: { zh: '排放' }, description: 'emissions' }], elements: [] },
    sourceMappings: { core_bindings: [{ core_record_id: 'obs-1', source_fact_id: 'source-1' }] },
    sourceFacts: { facts: [{ source_fact_id: 'source-1', source_key: 'reading-1', document_id: 'doc-1', ir_run_id: 'ir-1', ir_revision: 1,
      source_object_id: 'cell-1', source_row_id: 'row-1', source_row_label: '报告原文排放主题', kind: 'quantitative', value_raw: '10', unit_raw: 'tCO2e',
      identity_raw: { reporting_period: '2024年度', reporting_entity: '示例集团', entity_aggregation_role: '报告主体', scope: 'Scope 1', method: '报告原文方法', category: '原文类别' },
      physical_sources: [{ ir_object_id: 'cell-1', pdf_page_index: 0, printed_page_label: 'i', bbox: { x0: 10, y0: 20, x1: 30, y1: 40 } }],
      interpretation_version: 'fixture-v1', review_status: 'candidate' }] },
    records: { quantitative_observations: [{ observation_id: 'obs-1', metric_id: 'metric-1', value_raw: '10', value_numeric: '10', comparator: 'exact', unit_raw: 'tCO2e' }],
      qualitative_assertions: [], reporting_tasks: [], attribute_values: [], dimension_values: [],
      evidence_references: [{ evidence_id: 'evidence-1', source_record_id: 'obs-1', document_id: 'doc-1', ir_run_id: 'ir-1', ir_revision: 1,
        ir_object_id: 'cell-1', pdf_page_index: 0, evidence_excerpt: 'Scope 1: 10 tCO2e' }] } };
  return { ...result, ...overrides };
}

test('candidate import is atomic, idempotent, source-based and separately reviewed', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    const first = bundle();
    assert.equal((await importBundle(db, first, metadata)).imported, 1);
    assert.equal((await importBundle(db, first, metadata)).idempotent, true);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg_api.v_quantitative_observations')).rows[0].n, 0);
    const imported = (await db.query('SELECT disclosure_id,quality_status FROM esg.esg_disclosures')).rows[0];
    assert.equal(imported.quality_status, 'candidate');
    assert.equal((await db.query('SELECT page_number FROM esg.disclosure_sources')).rows[0].page_number, 1);

    const second = structuredClone(first);
    second.manifest.job_id = 'second-package-job';
    second.bundleDigest = '3'.repeat(64);
    second.request.package_id = 'esrs.second';
    second.standard.metrics[0].metric_id = 'metric-2';
    second.standard.metrics[0].source_datapoint_id = 'E1-5_01';
    second.records.quantitative_observations[0].metric_id = 'metric-2';
    assert.equal((await importBundle(db, second, metadata)).imported, 0);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.esg_disclosures')).rows[0].n, 1);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.disclosure_standard_mappings')).rows[0].n, 2);

    const collision = structuredClone(second);
    collision.bundleDigest = '4'.repeat(64);
    collision.sourceFacts.facts[0].value_raw = '11';
    await assert.rejects(importBundle(db, collision, metadata), /collision/);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.import_batches')).rows[0].n, 2);
    await assert.rejects(reviewRecord(db, { disclosure_id: imported.disclosure_id, decision: 'approved', operator_ref: '', reason: 'checked' }));
    await reviewRecord(db, { disclosure_id: imported.disclosure_id, decision: 'approved', operator_ref: 'reviewer-1', reason: 'Checked physical source and identity' });
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg_api.v_quantitative_observations')).rows[0].n, 1);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg_api.v_standard_disclosures')).rows[0].n, 0);
    const mapping = (await db.query('SELECT disclosure_standard_mapping_id AS id FROM esg.disclosure_standard_mappings LIMIT 1')).rows[0];
    await reviewRecord(db, { mapping_id: mapping.id, decision: 'approved', operator_ref: 'reviewer-2', reason: 'Checked applicability to requirement' });
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg_api.v_standard_disclosures')).rows[0].n, 1);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.review_decisions')).rows[0].n, 2);
  } finally { await db.close(); }
});

test('an outer scope-snapshot failure rolls back the candidate import transaction', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    await assert.rejects(db.transaction(async tx => {
      await importBundle(tx, bundle(), metadata, { inTransaction: true });
      throw new Error('injected immutable scope snapshot failure');
    }), /scope snapshot failure/);
    for (const table of ['import_batches','esg_disclosures','source_report_refs']) {
      assert.equal((await db.query(`SELECT count(*)::int AS n FROM esg.${table}`)).rows[0].n, 0);
    }
    assert.equal((await importBundle(db, bundle(), metadata)).imported, 1);
  } finally { await db.close(); }
});

test('qualitative statements remain readable and ranges have no fabricated point', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    const input = bundle();
    input.records.quantitative_observations[0] = { ...input.records.quantitative_observations[0], comparator: 'range', value_raw: '10–20', value_numeric: '10', value_lower: '10', value_upper: '20' };
    input.sourceFacts.facts[0].value_raw = '10–20';
    input.records.qualitative_assertions = [{ assertion_id: 'assert-1', metric_id: 'metric-1', statement_raw: 'The report uses a location-based method.' }];
    input.records.evidence_references.push({ ...input.records.evidence_references[0], evidence_id: 'evidence-2', source_record_id: 'assert-1' });
    await importBundle(db, input, metadata);
    const value = (await db.query('SELECT numeric_value,lower_bound,upper_bound FROM esg.structured_values')).rows[0];
    assert.equal(value.numeric_value, null);
    assert.equal(Number(value.lower_bound), 10);
    assert.equal(Number(value.upper_bound), 20);
    const claim = (await db.query('SELECT statement_raw,subject_text,predicate_text,object_text FROM esg.qualitative_claims')).rows[0];
    assert.match(claim.statement_raw, /location-based/);
    assert.equal(claim.subject_text, null);
    assert.equal(claim.predicate_text, null);
    assert.equal(claim.object_text, null);
  } finally { await db.close(); }
});

test('neutral source identity is preserved while package projections stay on their mappings', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    const first = bundle();
    first.records.dimension_values.push({ dimension_value_id: 'dim-1', parent_record_id: 'obs-1', dimension_id: 'package-method', value_raw: '标准分类A' });
    await importBundle(db, first, metadata);
    const stored = (await db.query('SELECT raw_indicator_name,source_identity FROM esg.esg_disclosures')).rows[0];
    assert.equal(stored.raw_indicator_name, '报告原文排放主题');
    assert.equal(stored.source_identity.identity_raw.reporting_entity, '示例集团');
    assert.equal(stored.source_identity.identity_raw.entity_aggregation_role, '报告主体');
    assert.equal(stored.source_identity.identity_raw.method, '报告原文方法');
    assert.deepEqual(stored.source_identity.physical_sources, first.sourceFacts.facts[0].physical_sources);
    assert.equal('dimensions' in stored.source_identity, false);
    const dimensions = (await db.query('SELECT dimension_key,raw_value,canonical_value FROM esg.disclosure_dimensions')).rows;
    assert.equal(dimensions.find(row => row.dimension_key === 'scope').raw_value, 'Scope 1');
    assert.equal(dimensions.find(row => row.dimension_key === 'method').canonical_value, null);
    assert.equal(dimensions.some(row => row.dimension_key === 'package-method'), false);
    const second = structuredClone(first);
    second.manifest.job_id = 'second-package'; second.bundleDigest = '5'.repeat(64); second.request.package_id = 'esrs.second';
    second.records.dimension_values[0].value_raw = '标准分类B';
    second.sourceFacts.facts[0].review_status = 'needs_confirmation';
    assert.equal((await importBundle(db, second, metadata)).imported, 0);
    const reasons = (await db.query('SELECT mapping_reason FROM esg.disclosure_standard_mappings')).rows.map(row => JSON.parse(row.mapping_reason));
    assert.deepEqual(reasons.map(row => row.projection_dimensions[0].value_raw).sort(), ['标准分类A', '标准分类B']);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.esg_disclosures')).rows[0].n, 1);
  } finally { await db.close(); }
});

test('same source ID rejects identity, physical source and interpretation changes atomically', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    await importBundle(db, bundle(), metadata);
    for (const mutate of [
      input => { input.sourceFacts.facts[0].identity_raw.method = '另一方法'; },
      input => { input.sourceFacts.facts[0].identity_raw.reporting_entity = '另一实体'; },
      input => { input.sourceFacts.facts[0].physical_sources[0].bbox.x0 = 11; },
    ]) {
      const conflicting = bundle(); conflicting.manifest.job_id = 'conflicting-job'; conflicting.bundleDigest = '6'.repeat(64); mutate(conflicting);
      await assert.rejects(importBundle(db, conflicting, metadata), /Source fact collision/);
      assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.import_batches')).rows[0].n, 1);
      assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.disclosure_standard_mappings')).rows[0].n, 1);
    }
    const revised = bundle();
    revised.manifest.job_id = 'interpretation-revision';
    revised.bundleDigest = '7'.repeat(64);
    revised.sourceFacts.facts[0].interpretation_version = 'fixture-v2';
    assert.equal((await importBundle(db, revised, metadata)).imported, 0);
    const snapshots = await db.query('SELECT snapshot_id,source_fact_ref FROM esg.report_source_fact_snapshots');
    assert.equal(snapshots.rows.length, 2);
  } finally { await db.close(); }
});

test('bindings have one valid Core and source reference under the frozen package', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    for (const [mutate, expected] of [
      [input => input.sourceMappings.core_bindings.push({ core_record_id: 'obs-1', source_fact_id: 'other' }), /Duplicate source binding/],
      [input => { input.sourceMappings.core_bindings[0].core_record_id = 'absent'; }, /unknown Core record/],
      [input => { input.sourceMappings.core_bindings[0].source_fact_id = 'absent'; }, /unknown SourceFact/],
      [input => { input.sourceMappings.core_bindings[0].metric_id = 'absent'; }, /metric disagrees/],
      [input => { input.sourceMappings.core_bindings[0].package_version = '9.0.0'; }, /package_version disagrees/],
      [input => { input.sourceFacts.facts[0].document_id = 'another-report'; }, /another report/],
      [input => { input.sourceFacts.facts[0].ir_revision = 2; }, /Core evidence revision/],
    ]) {
      const invalid = bundle(); mutate(invalid);
      await assert.rejects(importBundle(db, invalid, metadata), expected);
      assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.import_batches')).rows[0].n, 0);
      assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.esg_disclosures')).rows[0].n, 0);
    }
  } finally { await db.close(); }
});

test('report hash is frozen, case independent, and can fill an initially unknown identity', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    const input = bundle();
    await importBundle(db, input, metadata);
    assert.equal((await importBundle(db, input, { ...metadata, source_sha256: 'A'.repeat(64) })).idempotent, true);
    assert.equal((await db.query('SELECT source_sha256 FROM esg.source_report_refs')).rows[0].source_sha256, 'a'.repeat(64));
    await assert.rejects(importBundle(db, input, { ...metadata, source_sha256: 'b'.repeat(64) }), /Report identity collision/);
    await assert.rejects(importBundle(db, input, { ...metadata, source_sha256: 'not-a-hash' }), /Invalid report source_sha256/);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.import_batches')).rows[0].n, 1);
  } finally { await db.close(); }
});

test('legacy bundles retain separate job identity rather than guessing a shared source', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db);
    const first = bundle(); delete first.sourceFacts;
    assert.equal((await importBundle(db, first, metadata)).imported, 1);
    assert.equal((await importBundle(db, first, metadata)).idempotent, true);
    const second = structuredClone(first); second.manifest.job_id = 'other-legacy-job'; second.bundleDigest = '7'.repeat(64);
    assert.equal((await importBundle(db, second, metadata)).imported, 1);
    const rows = (await db.query('SELECT source_fact_ref,source_identity FROM esg.esg_disclosures')).rows;
    assert.equal(rows.length, 2);
    assert.equal(new Set(rows.map(row => row.source_fact_ref)).size, 2);
    assert.equal(rows.every(row => row.source_identity.origin === 'legacy_projection'), true);
    assert.equal(rows.every(row => row.source_identity.unverified_source_binding.source_fact_id === 'source-1'), true);
  } finally { await db.close(); }
});
