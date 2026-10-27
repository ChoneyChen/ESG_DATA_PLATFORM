#!/usr/bin/env node
/**
 * Phase 0 review baseline from one document's retained extraction bundles.
 *
 * The database is a new in-memory PGlite instance. Delivery performs the real
 * candidate import and automatic review, but no publication API is called.
 * Human work is measured only from the explicitly supplied ledger, never
 * inferred from automatic review decisions.
 */
import { readFile, readdir, realpath, stat, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { dispatch, dictionaryFile, platformRoot } from '../PlatESG/backend/service.mjs';
import { applyPlatformMigrations } from '../PlatESG/backend/database.mjs';
import { applyMigrations } from '../database/scripts/db.mjs';
import { loadBundle } from '../database/scripts/import-bundle.mjs';
import { seedMetricDictionary, processOutbox } from '../database/scripts/publication.mjs';
import { AUTO_REVIEW_OPERATOR } from '../database/scripts/auto-review.mjs';

// The caller supplies a portable, read-only bundle directory. No live report
// resources, artifact stores, extraction services or database paths are used.
const requireDB = createRequire(path.join(platformRoot, '../database/package.json'));
const { PGlite } = await import(pathToFileURL(requireDB.resolve('@electric-sql/pglite')).href);
const REPORT_FIELDS = ['upstream_system', 'organization_ref', 'report_ref', 'report_version_ref',
  'source_artifact_ref', 'source_sha256', 'report_period_start', 'report_period_end'];

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if (!['--bundle-root', '--output', '--ledger', '--document-id', '--ir-run-id'].includes(key)) {
      throw new Error(`Unknown argument: ${key}`);
    }
    const value = argv[++i];
    if (!value || value.startsWith('--')) throw new Error(`${key} requires a value`);
    options[key.slice(2)] = value;
  }
  for (const key of ['bundle-root', 'output']) if (!options[key]) throw new Error(`Required: ${key}`);
  if (!options['document-id']) throw new Error('--document-id is required; reports must not be mixed');
  return options;
}

