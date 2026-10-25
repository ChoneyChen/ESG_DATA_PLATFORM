import { createHash } from 'node:crypto';

/**
 * Maintenance purge of the publication artifact layer.
 *
 * Artifacts are everything produced by delivering, reviewing and publishing a
 * report: imports, disclosures, mappings, measurement contexts, review
 * decisions, source-fact snapshots, publication events and published
 * observations. The chain layer (dictionary, metric definitions, standards,
 * units, entities, report identity) is never touched.
 *
 * Snapshot tables are protected by immutable row triggers, so a purge cannot
 * be expressed as row DELETEs. TRUNCATE is used instead: it runs in the same
 * transaction, bypasses row-level guards by design, and rolls back with them
 * if any chain count moves.
 */
export const PURGE_RULE_VERSION = 'artifact-purge-v1';

const stable = value => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v)
  ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b))) : v);
const digest = value => createHash('sha256').update(value).digest('hex');

// Ordered so a reader can see the dependency direction: publication consumes
// candidates, candidates consume imports.
export const ARTIFACT_GROUPS = Object.freeze([
  {key: 'published', label: '已发布观测与数据集', tables: [
    'esg.report_publication_history', 'esg.report_publication_members', 'esg.report_publications',
    'esg.dataset_membership', 'esg.dataset_versions', 'esg.publication_states',
    'esg.observation_sources', 'esg.curated_observations',
    'esg.derived_publication_states', 'esg.derived_observation_lineage',
    'esg.extension_candidate_reviews', 'esg.extension_operand_lineage',
    'esg.extension_derived_candidates', 'esg.extension_derivation_idempotency_keys',
    'esg.extension_derivation_requests',
  ]},
  {key: 'review', label: '审核与测量口径', tables: [
    'esg.measurement_contexts', 'esg.review_decisions',
  ]},
  {key: 'candidates', label: '候选披露、映射与来源快照', tables: [
    'esg.disclosure_standard_mappings', 'esg.disclosure_relations',
    'esg.import_items', 'esg.import_batches', 'esg.import_source_facts',
    'esg.report_source_fact_snapshots', 'esg.extension_import_releases',
    'esg.extension_import_snapshots', 'esg.structured_values',
    'esg.disclosure_dimensions', 'esg.qualitative_claims', 'esg.disclosure_sources',
    'esg.esg_disclosures',
  ]},
  {key: 'imports', label: '导入回执、提取范围与来源保留', tables: [
    'plat.import_receipt_attempts', 'plat.import_receipts', 'plat.extraction_scopes',
    'plat.coverage_decisions', 'plat.import_stage_events',
    'plat.artifact_lifecycle_reservations',
  ]},
  {key: 'events', label: '待发布事件', tables: ['esg.outbox_events']},
]);

export const CHAIN_TABLES = Object.freeze([
  'esg.dictionary_versions', 'esg.dictionary_membership', 'esg.metric_definition_versions',
  'esg.standard_frameworks', 'esg.framework_versions', 'esg.standard_requirements',
  'esg.unit_definitions', 'esg.canonical_concepts', 'esg.concept_synonyms',
  'esg.normalization_rule_versions', 'esg.extension_proposals',
  'esg.extension_definition_decisions', 'esg.extension_releases',
  'esg.source_report_refs', 'esg.publication_control',
  'plat.entities', 'plat.import_contract_releases',
]);

// Rows the schema seeds itself are chain invariants, not report artifacts.
const BASELINE_ROWS = Object.freeze({'esg.dataset_versions': 'dataset_version = 0'});

async function countTable(db, table) {
  const filter = BASELINE_ROWS[table];
  return (await db.query(`SELECT count(*)::int AS n FROM ${table}${filter ? ` WHERE NOT (${filter})` : ''}`)).rows[0].n;
}

async function counts(db, tables) {
  const result = {};
  for (const table of tables) result[table] = await countTable(db, table);
  return result;
}

export async function artifactInventory(db) {
  const groups = [];
  for (const group of ARTIFACT_GROUPS) {
    const rows = await counts(db, group.tables);
    groups.push({key: group.key, label: group.label, tables: group.tables,
      rows, total: Object.values(rows).reduce((sum, n) => sum + n, 0)});
  }
  const chain = await counts(db, CHAIN_TABLES);
  return {
    rule_version: PURGE_RULE_VERSION, groups,
    artifact_rows: groups.reduce((sum, group) => sum + group.total, 0),
    chain, chain_rows: Object.values(chain).reduce((sum, n) => sum + n, 0),
  };
}

function validate({apply, confirm, operator_ref, reason}) {
  if (!apply) return;
  if (confirm !== PURGE_RULE_VERSION) throw new Error(`清理确认标识不正确，应为 ${PURGE_RULE_VERSION}`);
  if (typeof operator_ref !== 'string' || !operator_ref.trim()) throw new Error('清理需要操作员身份');
  if (typeof reason !== 'string' || !reason.trim()) throw new Error('清理需要原因');
}

/**
 * `apply: false` reports what would be removed; `apply: true` removes it in one
 * transaction and refuses to commit if any chain count or the report identity
 * count changed.
 */
export async function purgeArtifacts(db, input = {}) {
  const apply = input.apply === true;
  validate({...input, apply});
  if (!apply) return {applied: false, dry_run: true, ...(await artifactInventory(db))};

  return db.transaction(async tx => {
    // Count inside the transaction so `removed_rows` describes exactly what
    // this reset deleted, even if a writer committed just before it.
    const before = await artifactInventory(tx);
    const chainBefore = await counts(tx, CHAIN_TABLES);
    const tables = ARTIFACT_GROUPS.flatMap(group => group.tables);
    const dictionaryVersion = (await tx.query(`SELECT dictionary_version FROM esg.dictionary_versions
      WHERE sealed ORDER BY release_order DESC LIMIT 1`)).rows[0]?.dictionary_version || 'uninitialized';
    // No CASCADE on purpose: an unlisted referencing table must abort the
    // purge instead of silently widening it into the chain layer.
    await tx.query(`TRUNCATE TABLE ${tables.join(', ')} RESTART IDENTITY`);
    // The baseline is the empty dataset of the current dictionary, not the
    // migration sentinel: a restart must not create a dataset or enqueue a
    // dictionary event merely because artifacts were cleared.
    await tx.query(`INSERT INTO esg.dataset_versions(dataset_version,dictionary_version,content_digest,sealed)
      VALUES(0,$1,$2,true)`, [dictionaryVersion, digest(stable({dictionary_version: dictionaryVersion, observations: []}))]);
    const chainAfter = await counts(tx, CHAIN_TABLES);
    const moved = Object.keys(chainBefore).filter(table => chainBefore[table] !== chainAfter[table]);
    if (moved.length) throw new Error(`清理触及链路层，已回滚：${moved.join(', ')}`);
    const leftovers = Object.entries(await counts(tx, tables)).filter(([, n]) => n > 0);
    if (leftovers.length) throw new Error(`产物表未清空：${leftovers.map(([table]) => table).join(', ')}`);
    return {
      applied: true, dry_run: false, rule_version: PURGE_RULE_VERSION,
      operator_ref: input.operator_ref.trim(), reason: input.reason.trim(),
      removed: before.groups.map(group => ({key: group.key, label: group.label, rows: group.rows, total: group.total})),
      removed_rows: before.artifact_rows,
      dataset_baseline: 'restored',
      chain_before: chainBefore, chain_after: chainAfter, chain_preserved: true,
    };
  });
}
