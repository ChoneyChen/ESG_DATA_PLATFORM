/**
 * Evidence-backed proposals for candidate review, and a bounded machine
 * verification that only approves what the evidence can prove.
 *
 * A delivered candidate carries a printed value, its source pages, the
 * report's own identity and the extraction's guard verdict. This module reads
 * those, proposes the measurement context the reviewer would otherwise type,
 * and says which requirements remain human decisions. `machineVerify` applies
 * the proposal only when every publication requirement is resolved by
 * evidence; the publication itself stays an explicit operator action.
 */
import { measurementDefinitionForMapping, saveMeasurementContext, unitIsSupported } from './publication.mjs';
import { reviewRecord } from './import-bundle.mjs';
import { resolveDimensions, normalizeBoundary, REVIEW_EVIDENCE_VERSION } from './review-evidence.mjs';
export const VERIFICATION_RULE_VERSION = 'machine-verification-v2';
const GROUP_ENTITIES = new Set(['group', 'listed_company']);
// These are stored as their own measurement-context columns, never as
// scientific dimensions.
const TYPED_CONTEXT_FIELDS = new Set(['entity_ref', 'period_start', 'period_end', 'boundary_code',
  'value_role', 'measurement_method', 'reported_unit']);

const nonempty = value => typeof value === 'string' && value.trim().length > 0;
const identityOf = (disclosure) => disclosure?.source_identity?.identity_raw
  || disclosure?.source_payload?.source_fact?.identity_raw || {};

async function candidateRow(db, mappingId) {
  return (await db.query(`SELECT m.disclosure_standard_mapping_id AS mapping_id,m.disclosure_id,m.review_status AS mapping_review_status,
      m.mapping_relation,m.satisfaction_status,m.mapping_reason,
      d.source_report_id,d.raw_indicator_name,d.period_start::text AS period_start,d.period_end::text AS period_end,
      d.quality_status AS fact_review_status,d.source_identity,d.source_payload,
      (SELECT context_id FROM esg.measurement_contexts c WHERE c.mapping_id=m.disclosure_standard_mapping_id LIMIT 1) AS context_id,
      r.organization_ref,r.report_ref,r.report_title_snapshot,
      COALESCE(r.report_period_start::text,r.metadata->>'report_period_start') AS report_period_start,
      COALESCE(r.report_period_end::text,r.metadata->>'report_period_end') AS report_period_end,r.metadata AS report_metadata,
      s.requirement_code AS source_datapoint_id,s.clause_kind,
      v.numeric_value::text AS value_numeric,v.raw_unit,
      COALESCE(v.raw_value_text,q.statement_raw,d.raw_disclosure_text) AS raw_value_text
    FROM esg.disclosure_standard_mappings m JOIN esg.esg_disclosures d USING(disclosure_id)
    JOIN esg.source_report_refs r USING(source_report_id) JOIN esg.standard_requirements s USING(requirement_id)
    LEFT JOIN esg.structured_values v USING(disclosure_id)
    LEFT JOIN esg.qualitative_claims q USING(disclosure_id)
    WHERE m.disclosure_standard_mapping_id=$1`, [mappingId])).rows[0];
}

async function definitionFor(db, mappingId) {
  const definition = await measurementDefinitionForMapping(db, mappingId);
  if (!definition) return null;
  const readiness = (await db.query(`SELECT definition.readiness FROM esg.disclosure_standard_mappings mapping
    JOIN esg.standard_requirements requirement USING(requirement_id)
    JOIN esg.metric_definition_versions definition ON definition.clause_kind=requirement.clause_kind
      AND CASE WHEN requirement.clause_kind='project_extension' THEN definition.extension_id=requirement.extension_id
        ELSE definition.source_datapoint_id=requirement.requirement_code END
    JOIN esg.dictionary_membership membership USING(metric_id,definition_version)
    WHERE mapping.disclosure_standard_mapping_id=$1 AND membership.dictionary_version=
      (SELECT dictionary_version FROM esg.dictionary_versions WHERE sealed ORDER BY release_order DESC LIMIT 1)`,
    [mappingId])).rows[0]?.readiness;
  return { definition, readiness: readiness || null };
}

