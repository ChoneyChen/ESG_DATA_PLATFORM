import test from 'node:test';
import assert from 'node:assert/strict';
import { PGlite } from '@electric-sql/pglite';
import { applyMigrations } from '../scripts/db.mjs';
import { loadBundle, importBundle } from '../scripts/frozen-bundle.mjs';

// The integration run supplies an actual ResultExporter output created with
// anonymous temporary Core/source checkpoints. No hand-written ideal Bundle.
test('exported interrupted Core imports as candidates while unmapped sources stay outside Core',
  { skip: !process.env.ESG_TEST_PARTIAL_BUNDLE }, async () => {
    const bundle = await loadBundle(process.env.ESG_TEST_PARTIAL_BUNDLE);
    assert.equal(bundle.manifest.execution_status, 'interrupted');
    assert.equal(bundle.manifest.contract_validation.status, 'passed');
    assert.equal(bundle.manifest.result_status, 'partial');
    assert.equal(bundle.manifest.counts.unfinished_metrics, 1);
    assert.equal(bundle.manifest.counts.not_executed_metrics, 1);
    assert.equal(bundle.records.quantitative_observations.length, 1);
    assert.equal(bundle.sourceFacts.facts.length, 2);
    const db = await PGlite.create();
    try {
      await applyMigrations(db);
      const result = await importBundle(db, bundle, { upstream_system: 'offline-partial-contract',
        organization_ref: 'anonymous-organization', report_ref: 'anonymous-report',
        report_version_ref: 'anonymous-report-v1', source_artifact_ref: 'anonymous-pdf' });
      assert.equal(result.imported, 1);
      const stored = (await db.query('SELECT quality_status,source_identity FROM esg.esg_disclosures')).rows;
      assert.equal(stored.length, 1);
      assert.equal(stored[0].quality_status, 'candidate');
      assert.equal((await db.query('SELECT count(*)::int AS n FROM esg_api.v_quantitative_observations')).rows[0].n, 0);
      assert.equal((await importBundle(db, bundle, { upstream_system: 'offline-partial-contract',
        organization_ref: 'anonymous-organization', report_ref: 'anonymous-report',
        report_version_ref: 'anonymous-report-v1', source_artifact_ref: 'anonymous-pdf' })).idempotent, true);
    } finally { await db.close(); }
  });
