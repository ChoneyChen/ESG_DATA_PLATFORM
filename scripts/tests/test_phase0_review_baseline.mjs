import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

const scriptPath = fileURLToPath(new URL('../phase0_review_baseline.mjs', import.meta.url));
const { runReviewBaseline, measureHumanLedger, summarizeCandidates, discoverDocumentGroups } = await import(pathToFileURL(scriptPath).href);

function candidate(mappingId = 'mapping-1', approved = false) {
  return {
    mapping_id: mappingId, disclosure_id: `disclosure-${mappingId}`,
    source_datapoint_id: 'E1-6_07', raw_indicator_name: '合成范围一排放',
    raw_value_text: '10', raw_unit: '吨二氧化碳当量', period_start: '2025-01-01', period_end: '2025-12-31',
    fact_review_status: approved ? 'approved' : 'candidate', mapping_review_status: approved ? 'approved' : 'candidate',
    context: approved ? { boundary_code: 'group', entity_ref: 'phase0-synthetic-entity', period_start: '2025-01-01',
      period_end: '2025-12-31', value_role: 'actual', dimensions: {} } : null,
    source_identity: { document_id: 'doc-sha256-test', source_fact_ref: 'source-1', ir_run_id: 'ir-1',
      physical_sources: [{ ir_object_id: 'cell-1', pdf_page_index: 0 }] },
    source_ir_run_id: 'ir-1', source_ir_revision: 1, mapping_relation: 'exact',
    publication_status: 'blocked', observation_id: null,
  };
}

function proposal(mappingId = 'mapping-1', resolvable = true) {
  return { mapping_id: mappingId, resolvable, already_approved: false,
    blockers: resolvable ? [] : ['mapping_exact'], suggested: { entity_ref: 'phase0-synthetic-entity',
      period_start: '2025-01-01', period_end: '2025-12-31', boundary_code: 'group', dimensions: {} },
    boundary_source: 'report_identity_group' };
}

async function portableRoot(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'phase0-portable-bundles-'));
  const documentId = 'doc-sha256-' + 'a'.repeat(64);
  await mkdir(path.join(root, 'synthetic-bundle', 'input'), { recursive: true });
  await mkdir(path.join(root, 'nested', 'not-a-child'), { recursive: true });
  await writeFile(path.join(root, 'synthetic-bundle', 'manifest.json'), '{}\n');
  await writeFile(path.join(root, 'synthetic-bundle', 'input', 'ir-manifest.json'), JSON.stringify({ document_id: documentId }));
  await writeFile(path.join(root, 'nested', 'not-a-child', 'manifest.json'), '{}\n');
  await writeFile(path.join(root, 'nested', 'not-a-child', 'ir-manifest.json'), '{}');
  t.after(() => rm(root, { recursive: true, force: true }));
  return { root, documentId };
}

async function fixture(t, { candidates = [], proposals = [], automaticReview = null } = {}) {
  const output = await mkdtemp(path.join(tmpdir(), 'phase0-review-baseline-'));
  const { root: bundleRoot, documentId } = await portableRoot(t);
  t.after(() => rm(output, { recursive: true, force: true }));
  const bundle = { root: path.join(bundleRoot, 'synthetic-bundle'),
    manifest: { job_id: 'synthetic-job', configuration: { reporting_period: { year: 2025 } } },
    request: { selection_snapshot: { profile_id: 'synthetic-profile', source_datapoint_ids: ['E1-6_07'] } } };
  const db = { async query(sql) {
    if (sql.includes('FROM esg.review_decisions')) return { rows: [] };
    if (sql.includes('FROM esg.report_publications') || sql.includes('FROM esg.curated_observations')) return { rows: [{ n: 0 }] };
    throw new Error(`Unexpected SQL: ${sql}`);
  } };
  const calls = [];
  async function mockDispatch(database, method, input) {
    calls.push({ database, method, input });
    if (method === 'register_entity') return { entity_ref: input.entity_ref };
    if (method === 'candidates') return { items: candidates };
    if (method === 'candidate_proposal') return proposals.find(item => item.mapping_id === input.mapping_id)
      || proposal(input.mapping_id);
    if (method === 'deliver') return { status: 'imported', imported: candidates.length, publication_status: 'not_published',
      auto_review: { status: 'completed', considered: candidates.length, applied: 0, needs_human: candidates.length,
        duplicates_found: 0, duplicates_collapsed: 0, ...automaticReview } };
    throw new Error(`Unexpected dispatch: ${method}`);
  }
  return { output, documentId, db, mockDispatch, calls,
    run: (ledger = null) => runReviewBaseline({ bundleRoot, output, documentId, ledger,
      dependencies: { db, dispatch: mockDispatch, loadBundle: async () => structuredClone(bundle), setupComplete: true } }) };
}

