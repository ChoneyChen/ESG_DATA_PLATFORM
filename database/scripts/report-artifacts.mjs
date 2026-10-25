/**
 * Report artifact inventory and guarded candidate-layer deletion.
 *
 * The platform owns delivered candidates, not extraction outputs. This module
 * locates both for one report: PDF/source identity, import bundles, OCR/IR run
 * identities and candidate rows. Deletion is limited to candidates that have
 * not produced an observation or a measurement context; immutable published
 * snapshots and the report identity are never touched.
 */
export const REPORT_ARTIFACT_RULE_VERSION = 'report-artifacts-v1';

const nonempty = value => typeof value === 'string' && value.trim().length > 0;

async function reportRow(db, reportRef) {
  return (await db.query(`SELECT source_report_id,report_ref,report_version_ref,organization_ref,
      report_title_snapshot,source_artifact_ref,source_sha256,metadata
    FROM esg.source_report_refs WHERE report_ref=$1`, [reportRef])).rows[0];
}

async function candidateRows(db, reportRef, mappingIds = null) {
  const params = [reportRef];
  let filter = '';
  if (mappingIds?.length) {
    filter = ` AND m.disclosure_standard_mapping_id IN (${mappingIds.map((_, index) => `$${index + 2}`).join(',')})`;
    params.push(...mappingIds);
  }
  return (await db.query(`SELECT m.disclosure_standard_mapping_id AS mapping_id,d.disclosure_id,
      d.raw_indicator_name,COALESCE(v.raw_value_text,q.statement_raw,d.raw_disclosure_text) AS raw_value_text,
      d.source_ir_run_id,d.source_identity,d.period_start::text AS period_start,d.period_end::text AS period_end,
      d.quality_status AS fact_review_status,m.review_status AS mapping_review_status,
      m.mapping_relation,s.clause_kind,s.extension_id,s.requirement_code AS source_datapoint_id,
      s.requirement_title AS clause_label,p.publication_status,p.observation_id,p.reason_codes,
      c.context_id,c.revision AS context_revision
    FROM esg.source_report_refs r
    JOIN esg.esg_disclosures d USING(source_report_id)
    JOIN esg.disclosure_standard_mappings m USING(disclosure_id)
    JOIN esg.standard_requirements s USING(requirement_id)
    LEFT JOIN esg.structured_values v USING(disclosure_id)
    LEFT JOIN esg.qualitative_claims q USING(disclosure_id)
    LEFT JOIN esg.publication_states p ON p.mapping_id=m.disclosure_standard_mapping_id
    LEFT JOIN LATERAL (SELECT * FROM esg.measurement_contexts c WHERE c.mapping_id=m.disclosure_standard_mapping_id
      ORDER BY revision DESC LIMIT 1) c ON true
    WHERE r.report_ref=$1${filter}
    ORDER BY d.created_at,d.disclosure_id`, params)).rows;
}

function deletionBlocker(candidate, superseded = false) {
  if (candidate.observation_id || candidate.publication_status === 'published') return 'published_observation';
  if (candidate.context_id) return 'measurement_context_present';
  if (candidate.clause_kind === 'project_extension' || candidate.extension_id) return 'project_extension_candidate';
  if (superseded) return 'superseded_by_another_disclosure';
  return null;
}