export async function discoverDocumentGroups(bundleRoot) {
  const groups = new Map();
  for (const entry of await readdir(bundleRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const bundlePath = path.join(bundleRoot, entry.name);
    const irPath = path.join(bundlePath, 'input/ir-manifest.json');
    try {
      await stat(path.join(bundlePath, 'manifest.json'));
      await stat(irPath);
    } catch (error) {
      if (error.code === 'ENOENT') continue;
      throw error;
    }
    const ir = JSON.parse(await readFile(irPath, 'utf8'));
    if (typeof ir.document_id !== 'string' || !ir.document_id) {
      throw new Error(`Bundle IR has no document_id: ${bundlePath}`);
    }
    if (!groups.has(ir.document_id)) groups.set(ir.document_id, []);
    groups.get(ir.document_id).push({ bundle_path: bundlePath, ir });
  }
  for (const items of groups.values()) items.sort((a, b) => a.bundle_path.localeCompare(b.bundle_path));
  return groups;
}

function documentSha256(documentId) {
  const match = /^doc-sha256-([0-9a-f]{64})$/i.exec(documentId);
  if (!match) throw new Error(`Document id does not contain its source SHA-256: ${documentId}`);
  return match[1].toLowerCase();
}

function reportingYear(bundle) {
  const year = bundle.manifest.configuration?.reporting_period?.year;
  if (!Number.isInteger(year) || year < 1 || year > 9999) {
    throw new Error(`Bundle has no valid manifest reporting year: ${bundle.root}`);
  }
  return year;
}

export function reportingPeriod(bundles) {
  const years = [...new Set(bundles.map(reportingYear))];
  if (years.length !== 1) throw new Error(`Document bundles mix reporting years: ${years.join(', ')}`);
  const year = String(years[0]).padStart(4, '0');
  return { reporting_year: years[0], report_period_start: `${year}-01-01`, report_period_end: `${year}-12-31` };
}

function syntheticMetadata(documentId, bundles) {
  const sourceSha256 = documentSha256(documentId);
  const period = reportingPeriod(bundles);
  return {
    upstream_system: 'phase0-review-baseline-isolated',
    organization_ref: 'phase0-synthetic-entity',
    report_ref: `phase0-${documentId}`,
    report_version_ref: `phase0-${documentId}:v1`,
    source_artifact_ref: `pdf-${sourceSha256.slice(0, 24)}`,
    source_sha256: sourceSha256,
    report_title: `Phase 0 isolated replay: ${documentId}`,
    ...period,
  };
}

export function summarizeCandidates(initialProposals, currentCandidates, currentProposals, automaticReviews, decisions = []) {
  const initialById = new Map(initialProposals.map(item => [item.mapping_id, item]));
  const proposalById = new Map(currentProposals.map(item => [item.mapping_id, item]));
  const initialIds = new Set(initialById.keys());
  const currentIds = new Set(currentCandidates.map(item => item.mapping_id));
  const uniqueInitialProposals = [...initialById.values()];
  const collapsedIds = [...initialIds].filter(id => !currentIds.has(id));
  const humanDecisions = decisions.filter(item => item.operator_ref !== AUTO_REVIEW_OPERATOR);
  const automaticDecisions = decisions.filter(item => item.operator_ref === AUTO_REVIEW_OPERATOR);
  const approvedDecisions = items => {
    const approved = items.filter(item => item.decision === 'approved');
    return { total: approved.length, fact: approved.filter(item => item.disclosure_id).length,
      mapping: approved.filter(item => item.mapping_id).length };
  };
  const blockers = {};
  for (const item of currentProposals.filter(item => !item.already_approved)) {
    for (const code of item.blockers || []) blockers[code] = (blockers[code] || 0) + 1;
  }
  const rows = currentCandidates.map(candidate => {
    const initial = initialById.get(candidate.mapping_id);
    const proposal = proposalById.get(candidate.mapping_id);
    const mappingApproved = candidate.mapping_review_status === 'approved';
    const factApproved = candidate.fact_review_status === 'approved';
    const contextPresent = Boolean(candidate.context);
    const approved = mappingApproved && factApproved && contextPresent;
    const blockerCodes = [...new Set(proposal?.blockers || [])];
    if (!mappingApproved) blockerCodes.push('mapping_review_required');
    if (!factApproved) blockerCodes.push('fact_review_required');
    if (!contextPresent) blockerCodes.push('context_required');
    const states = [];
    if (!factApproved) states.push('fact_review_required');
    if (!mappingApproved) states.push('mapping_review_required');
    if (!contextPresent) states.push('context_required');
    return {
      mapping_id: candidate.mapping_id,
      disclosure_id: candidate.disclosure_id,
      code: candidate.source_datapoint_id || candidate.extension_id || candidate.clause_label,
      raw_indicator_name: candidate.raw_indicator_name,
      raw_value: candidate.raw_value_text ?? candidate.raw_disclosure_text ?? '',
      raw_unit: candidate.raw_unit,
      period_start: candidate.period_start?.slice?.(0, 10) || candidate.period_start || null,
      period_end: candidate.period_end?.slice?.(0, 10) || candidate.period_end || null,
      boundary: candidate.context?.boundary_code || initial?.suggested?.boundary_code || null,
      boundary_source: initial?.boundary_source || null,
      source_fact_ref: candidate.source_identity?.source_fact_ref || candidate.source_identity?.source_fact_id || null,
      document_id: candidate.source_identity?.document_id || null,
      ir_run_id: candidate.source_ir_run_id || candidate.source_identity?.ir_run_id || null,
      ir_revision: candidate.source_ir_revision || candidate.source_identity?.ir_revision || null,
      physical_sources: candidate.source_identity?.physical_sources || candidate.sources || [],
      mapping_relation: candidate.mapping_relation,
      initial_resolvable: initial?.resolvable ?? null,
      initial_blockers: initial?.blockers || [],
      blockers: blockerCodes,
      fact_review_status: candidate.fact_review_status,
      mapping_review_status: candidate.mapping_review_status,
      context_present: contextPresent,
      remaining_human_decisions: { fact: !factApproved, mapping: !mappingApproved, context: !contextPresent },
      final_status: approved ? 'auto_approved' : 'human_decision_required',
      automatic_approved: approved,
      publication_status: candidate.publication_status,
      observation_id: candidate.observation_id || null,
    };
  });
  const remaining = rows.filter(row => row.final_status === 'human_decision_required');
  return {
    considered_distinct_mappings: initialIds.size,
    initially_eligible: uniqueInitialProposals.filter(item => !item.already_approved && item.resolvable).length,
    automatic_approved_final: rows.filter(row => row.automatic_approved).length,
    remaining_human_decisions_required: {
      mappings: remaining.length,
      fact: remaining.filter(row => row.remaining_human_decisions.fact).length,
      mapping: remaining.filter(row => row.remaining_human_decisions.mapping).length,
      context: remaining.filter(row => row.remaining_human_decisions.context).length,
    },
    blockers,
    duplicate_collapse: {
      found: automaticReviews.reduce((sum, review) => sum + (review.duplicates_found || 0), 0),
      collapsed: collapsedIds.length,
      collapsed_mapping_ids: collapsedIds.sort(),
      note: 'found is the sum of each delivery auto-review; the same duplicate may be observed more than once',
    },
    automatic_decisions_recorded: automaticDecisions.length,
    human_decisions_recorded: humanDecisions.length,
    approvals_by_operator: { automatic: approvedDecisions(automaticDecisions), human: approvedDecisions(humanDecisions),
      note: 'Recorded decision counts are not editing measurements; human edits require the explicit ledger' },
    rows,
  };
}

export async function measureHumanLedger(ledger, reportRef, currentCandidates, initialProposals) {
  if (ledger == null) {
    return { status: 'not_measured', reason: 'No explicit human ledger supplied; decisions cannot establish human editing',
      entries: [], counts: { edited: 0, approved: 0, rejected: 0, material_edits: 0, unmatched: 0 } };
  }
  const entries = Array.isArray(ledger) ? ledger : ledger.entries;
  if (!Array.isArray(entries)) throw new Error('Human ledger must be an array or an object containing entries');
  const candidates = new Map(currentCandidates.map(item => [item.mapping_id, item]));
  const initial = new Map(initialProposals.map(item => [item.mapping_id, item]));
  const output = [];
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index];
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new Error(`Invalid ledger entry ${index + 1}`);
    const caseRef = entry.case_id ?? entry.report_ref ?? ledger.case_id;
    if (caseRef !== reportRef) {
      output.push({ ...entry, match_status: 'unmatched_report' });
      continue;
    }
    const candidate = candidates.get(entry.mapping_id);
    if (!candidate) {
      if (!['edited', 'approved', 'rejected'].includes(entry.action)
          || typeof entry.original !== 'object' || entry.original == null || Array.isArray(entry.original)
          || typeof entry.final !== 'object' || entry.final == null || Array.isArray(entry.final)) {
        throw new Error(`Ledger entry ${index + 1} requires original/final objects and edited/approved/rejected action`);
      }
      const keys = [...new Set([...Object.keys(entry.original), ...Object.keys(entry.final)])].sort();
      const changedFields = keys.filter(key => JSON.stringify(entry.original[key]) !== JSON.stringify(entry.final[key]));
      output.push({ case_id: caseRef, mapping_id: entry.mapping_id, action: entry.action,
        reason: entry.reason ?? null, changed_fields: changedFields,
        material_edit: entry.action === 'edited' && changedFields.length > 0, match_status: 'collapsed_or_unmatched_mapping' });
      continue;
    }
    if (!['edited', 'approved', 'rejected'].includes(entry.action)
        || typeof entry.original !== 'object' || entry.original == null || Array.isArray(entry.original)
        || typeof entry.final !== 'object' || entry.final == null || Array.isArray(entry.final)) {
      throw new Error(`Ledger entry ${index + 1} requires original/final objects and edited/approved/rejected action`);
    }
    const keys = [...new Set([...Object.keys(entry.original), ...Object.keys(entry.final)])].sort();
    const changedFields = keys.filter(key => {
      const original = key.startsWith('dimensions.') ? entry.original.dimensions?.[key.slice('dimensions.'.length)] : entry.original[key];
      const final = key.startsWith('dimensions.') ? entry.final.dimensions?.[key.slice('dimensions.'.length)] : entry.final[key];
      return JSON.stringify(original) !== JSON.stringify(final);
    });
    const changed = Object.fromEntries(keys.map(key => {
      const original = key === 'dimensions' ? entry.original[key] : key.startsWith('dimensions.')
        ? entry.original.dimensions?.[key.slice('dimensions.'.length)] : entry.original[key];
      const final = key === 'dimensions' ? entry.final[key] : key.startsWith('dimensions.')
        ? entry.final.dimensions?.[key.slice('dimensions.'.length)] : entry.final[key];
      return [key, { original: original ?? null, final: final ?? null }];
    }));
    const proposal = initial.get(entry.mapping_id);
    const current = { entity_ref: candidate.context?.entity_ref || null,
      period_start: candidate.context?.period_start || candidate.period_start || null,
      period_end: candidate.context?.period_end || candidate.period_end || null,
      boundary_code: candidate.context?.boundary_code || null,
      value_role: candidate.context?.value_role || null,
      measurement_method: candidate.context?.measurement_method || null,
      reported_unit: candidate.context?.reported_unit || candidate.raw_unit || null,
      dimensions: candidate.context?.dimensions || {} };
    const originalMatches = keys.every(key => {
      const actual = key.startsWith('dimensions.') ? proposal?.suggested?.dimensions?.[key.slice('dimensions.'.length)]
        : proposal?.suggested?.[key];
      return actual !== undefined && JSON.stringify(actual) === JSON.stringify(key.startsWith('dimensions.')
        ? entry.original.dimensions?.[key.slice('dimensions.'.length)] : entry.original[key]);
    });
    const finalMatches = keys.every(key => {
      const actual = key.startsWith('dimensions.') ? current.dimensions?.[key.slice('dimensions.'.length)] : current[key];
      return actual !== undefined && JSON.stringify(actual) === JSON.stringify(key.startsWith('dimensions.')
        ? entry.final.dimensions?.[key.slice('dimensions.'.length)] : entry.final[key]);
    });
    output.push({ case_id: caseRef, mapping_id: entry.mapping_id, action: entry.action,
      reason: entry.reason ?? null, changed_fields: changedFields,
      material_edit: entry.action === 'edited' && changedFields.length > 0,
      original_matches_initial_proposal: originalMatches, final_matches_current_context: finalMatches,
      changes: changed, match_status: 'matched' });
  }
  return { status: 'measured', entries: output, counts: {
    edited: output.filter(item => item.action === 'edited').length,
    approved: output.filter(item => item.action === 'approved').length,
    rejected: output.filter(item => item.action === 'rejected').length,
    material_edits: output.filter(item => item.material_edit).length,
    unmatched: output.filter(item => item.match_status !== 'matched').length,
  } };
}