test('empty replay has zero candidates and human editing remains not measured', async t => {
  const fixtureState = await fixture(t);
  const report = await fixtureState.run();
  assert.equal(report.baseline.considered_distinct_mappings, 0);
  assert.equal(report.baseline.initially_eligible, 0);
  assert.equal(report.baseline.automatic_approved_final, 0);
  assert.deepEqual(report.baseline.remaining_human_decisions_required,
    { mappings: 0, fact: 0, mapping: 0, context: 0 });
  assert.deepEqual(report.baseline.blockers, {});
  assert.equal(report.human_review.status, 'not_measured');
  assert.equal(report.human_review.counts.edited, 0);
  assert.equal(report.human_review.counts.approved, 0);
  assert.equal(report.baseline.human_decisions_recorded, 0);
  assert.equal(report.publication.attempted, false);
  assert.equal(fixtureState.calls.some(call => call.method.startsWith('publish')), false);
  const files = await readdir(fixtureState.output);
  assert.deepEqual(files.sort(), ['human-review.csv', 'review-baseline.json']);
  const csv = await readFile(path.join(fixtureState.output, 'human-review.csv'), 'utf8');
  assert.equal(csv.trim().split('\n').length, 1);
  assert.match(csv, /"mapping_id","code","raw_value","raw_unit"/);
  const saved = JSON.parse(await readFile(path.join(fixtureState.output, 'review-baseline.json'), 'utf8'));
  assert.equal(saved.human_review.status, 'not_measured');
  assert.equal(saved.current_candidates.length, 0);
});

test('baseline separates automatic final approval from three human decision states', () => {
  const approved = candidate('mapping-auto', true);
  const unresolved = candidate('mapping-human', false);
  const summary = summarizeCandidates([proposal('mapping-auto'), proposal('mapping-auto'), proposal('mapping-human', false)],
    [approved, unresolved], [proposal('mapping-auto'), proposal('mapping-human', false)],
    [{ duplicates_found: 1, duplicates_collapsed: 1 }], [
      { disclosure_id: 'disclosure-mapping-auto', decision: 'approved', operator_ref: 'auto-review:evidence-v1' },
      { mapping_id: 'mapping-auto', decision: 'approved', operator_ref: 'auto-review:evidence-v1' }]);
  assert.equal(summary.considered_distinct_mappings, 2);
  assert.equal(summary.initially_eligible, 1);
  assert.equal(summary.automatic_approved_final, 1);
  assert.deepEqual(summary.remaining_human_decisions_required, { mappings: 1, fact: 1, mapping: 1, context: 1 });
  assert.equal(summary.rows[0].final_status, 'auto_approved');
  assert.equal(summary.rows[1].final_status, 'human_decision_required');
  assert.ok(summary.rows[1].blockers.includes('fact_review_required'));
  assert.ok(summary.rows[1].blockers.includes('mapping_review_required'));
  assert.ok(summary.rows[1].blockers.includes('context_required'));
  assert.equal(summary.duplicate_collapse.collapsed, 0);
  assert.deepEqual(summary.approvals_by_operator.automatic, { total: 2, fact: 1, mapping: 1 });
  assert.deepEqual(summary.approvals_by_operator.human, { total: 0, fact: 0, mapping: 0 });
  assert.equal(summary.human_decisions_recorded, 0);
});

test('explicit ledger alone measures an edit; automatic approval is not human work', async () => {
  const mappingId = 'mapping-1';
  const ledger = { case_id: 'phase0-report', entries: [{ mapping_id: mappingId, action: 'edited',
    original: { boundary_code: 'group' }, final: { boundary_code: 'part_of_group' }, reason: '按附注收窄边界' }] };
  const human = await measureHumanLedger(ledger, 'phase0-report', [candidate()], [proposal()]);
  assert.equal(human.status, 'measured');
  assert.equal(human.counts.edited, 1);
  assert.equal(human.counts.material_edits, 1);
  assert.equal(human.entries[0].original_matches_initial_proposal, true);
  assert.equal(human.entries[0].final_matches_current_context, false);
  assert.deepEqual(human.entries[0].changed_fields, ['boundary_code']);
  assert.equal(human.entries[0].reason, '按附注收窄边界');
});

test('empty explicit ledger is measured zero, unlike a missing ledger', async () => {
  const measured = await measureHumanLedger({ case_id: 'phase0-report', entries: [] }, 'phase0-report', [], []);
  assert.equal(measured.status, 'measured');
  assert.deepEqual(measured.counts, { edited: 0, approved: 0, rejected: 0, material_edits: 0, unmatched: 0 });
  const missing = await measureHumanLedger(null, 'phase0-report', [], []);
  assert.equal(missing.status, 'not_measured');
});

test('output under the portable bundle root and missing document id are rejected before writing', async t => {
  const output = await mkdtemp(path.join(tmpdir(), 'phase0-review-root-'));
  const { root } = await portableRoot(t);
  t.after(() => rm(output, { recursive: true, force: true }));
  await assert.rejects(runReviewBaseline({ bundleRoot: root, output: path.join(root, 'must-not-write'),
    documentId: 'doc-sha256-test' }), /Output must be outside/);
  assert.deepEqual(await readdir(output), []);
  const cli = spawnSync(process.execPath, [scriptPath, '--bundle-root', root, '--output', output], { encoding: 'utf8' });
  assert.notEqual(cli.status, 0);
  assert.match(cli.stderr, /--document-id is required/);
  assert.deepEqual(await readdir(output), []);
});

test('discovery uses immediate children of any supplied read-only root', async t => {
  const { root, documentId } = await portableRoot(t);
  const groups = await discoverDocumentGroups(root);
  assert.deepEqual([...groups.keys()], [documentId]);
  assert.equal(groups.get(documentId).length, 1);
  assert.equal(path.dirname(groups.get(documentId)[0].bundle_path), root);
});