async function proposalFor(db, mappingId) {
  const row = await candidateRow(db, mappingId);
  if (!row) throw new Error(`Candidate not found: ${mappingId}`);
  const metric = await definitionFor(db, mappingId);
  if (!metric) throw new Error('该映射没有当前指标定义，不能提出测量口径');
  const { definition, readiness } = metric;
  const identity = identityOf(row);
  const rawDimensions = (await db.query('SELECT dimension_key,ordinal,raw_value,canonical_value FROM esg.disclosure_dimensions WHERE disclosure_id=$1 ORDER BY dimension_key,ordinal',
    [row.disclosure_id])).rows;
  const sourceCount = (await db.query('SELECT count(*)::int AS n FROM esg.disclosure_sources WHERE disclosure_id=$1', [row.disclosure_id])).rows[0].n;
  const entity = (await db.query('SELECT entity_ref,entity_kind FROM plat.entities WHERE entity_ref=$1', [row.organization_ref])).rows[0] || null;

  const fixed = { ...(definition.fixed_dimensions || {}) };
  const allowed = new Set([
    ...(definition.required_dimensions || []), ...(definition.comparison_dimensions || []),
    ...Object.keys(definition.dimension_constraints || {}), ...Object.keys(definition.typed_context_constraints || {}),
  ]);
  const dimensions = { ...fixed };
  const dimensionSources = Object.fromEntries(Object.keys(fixed).map(key => [key, 'definition_fixed']));
  // The extraction already read report axes (pollutant, scope, category,
  // method, denominator, boundary). Normalise them instead of asking a person
  // to retype what the source says.
  const resolved = resolveDimensions({
    definition, identity, rawDimensions,
    label: row.raw_indicator_name || '', statement: row.raw_value_text || '',
    unit: row.raw_unit || '', metricLabel: definition.label_zh || '',
  });
  for (const [key, value] of Object.entries(resolved.values)) {
    if (TYPED_CONTEXT_FIELDS.has(key) || nonempty(dimensions[key])) continue;
    dimensions[key] = value; dimensionSources[key] = resolved.sources[key];
  }
  // A dimension the definition admits in exactly one form is not a choice.
  const singleAllowed = (key) => {
    const rule = definition.dimension_constraints?.[key] || definition.typed_context_constraints?.[key];
    return Array.isArray(rule?.allowed_values) && rule.allowed_values.length === 1 ? rule.allowed_values[0] : null;
  };
  for (const key of definition.required_dimensions || []) {
    if (nonempty(dimensions[key])) continue;
    const only = singleAllowed(key);
    if (only) { dimensions[key] = only; dimensionSources[key] = 'definition_single_value'; }
  }
  // A definition may declare that an unstated axis is recorded as
  // `not_stated` rather than guessed or blocking the whole disclosure. It is
  // only usable where the definition imposes no closed value set.
  for (const key of definition.required_dimensions || []) {
    if (nonempty(dimensions[key]) || definition.unresolved_dimension_policy !== 'not_stated') continue;
    if (definition.dimension_constraints?.[key] || definition.typed_context_constraints?.[key]) continue;
    dimensions[key] = 'not_stated'; dimensionSources[key] = 'definition_policy';
  }
  for (const key of TYPED_CONTEXT_FIELDS) { delete dimensions[key]; delete dimensionSources[key]; }

  const periodStart = row.period_start || row.report_period_start || null;
  const periodEnd = row.period_end || row.report_period_end || null;
  const printedBoundary = identity.reporting_boundary || null;
  const boundaryReading = normalizeBoundary(printedBoundary);
  const boundary = boundaryReading?.code
    || (entity && GROUP_ENTITIES.has(entity.entity_kind) ? 'group'
      : entity?.entity_kind === 'subsidiary' ? 'reporting_entity' : null);
  const boundarySource = boundaryReading
    ? (boundaryReading.is_group ? 'printed_group_boundary' : 'printed_narrower_boundary')
    : boundary ? (entity?.entity_kind === 'subsidiary' ? 'entity_scope' : 'report_identity_group') : null;

  let method = null;
  if ((definition.required_context_fields || []).includes('measurement_method') || allowed.has('measurement_method')) {
    const raw = rawDimensions.find(d => ['measurement_method', 'scope_2_method', 'scope2_method'].includes(d.dimension_key));
    method = nonempty(resolved.values.measurement_method) ? resolved.values.measurement_method
      : nonempty(raw?.canonical_value) ? raw.canonical_value
      : nonempty(definition.fixed_dimensions?.measurement_method) ? definition.fixed_dimensions.measurement_method
      : singleAllowed('measurement_method');
  }

  const typed = { entity_ref: entity?.entity_ref || null, period_start: periodStart, period_end: periodEnd,
    boundary_code: boundary, measurement_method: method, value_role: 'actual', reported_unit: row.raw_unit || null };
  const missing = [];
  for (const key of definition.required_dimensions || []) if (!nonempty(typed[key]) && !nonempty(dimensions[key])) missing.push(key);
  for (const key of definition.required_context_fields || []) if (!nonempty(typed[key])) missing.push(key);
  const constraints = { ...(definition.dimension_constraints || {}), ...(definition.typed_context_constraints || {}) };
  const mismatched = [];
  for (const [key, rule] of Object.entries(constraints)) {
    const value = typed[key] ?? dimensions[key];
    if (nonempty(value) && Array.isArray(rule.allowed_values) && !rule.allowed_values.includes(value)) mismatched.push(key);
  }
  // A fixed axis is the definition's meaning, but evidence that explicitly
  // says otherwise is a contradiction, not something the fixed value may hide.
  const fixedMismatch = Object.entries(definition.fixed_dimensions || {})
    .filter(([key]) => !TYPED_CONTEXT_FIELDS.has(key))
    .filter(([key, value]) => nonempty(resolved.values[key]) && resolved.values[key] !== value)
    .map(([key]) => key);

  const checks = [
    { code: 'metric_ready', ok: readiness === 'ready' },
    { code: 'mapping_exact', ok: row.mapping_relation === 'exact' && row.satisfaction_status === 'supports' },
    { code: 'physical_source', ok: sourceCount > 0 },
    { code: 'row_label', ok: definition.value_type === 'statement'
      || nonempty(row.raw_indicator_name) || nonempty(identity.source_row_label) },
    { code: 'value_numeric', ok: definition.value_type === 'statement' ? nonempty(row.raw_value_text) : nonempty(row.value_numeric) },
    { code: 'period_present', ok: Boolean(periodStart && periodEnd && periodStart <= periodEnd) },
    { code: 'entity_registered', ok: Boolean(entity) },
    { code: 'boundary_resolved', ok: Boolean(boundary) },
    { code: 'unit_supported', ok: definition.value_type === 'statement' || unitIsSupported(definition.canonical_unit, row.raw_unit) },
    { code: 'dimensions_complete', ok: missing.length === 0 && mismatched.length === 0 && fixedMismatch.length === 0 },
  ];
  const blockers = checks.filter(check => !check.ok).map(check => check.code);
  const blockerDetails = checks.filter(check => !check.ok).map(check => ({ code: check.code,
    ...(check.code === 'mapping_exact' ? { relation: row.mapping_relation, satisfaction: row.satisfaction_status,
      detail: row.mapping_relation === 'narrower'
        ? '概念匹配，但原文披露的是更窄边界；需确认是否按该边界发布'
        : '提取证据未判定概念完全等价，需人工确认映射' } : {}),
    ...(check.code === 'dimensions_complete' ? { missing, mismatched, fixed_mismatch: fixedMismatch } : {}),
  }));
  const methodSource = method ? (resolved.sources.measurement_method
    || (definition.fixed_dimensions?.measurement_method === method ? 'definition_fixed' : 'source')) : null;
  return {
    mapping_id: mappingId, disclosure_id: row.disclosure_id, report_ref: row.report_ref,
    source_datapoint_id: row.source_datapoint_id,
    raw_indicator_name: row.raw_indicator_name, raw_value_text: row.raw_value_text, raw_unit: row.raw_unit,
    metric_id: definition.metric_id, canonical_unit: definition.canonical_unit, value_type: definition.value_type,
    mapping_relation: row.mapping_relation, satisfaction_status: row.satisfaction_status,
    suggested: { ...typed, dimensions }, period_source: row.period_start ? 'fact' : 'report_identity',
    boundary_source: boundarySource, method_source: methodSource,
    dimension_sources: dimensionSources, resolved_axes: Object.keys(resolved.values),
    unstated_dimensions: Object.entries(dimensions).filter(([, value]) => value === 'not_stated').map(([key]) => key),
    evidence_rule_version: REVIEW_EVIDENCE_VERSION,
    missing, mismatched_dimensions: mismatched, fixed_dimension_mismatch: fixedMismatch,
    evidence_conflicts: fixedMismatch,
    checks, blockers, blocker_details: blockerDetails, resolvable: blockers.length === 0,
    already_approved: row.fact_review_status === 'approved' && row.mapping_review_status === 'approved' && Boolean(row.context_id),
    rule_version: VERIFICATION_RULE_VERSION,
  };
}