export async function runReviewBaseline({ bundleRoot, output, documentId, ledger = null, irRunId = null,
  dependencies = null } = {}) {
  const root = path.resolve(bundleRoot);
  const realRoot = await realpath(root);
  const outputPath = path.resolve(output);
  // Canonicalize the existing ancestor too: on macOS a portable root under
  // /tmp resolves to /private/tmp, and a lexical check alone would miss it.
  const missingParts = [];
  let ancestor = outputPath;
  while (!(await realpath(ancestor).catch(() => null))) {
    missingParts.unshift(path.basename(ancestor));
    const parent = path.dirname(ancestor);
    if (parent === ancestor) break;
    ancestor = parent;
  }
  const canonicalAncestor = await realpath(ancestor).catch(() => ancestor);
  const canonicalOutput = path.join(canonicalAncestor, ...missingParts);
  if (canonicalOutput === realRoot || canonicalOutput.startsWith(realRoot + path.sep)) {
    throw new Error('Output must be outside the read-only bundle root');
  }
  const groups = await discoverDocumentGroups(realRoot);
  const group = groups.get(documentId)?.filter(item => !irRunId || item.ir.run_id === irRunId);
  if (!group?.length) {
    const available = [...groups.keys()].sort();
    throw new Error(`No retained bundle group for document-id ${documentId} and IR ${irRunId || 'any'}. Available: ${available.join(', ') || 'none'}`);
  }
  const db = dependencies?.db || await PGlite.create();
  const ownsDatabase = !dependencies?.db;
  const previousImportRoots = process.env.PLATESG_IMPORT_ROOTS;
  const previousAutoReview = process.env.PLATESG_AUTO_REVIEW;
  const previousDatabaseUrl = process.env.PLATESG_DATABASE_URL;
  process.env.PLATESG_IMPORT_ROOTS = realRoot;
  process.env.PLATESG_AUTO_REVIEW = '1';
  delete process.env.PLATESG_DATABASE_URL;
  try {
    const api = dependencies?.dispatch || dispatch;
    if (!dependencies?.setupComplete) {
      await applyMigrations(db);
      await applyPlatformMigrations(db);
      const { file_sha256, ...dictionary } = await dictionaryFile();
      await seedMetricDictionary(db, dictionary);
      await processOutbox(db);
    }
    const bundles = [];
    for (const item of group) bundles.push(await (dependencies?.loadBundle || loadBundle)(item.bundle_path));
    const metadata = syntheticMetadata(documentId, bundles);
    await api(db, 'register_entity', { entity_ref: metadata.organization_ref,
      name_zh: 'Phase 0 隔离回放合成主体', entity_kind: 'group', industry_code: 'other', country_code: 'CN', is_demo: false });
    const initialCandidates = await api(db, 'candidates', { report_ref: metadata.report_ref, limit: 100000 });
    const initialProposals = [];
    const deliveries = [];
    for (const bundle of bundles) {
      const before = await api(db, 'candidates', { report_ref: metadata.report_ref, limit: 100000 });
      for (const candidate of before.items) {
        if (!initialCandidates.items.some(item => item.mapping_id === candidate.mapping_id)) initialCandidates.items.push(candidate);
        initialProposals.push(await api(db, 'candidate_proposal', { mapping_id: candidate.mapping_id }));
      }
      const delivery = await api(db, 'deliver', { bundle_path: bundle.root,
        report_metadata: Object.fromEntries(REPORT_FIELDS.map(key => [key, metadata[key]])),
        selection_snapshot: bundle.request.selection_snapshot });
      deliveries.push({ bundle_path: bundle.root, job_id: bundle.manifest.job_id, delivery });
      if (delivery.status !== 'imported') {
        const failure = { bundle_path: bundle.root, job_id: bundle.manifest.job_id, status: delivery.status,
          error: delivery.detail?.message || delivery.status, code: delivery.detail?.code || null };
        if (dependencies?.onDeliveryFailure) await dependencies.onDeliveryFailure(failure);
        throw Object.assign(new Error(`Delivery failed for ${bundle.root}: ${failure.error}`), { deliveryFailure: failure });
      }
      if (delivery.auto_review?.status !== 'completed') {
        throw new Error(`Automatic review failed for ${bundle.root}: ${delivery.auto_review?.message || delivery.auto_review?.status}`);
      }
    }
    const current = await api(db, 'candidates', { report_ref: metadata.report_ref, limit: 100000 });
    const currentProposals = [];
    for (const candidate of current.items) currentProposals.push(await api(db, 'candidate_proposal', { mapping_id: candidate.mapping_id }));
    const decisions = (await db.query(`SELECT mapping_id,disclosure_id,decision,operator_ref,reason,created_at
      FROM esg.review_decisions ORDER BY created_at,decision_id`)).rows;
    const automaticReviews = deliveries.map(item => item.delivery.auto_review);
    const summary = summarizeCandidates(initialProposals, current.items, currentProposals, automaticReviews, decisions);
    const human = await measureHumanLedger(ledger, metadata.report_ref, current.items, [...new Map(initialProposals.map(item => [item.mapping_id, item])).values()]);
    const publications = (await db.query('SELECT count(*)::int AS n FROM esg.report_publications')).rows[0].n;
    const observations = (await db.query('SELECT count(*)::int AS n FROM esg.curated_observations')).rows[0].n;
    if (publications !== 0 || observations !== 0 || summary.rows.some(row => row.observation_id)) {
      throw new Error('Isolated baseline unexpectedly published a report or observation');
    }
    const report = {
      schema_version: 'phase0-review-baseline-1.0', document_id: documentId, ir_run_id: irRunId,
      bundle_root: realRoot, bundle_count: bundles.length, bundles: deliveries,
      synthetic_report: metadata, initial_candidates: initialCandidates.items,
      initial_proposals: initialProposals, current_candidates: current.items,
      automatic_review: automaticReviews, baseline: { ...summary, rows: undefined },
      candidate_mappings: summary.rows, human_review: human,
      publication: { attempted: false, report_publications: publications, curated_observations: observations },
      isolation: { database: 'in-memory PGlite', service_started: false, bundle_root_access: 'read_only' },
    };
    await mkdir(output, { recursive: true });
    await writeFile(path.join(output, 'review-baseline.json'), JSON.stringify(report, null, 2) + '\n');
    const columns = ['mapping_id', 'code', 'raw_value', 'raw_unit', 'period_start', 'period_end', 'boundary',
      'document_id', 'source_fact_ref', 'ir_run_id', 'ir_revision', 'blockers', 'final_status',
      'fact_review_status', 'mapping_review_status', 'context_present', 'publication_status'];
    const escape = value => `"${String(value ?? '').replaceAll('"', '""')}"`;
    const lines = [columns.map(escape).join(',')];
    for (const row of summary.rows) lines.push(columns.map(column => escape(column === 'blockers' ? row[column].join('|') : row[column])).join(','));
    await writeFile(path.join(output, 'human-review.csv'), lines.join('\n') + '\n');
    return report;
  } finally {
    if (previousImportRoots === undefined) delete process.env.PLATESG_IMPORT_ROOTS; else process.env.PLATESG_IMPORT_ROOTS = previousImportRoots;
    if (previousAutoReview === undefined) delete process.env.PLATESG_AUTO_REVIEW; else process.env.PLATESG_AUTO_REVIEW = previousAutoReview;
    if (previousDatabaseUrl === undefined) delete process.env.PLATESG_DATABASE_URL; else process.env.PLATESG_DATABASE_URL = previousDatabaseUrl;
    if (ownsDatabase) await db.close();
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const ledger = options.ledger ? JSON.parse(await readFile(options.ledger, 'utf8')) : null;
  const report = await runReviewBaseline({ bundleRoot: options['bundle-root'], output: path.resolve(options.output),
    documentId: options['document-id'], ledger, irRunId: options['ir-run-id'] || null });
  process.stdout.write(JSON.stringify({ document_id: report.document_id, baseline: report.baseline,
    human_review: report.human_review, output: path.resolve(options.output) }, null, 2) + '\n');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { process.stderr.write(`phase0_review_baseline: ${error.message}\n`); process.exitCode = 1; });
}