export async function reportArtifacts(db, {report_ref}) {
  if (!nonempty(report_ref)) throw new Error('报告产物读取需要 report_ref');
  const report = await reportRow(db, report_ref);
  if (!report) return {rule_version: REPORT_ARTIFACT_RULE_VERSION, report: null, candidates: [], counts: {}};
  const candidates = await candidateRows(db, report_ref);
  const batches = (await db.query(`SELECT b.import_batch_id,b.bundle_job_id,b.bundle_ref,b.package_id,
      b.package_version,b.created_at AS imported_at,
      (SELECT count(*)::int FROM esg.import_items i WHERE i.import_batch_id=b.import_batch_id) AS item_count
    FROM esg.import_batches b WHERE b.source_report_id=$1 ORDER BY b.created_at DESC`,
    [report.source_report_id])).rows;
  const superseded = new Set((await db.query(`SELECT supersedes_disclosure_id FROM esg.esg_disclosures
    WHERE source_report_id=$1 AND supersedes_disclosure_id IS NOT NULL`, [report.source_report_id]))
    .rows.map(row => row.supersedes_disclosure_id));
  const items = candidates.map(candidate => ({
    ...candidate,
    source_ir_run_id: candidate.source_ir_run_id || candidate.source_identity?.ir_run_id || null,
    document_id: candidate.source_identity?.document_id || null,
    page_number: candidate.source_identity?.physical_sources?.[0]?.pdf_page_index != null
      ? candidate.source_identity.physical_sources[0].pdf_page_index + 1 : null,
    deletable: deletionBlocker(candidate, superseded.has(candidate.disclosure_id)) === null,
    deletion_blocker: deletionBlocker(candidate, superseded.has(candidate.disclosure_id)),
  }));
  return {
    rule_version: REPORT_ARTIFACT_RULE_VERSION,
    report: {report_ref: report.report_ref, report_version_ref: report.report_version_ref,
      organization_ref: report.organization_ref, title: report.report_title_snapshot,
      source_artifact_ref: report.source_artifact_ref, source_sha256: report.source_sha256,
      metadata: report.metadata},
    candidates: items,
    extraction_batches: batches,
    counts: {candidates: items.length, deletable: items.filter(item => item.deletable).length,
      blocked: items.filter(item => !item.deletable).length, extraction_batches: batches.length},
    preservation: {report_identity: true, published_observations: true,
      measurement_contexts: true, immutable_source_snapshots: true,
      import_batches: true, extraction_outputs: 'owned_by_extraction_service'},
  };
}

export async function deleteReportCandidates(db, {report_ref, mapping_ids = null, apply = false,
                                                   operator_ref, reason}) {
  if (!nonempty(report_ref)) throw new Error('候选删除需要 report_ref');
  const inventory = await reportArtifacts(db, {report_ref});
  if (!inventory.report) throw new Error('报告身份不存在');
  const selected = inventory.candidates.filter(item => !mapping_ids?.length || mapping_ids.includes(item.mapping_id));
  const deletable = selected.filter(item => item.deletable);
  const blocked = selected.filter(item => !item.deletable);
  if (!apply) return {...inventory, dry_run: true, applied: false, selected: selected.length,
    deletable: deletable.length, blocked_candidates: blocked};
  if (!nonempty(operator_ref) || !nonempty(reason)) throw new Error('候选删除需要操作员身份与原因');
  if (!deletable.length) return {...inventory, dry_run: false, applied: false, deleted: 0,
    selected: selected.length, blocked_candidates: blocked};

  const mappingIds = deletable.map(item => item.mapping_id);
  const disclosureIds = [...new Set(deletable.map(item => item.disclosure_id))];
  return db.transaction(async tx => {
    const maps = mappingIds.map((_, index) => `$${index + 1}`).join(',');
    const mapOffset = mappingIds.length;
    const disclosures = disclosureIds.map((_, index) => `$${mapOffset + index + 1}`).join(',');
    const ownDisclosures = disclosureIds.map((_, index) => `$${index + 1}`).join(',');
    await tx.query(`DELETE FROM esg.outbox_events WHERE target_ref IN (${maps}) OR target_ref IN (${disclosures})`,
      [...mappingIds, ...disclosureIds]);
    await tx.query(`DELETE FROM esg.review_decisions WHERE mapping_id IN (${maps}) OR disclosure_id IN (${disclosures})`,
      [...mappingIds, ...disclosureIds]);
    await tx.query(`DELETE FROM esg.publication_states WHERE mapping_id IN (${maps})`, mappingIds);
    await tx.query(`DELETE FROM esg.disclosure_relations WHERE source_disclosure_id IN (${ownDisclosures})
      OR target_disclosure_id IN (${ownDisclosures})`, disclosureIds);
    await tx.query(`DELETE FROM esg.import_items WHERE disclosure_id IN (${ownDisclosures})`, disclosureIds);
    await tx.query(`DELETE FROM esg.disclosure_standard_mappings WHERE disclosure_standard_mapping_id IN (${maps})`, mappingIds);
    for (const table of ['esg.structured_values', 'esg.disclosure_dimensions',
      'esg.qualitative_claims', 'esg.disclosure_sources']) {
      await tx.query(`DELETE FROM ${table} WHERE disclosure_id IN (${ownDisclosures})`, disclosureIds);
    }
    await tx.query(`DELETE FROM esg.esg_disclosures d WHERE d.disclosure_id IN (${ownDisclosures})
      AND NOT EXISTS (SELECT 1 FROM esg.disclosure_standard_mappings m WHERE m.disclosure_id=d.disclosure_id)`, disclosureIds);
    return {...inventory, dry_run: false, applied: true, deleted: deletable.length,
      deleted_mappings: mappingIds.length, deleted_disclosures: disclosureIds.length,
      selected: selected.length, blocked_candidates: blocked,
      preserved: {report_identity: true, published_observations: true,
        measurement_contexts: true, immutable_source_snapshots: true, import_batches: true,
        extraction_outputs: 'owned_by_extraction_service'}};
  });
}