export async function candidateProposal(db, { mapping_id }) {
  return proposalFor(db, mapping_id);
}

export async function candidateProposals(db, { report_ref = null, limit = 500 }) {
  const rows = (await db.query(`SELECT m.disclosure_standard_mapping_id AS mapping_id FROM esg.disclosure_standard_mappings m
    JOIN esg.esg_disclosures d USING(disclosure_id) JOIN esg.source_report_refs r USING(source_report_id)
    WHERE ($1::text IS NULL OR r.report_ref=$1) ORDER BY d.created_at LIMIT $2`, [report_ref, limit])).rows;
  const items = [];
  for (const row of rows) items.push(await proposalFor(db, row.mapping_id));
  return { items, resolvable: items.filter(item => item.resolvable).length,
    total: items.length, rule_version: VERIFICATION_RULE_VERSION };
}

export async function machineVerify(db, { mapping_ids = null, report_ref = null, apply = false,
                                         operator_ref = 'machine-verifier', reason = null, limit = 500 }) {
  if (!nonempty(operator_ref)) throw new Error('Machine verification requires an operator identity');
  const targets = mapping_ids?.length ? mapping_ids : (await db.query(`SELECT m.disclosure_standard_mapping_id AS mapping_id
    FROM esg.disclosure_standard_mappings m JOIN esg.esg_disclosures d USING(disclosure_id)
    JOIN esg.source_report_refs r USING(source_report_id)
    WHERE ($1::text IS NULL OR r.report_ref=$1) ORDER BY d.created_at LIMIT $2`, [report_ref, limit])).rows.map(r => r.mapping_id);
  const results = [];
  for (const mappingId of targets) {
    const proposal = await proposalFor(db, mappingId);
    if (proposal.already_approved) { results.push({ mapping_id: mappingId, applied: false, already_approved: true, proposal }); continue; }
    if (!proposal.resolvable) { results.push({ mapping_id: mappingId, applied: false, blockers: proposal.blockers, proposal }); continue; }
    if (!apply) { results.push({ mapping_id: mappingId, applied: false, dry_run: true, proposal }); continue; }
    const boundaryText = {
      printed_group_boundary: '原文标明集团口径', printed_in_source: '原文标明',
      printed_narrower_boundary: '原文标明更窄范围', entity_scope: '报告主体为子公司',
      report_identity_group: '报告主体口径',
    }[proposal.boundary_source] || '报告主体口径';
    const evidence = `规则 ${VERIFICATION_RULE_VERSION}：` + [
      '指标已就绪', `映射由提取证据判定为 ${proposal.mapping_relation}/${proposal.satisfaction_status}`,
      `${proposal.suggested.period_start} 至 ${proposal.suggested.period_end}（${proposal.period_source === 'fact' ? '来源标明' : '报告身份'}）`,
      `边界 ${proposal.suggested.boundary_code}（${boundaryText}）`,
      proposal.value_type === 'statement' ? '陈述型指标无需单位'
        : `单位 ${proposal.raw_unit || '未记录'} 可换算为 ${proposal.canonical_unit}`,
      ...(proposal.unstated_dimensions.length
        ? [`未陈述轴 ${proposal.unstated_dimensions.join('、')} 按定义记录为 not_stated，不进入同口径比较`] : []),
    ].join('；');
    const justification = reason?.trim() ? `${evidence}。${reason.trim()}` : evidence;
    const context = await saveMeasurementContext(db, {
      mapping_id: mappingId, entity_ref: proposal.suggested.entity_ref, period_start: proposal.suggested.period_start,
      period_end: proposal.suggested.period_end, boundary_code: proposal.suggested.boundary_code,
      value_role: proposal.suggested.value_role, measurement_method: proposal.suggested.measurement_method,
      dimensions: proposal.suggested.dimensions, reported_unit: proposal.suggested.reported_unit,
      operator_ref: operator_ref.trim(), reason: justification,
    });
    await reviewRecord(db, { disclosure_id: proposal.disclosure_id, decision: 'approved', operator_ref: operator_ref.trim(), reason: justification });
    await reviewRecord(db, { mapping_id: mappingId, decision: 'approved', mapping_relation: proposal.mapping_relation,
      satisfaction_status: proposal.satisfaction_status, operator_ref: operator_ref.trim(), reason: justification });
    results.push({ mapping_id: mappingId, applied: true, context_id: context.context_id, proposal });
  }
  return { rule_version: VERIFICATION_RULE_VERSION, applied: results.filter(r => r.applied).length,
    blocked: results.filter(r => !r.applied && !r.dry_run).length, items: results };
}