/**
 * A report can be read and delivered more than once. The same physical cell
 * then arrives as a new disclosure because its extraction fact id changed,
 * doubling every review row and every future observation. This finds rows that
 * describe the same report, clause, value and physical source and keeps the
 * newest reading; older copies are removed only when they are unpublished and
 * carry no measurement context.
 */
export async function collapseDuplicateCandidates(db, {report_ref, apply = false, operator_ref, reason}) {
  const inventory = await reportArtifacts(db, {report_ref});
  if (!inventory.report) return {...inventory, duplicates: 0, collapsed: 0};
  const groups = new Map();
  for (const item of inventory.candidates) {
    const source = item.source_identity?.physical_sources?.[0] || {};
    const page = source.pdf_page_index ?? null;
    const node = source.ir_object_id || null;
    if (page == null || !node) continue;
    const key = [item.source_datapoint_id, item.raw_value_text, page, node,
      item.period_start || '', item.period_end || '',
      item.source_identity?.identity_raw?.reporting_boundary || ''].join('|');
    groups.set(key, [...(groups.get(key) || []), item]);
  }
  const duplicateGroups = [...groups.values()].filter(items => items.length > 1);
  const duplicateMappingIds = [];
  for (const items of duplicateGroups) {
    // Keep the row that already carries a review context or publication over a
    // bare duplicate, then the newest reading. A protected row is never
    // deleted, so it must be the survivor when one exists.
    const ordered = [...items].sort((a, b) => Number(a.deletable) - Number(b.deletable)
      || String(b.source_ir_run_id || '').localeCompare(String(a.source_ir_run_id || '')));
    for (const item of ordered.slice(1)) if (item.deletable) duplicateMappingIds.push(item.mapping_id);
  }
  const summary = {...inventory, duplicate_groups: duplicateGroups.length,
    duplicate_candidates: duplicateGroups.reduce((sum, items) => sum + items.length - 1, 0),
    deletable_duplicates: duplicateMappingIds.length};
  if (!apply || !duplicateMappingIds.length) return {...summary, applied: false, collapsed: 0};
  const result = await deleteReportCandidates(db, {report_ref, mapping_ids: duplicateMappingIds,
    apply: true, operator_ref, reason});
  return {...summary, applied: true, collapsed: result.deleted, delete_result: result};
}
