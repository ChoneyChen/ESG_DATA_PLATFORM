import { readFile, realpath } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
import { loadBundle, importBundle, reviewRecord } from '../../database/scripts/import-bundle.mjs';
import { readDataset, saveMeasurementContext, processOutbox, seedMetricDictionary, measurementDefinitionForMapping, scientificDimensionKeys } from '../../database/scripts/publication.mjs';
import {saveExtensionProposal,recordExtensionDefinitionDecision,registerExtensionRelease,listExtensionRegistry,
  requestExtensionDerivation,retryExtensionDerivation,reviewDerivedCandidate,exactDecimalSum} from '../../database/scripts/extension-derivation.mjs';
import {publicMetric,publicEntity,publicObservation,publicEvidence,publicDataset} from './public_contracts.mjs';
import {availableReportedProperties} from '../../database/scripts/scientific-properties.mjs';
import {readReportSourceFacts} from '../../database/scripts/report-source-facts.mjs';
import {candidateProposal,candidateProposals,machineVerify} from '../../database/scripts/candidate-review.mjs';
import {artifactInventory,purgeArtifacts} from '../../database/scripts/purge-artifacts.mjs';
import {resolveImportCatalog,validateMajorImportScope,registerImportContract,listImportContracts} from './import_contracts.mjs';
import {lifecycleReferences,lifecycleReserve,lifecycleReservation,assertImportSourcesAvailable} from './lifecycle.mjs';
import {readDeliveryStatus,deliveryDescriptor} from './delivery_status.mjs';

export const platformRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const hash = data => createHash('sha256').update(data).digest('hex');
const isoDate = value => value instanceof Date ? value.toISOString().slice(0, 10) : value == null ? null : String(value).slice(0, 10);
const error = (message, code = 'invalid_request') => Object.assign(new Error(message), { code });
const stable = value => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v)
  ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b))) : v);
const sourceId = source => hash(stable({sha:source.source_sha256,title:source.report_title_snapshot}));
const documentUrl = (source,version) => /^[0-9a-f]{64}$/.test(source.source_sha256 || '')
  && (/^pdf-[0-9a-f]{24}$/.test(source.source_artifact_ref || '') || /\.pdf$/i.test(source.source_artifact_ref || ''))
  ? `/api/v1/sources/${sourceId(source)}/document?dataset_version=${version}` : null;

export async function dictionaryFile() {
  const bytes = await readFile(path.join(platformRoot, 'catalog/metric-dictionary.v1.json'), 'utf8');
  return { ...JSON.parse(bytes), file_sha256: hash(bytes) };
}

async function definitions(db, dictionaryVersion) {
  return (await db.query(`SELECT d.definition FROM esg.dictionary_membership membership
    JOIN esg.metric_definition_versions d USING(metric_id,definition_version)
    WHERE membership.dictionary_version=$1 ORDER BY membership.source_datapoint_id`, [dictionaryVersion])).rows.map(row => row.definition);
}

export async function snapshot(db, version = null) {
  const data = await readDataset(db, version);
  const metrics = await definitions(db, data.dictionary_version);
  const byId = new Map(metrics.map(m => [m.metric_id, m]));
  const entities = (await db.query('SELECT * FROM plat.entities ORDER BY entity_ref')).rows;
  const names = new Map(entities.map(e => [e.entity_ref, e]));
  const rows = data.observations.map(row => {
    const metric = byId.get(row.metric_id);
    const entity = names.get(row.entity_ref);
    return {
      ...row, source_datapoint_id: metric.source_datapoint_id, label_zh: metric.label_zh,
      clause_kind:metric.clause_kind || 'original_datapoint',extension_id:metric.extension_id || null,
      related_source_datapoint_ids:metric.related_source_datapoint_ids || [],parent_package_id:metric.parent_package_id,
      entity_name: entity?.name_zh || row.entity_ref, is_demo: entity?.is_demo || false,
      // The registry is authoritative; a context that recorded the kind stays
      // as evidence but never overrides it.
      entity_kind: entity?.entity_kind || row.dimensions?.entity_kind || null,
      period_start: isoDate(row.period_start), period_end: isoDate(row.period_end),
      value_numeric: row.numeric_value == null ? null : String(row.numeric_value),
      lower_bound: row.lower_bound == null ? null : String(row.lower_bound),
      upper_bound: row.upper_bound == null ? null : String(row.upper_bound),
      statement_text: row.statement || null, definition_version: row.metric_definition_version || row.definition_version,
      raw_value_text:row.sources?.length===1?row.sources[0].raw_value_text || row.sources[0].statement_raw:null,
      raw_unit:row.sources?.length===1?row.sources[0].raw_unit:null,source_count:row.sources?.length || 0,
      publication_status: 'published', dataset_version: Number(data.dataset_version),
    };
  });
  return { ...data, dataset_version: Number(data.dataset_version), metrics, entities, rows };
}

function listResponse(data, items, extra = {}) {
  return { dataset_version: data.dataset_version, as_of: data.as_of,
    dictionary_version: data.dictionary_version, items, ...extra };
}

async function entities(db, params) {
  const data = await snapshot(db, params.dataset_version);
  return listResponse(data, data.entities.filter(e => data.rows.some(r=>r.entity_ref===e.entity_ref)
    && (!params.search || `${e.name_zh} ${e.entity_ref}`.toLowerCase().includes(params.search.toLowerCase())))
    .map(e => publicEntity({ ...e, coverage_count: new Set(data.rows.filter(r => r.entity_ref === e.entity_ref).map(r => r.metric_id)).size })));
}

async function metrics(db, params) {
  const data = await snapshot(db, params.dataset_version);
  return listResponse(data, data.metrics.filter(m => !params.topic || m.topic === params.topic).map(publicMetric));
}

async function observations(db, params) {
  const data = await snapshot(db, params.dataset_version);
  const queryIdentity = hash(stable({ dataset_version: data.dataset_version,
    entity_ref: params.entity_ref || null, metric_id: params.metric_id || null,
    period_start: params.period_start || null, period_end: params.period_end || null }));
  let after = '';
  if (params.cursor) {
    try {
      const cursor = JSON.parse(Buffer.from(params.cursor, 'base64url').toString('utf8'));
      if (cursor.query !== queryIdentity) throw error('Cursor belongs to another dataset or query', 'invalid_cursor');
      after = cursor.after;
    } catch { throw error('Invalid cursor for this dataset and query', 'invalid_cursor'); }
  }
  const rows = data.rows.filter(r => (!params.entity_ref || r.entity_ref === params.entity_ref)
    && (!params.metric_id || r.metric_id === params.metric_id)
    && (!params.period_start || r.period_start === params.period_start)
    && (!params.period_end || r.period_end === params.period_end)).sort((a, b) => a.observation_id.localeCompare(b.observation_id));
  const remaining = rows.filter(r => r.observation_id > after);
  const items = remaining.slice(0, params.limit || 200);
  const next = remaining.length > items.length ? Buffer.from(JSON.stringify({ query: queryIdentity,
    after: items.at(-1).observation_id })).toString('base64url') : null;
  return listResponse(data, items.map(row => publicObservation(row, data.metrics.find(m => m.metric_id === row.metric_id), data.as_of)), { next_cursor: next });
}

async function internalDetail(db, params) {
  const data = await snapshot(db, params.dataset_version);
  const observation = data.rows.find(r => r.observation_id === params.observation_id);
  if (!observation) throw error('Observation is not present in the requested published dataset', 'not_found');
  const currentSources = (await db.query(`SELECT s.*,v.raw_value_text,v.raw_unit,d.quality_status AS fact_review_status,
    m.review_status AS mapping_review_status,r.report_title_snapshot,r.source_artifact_ref,r.source_sha256,
    r.report_version_ref,os.context_id
    FROM esg.observation_sources os JOIN esg.disclosure_sources s USING(disclosure_id)
    JOIN esg.esg_disclosures d USING(disclosure_id)
    JOIN esg.disclosure_standard_mappings m ON m.disclosure_standard_mapping_id=os.mapping_id
    JOIN esg.source_report_refs r USING(source_report_id)
    LEFT JOIN esg.structured_values v ON v.disclosure_id=os.disclosure_id
    WHERE os.observation_id=$1 ORDER BY s.disclosure_id,s.ordinal`, [observation.observation_id])).rows;
  const history = (await db.query(`SELECT rv.decision,rv.operator_ref,rv.reason,rv.created_at,rv.disclosure_id,rv.mapping_id
    FROM esg.review_decisions rv WHERE rv.disclosure_id IN
      (SELECT disclosure_id FROM esg.observation_sources WHERE observation_id=$1)
    OR rv.mapping_id IN (SELECT mapping_id FROM esg.observation_sources WHERE observation_id=$1)
    ORDER BY rv.created_at,rv.decision_id`, [observation.observation_id])).rows;
  const frozen = observation.sources || [];
  const sources = frozen.flatMap(source => (source.evidence || []).map(evidence => ({...evidence,
    disclosure_id:source.disclosure_id,mapping_id:source.mapping_id,raw_unit:source.raw_unit,raw_value_text:source.raw_value_text,
    context_id:source.context_id,fact_review_decision_id:source.fact_review_decision_id,mapping_review_decision_id:source.mapping_review_decision_id,
    unit_conversion:source.unit_conversion || null})));
  const first = frozen[0];
  const current = currentSources[0];
  return { ...observation, sources, report: first ? {
    report_title_snapshot: first.report_title_snapshot, source_artifact_ref: first.source_artifact_ref,
    source_sha256: first.source_sha256, report_version_ref: first.report_version_ref,
  } : null, review: { fact_review_status: current?.fact_review_status, mapping_review_status: current?.mapping_review_status,
    context_review_status: 'approved', reflects: 'current_review_state', published_snapshot_remains_frozen: true }, history,
    raw_value_text: first?.raw_value_text || observation.statement_text, raw_unit: first?.raw_unit || null };
}

async function internalCoverage(db, params) {
  const data = await snapshot(db, params.dataset_version);
  const entityRefs = params.entity_ref ? [params.entity_ref] : data.entities.map(e => e.entity_ref);
  const pending = (await db.query(`SELECT c.entity_ref,r.organization_ref AS report_organization_ref,s.requirement_code,
    COALESCE(c.period_start,d.period_start)::text AS period_start,COALESCE(c.period_end,d.period_end)::text AS period_end,
    count(*)::int AS n
    FROM esg.esg_disclosures d JOIN esg.source_report_refs r USING(source_report_id)
    JOIN esg.disclosure_standard_mappings m USING(disclosure_id)
    JOIN esg.standard_requirements s USING(requirement_id)
    LEFT JOIN LATERAL (SELECT * FROM esg.measurement_contexts c WHERE c.mapping_id=m.disclosure_standard_mapping_id ORDER BY revision DESC LIMIT 1) c ON true
    GROUP BY c.entity_ref,r.organization_ref,s.requirement_code,COALESCE(c.period_start,d.period_start),COALESCE(c.period_end,d.period_end)`)).rows;
  const decisions = (await db.query(`SELECT DISTINCT ON(c.entity_ref,c.source_datapoint_id,c.period_start,c.period_end)
    c.coverage_decision_id,c.entity_ref,c.source_datapoint_id,c.status,c.period_start::text,c.period_end::text,
    c.evidence_scope,c.operator_ref,c.reason,c.created_at FROM plat.coverage_decisions c
    ORDER BY c.entity_ref,c.source_datapoint_id,c.period_start,c.period_end,c.created_at DESC`)).rows;
  const items = entityRefs.flatMap(entity_ref => data.metrics.map(metric => {
    const allRows = data.rows.filter(r => r.entity_ref === entity_ref && r.metric_id === metric.metric_id);
    const relevant = decisions.filter(r => r.entity_ref === entity_ref && r.source_datapoint_id === metric.source_datapoint_id);
    const periodKeys = new Set([...allRows,...relevant].map(r => `${r.period_start}/${r.period_end}`));
    if (params.period_start && params.period_end) periodKeys.add(`${params.period_start}/${params.period_end}`);
    const periods = [...periodKeys].sort().map(key => {
      const [period_start,period_end] = key.split('/');
      const published_count = allRows.filter(r => r.period_start === period_start && r.period_end === period_end).length;
      const decision = relevant.find(r => r.period_start === period_start && r.period_end === period_end);
      return { period_start,period_end,published_count,status:published_count?'published':decision?.status || 'not_extracted',
        reason:published_count?'该期间存在正式观测':decision?.reason || '该期间尚无正式观测，不能据此推断未披露',evidence_scope:decision?.evidence_scope || null };
    }).filter(r => (!params.period_start || r.period_start === params.period_start) && (!params.period_end || r.period_end === params.period_end));
    const count = periods.reduce((sum,r)=>sum+r.published_count,0);
    const explicit = params.period_start && params.period_end ? relevant.find(r=>r.period_start===params.period_start && r.period_end===params.period_end) : null;
    // Candidate state is intentionally current and is marked as such; it is not a past-dataset fact.
    const hasCandidate = pending.some(r => r.entity_ref === entity_ref && r.requirement_code === (metric.extension_id || metric.source_datapoint_id)
      && (!params.period_start || r.period_start===params.period_start) && (!params.period_end || r.period_end===params.period_end));
    const unresolvedReportCandidate=pending.some(r=>r.entity_ref===null && r.report_organization_ref===entity_ref && r.requirement_code===(metric.extension_id || metric.source_datapoint_id));
    const status = count ? 'published' : metric.readiness === 'planned' ? 'package_planned' : explicit?.status || (hasCandidate ? 'review_required' : 'not_extracted');
    return { entity_ref, metric_id: metric.metric_id, source_datapoint_id: metric.source_datapoint_id, status,
      reason: count ? '存在已发布观测' : explicit?.reason || (status === 'package_planned' ? '主要条款已登记，标准包待建设' : hasCandidate ? '已有候选；审核、口径或发布条件尚未完成' : '尚无正式观测；不能据此推断未披露'),
      published_count: count, candidate_state_as_of: 'current', period_start: explicit?.period_start || null,
      period_end: explicit?.period_end || null, evidence_scope: explicit?.evidence_scope || null,periods,
      report_candidate_identity_unresolved:unresolvedReportCandidate };
  }));
  return listResponse(data, items, {coverage_as_of:new Date().toISOString(),publication_as_of:data.as_of,
    coverage_scope:params.period_start || params.period_end?'specified_period':'all_recorded_periods',
    coverage_state:'current_audited_state',period_filter:{period_start:params.period_start || null,period_end:params.period_end || null}});
}

// Frozen public evidence never queries the live candidate or review tables.
// Internal review reads the current dictionary, not the last published
// snapshot: a newly ready clause must be reviewable before it can produce an
// observation and hence a new dataset. Public /v1/metrics stays dataset-bound.
async function internalMetrics(db, params) {
  const data = await snapshot(db, params.dataset_version);
  const dictionary = await dictionaryFile();
  const current = new Map(dictionary.metrics.map(metric => [metric.source_datapoint_id, metric]));
  const items = data.metrics.map(metric => {
    const live = current.get(metric.source_datapoint_id);
    return live ? { ...metric, ...live } : metric;
  });
  return listResponse(data, items);
}

async function detail(db, params) {  const data = await snapshot(db, params.dataset_version);
  const row = data.rows.find(r => r.observation_id === params.observation_id);
  if (!row) throw error('Observation is not present in the requested published dataset','not_found');
  const metric = data.metrics.find(m => m.metric_id === row.metric_id);
  const first = row.sources?.[0];
  return { ...publicObservation(row, metric, data.as_of),
    sources: (row.sources || []).flatMap(source => (source.evidence || []).map(evidence => publicEvidence(evidence,source))),
    report: first ? {report_title_snapshot:first.report_title_snapshot,source_id:sourceId(first),public_url:documentUrl(first,data.dataset_version)} : null,
    public_revisions: [], calculation:row.calculation?{
      operation:'sum',rule_version:row.calculation.rule_version,input_dataset_version:row.calculation.input_dataset_version,
      operands:row.calculation.operands.map(operand=>({observation_id:operand.observation_id,label_zh:operand.label_zh,
        numeric_value:operand.numeric_value,canonical_unit:operand.canonical_unit}))}:null };
}
async function sources(db, params) {
  const data = await snapshot(db,params.dataset_version);
  const reports = new Map();
  for (const row of data.rows) for (const source of row.sources || []) {
    const id = sourceId(source);
    if (!reports.has(id)) reports.set(id,{source_id:id,report_title:source.report_title_snapshot || '来源报告',public_url:documentUrl(source,data.dataset_version),entity_refs:new Set(),observation_ids:new Set(),is_demo:row.is_demo});
    const report = reports.get(id); report.entity_refs.add(row.entity_ref);report.observation_ids.add(row.observation_id);
  }
  return listResponse(data,[...reports.values()].map(({entity_refs,observation_ids,...report})=>({...report,entity_refs:[...entity_refs].sort(),observation_count:observation_ids.size})));
}
async function sourceDocument(db,params) {
  const data=await snapshot(db,params.dataset_version);
  const source=data.rows.flatMap(row=>row.sources || []).find(source=>sourceId(source)===params.source_id);
  if (!source || !documentUrl(source,data.dataset_version)) throw error('No published PDF source in this dataset','not_found');
  return {source_sha256:source.source_sha256,source_artifact_ref:source.source_artifact_ref,report_title:source.report_title_snapshot,dataset_version:data.dataset_version};
}
async function coverage(db, params) {
  const data = await snapshot(db,params.dataset_version);
  const entityRefs = params.entity_ref ? [params.entity_ref] : [...new Set(data.rows.map(r=>r.entity_ref))];
  return {...listResponse(data,entityRefs.flatMap(entity_ref=>data.metrics.map(metric=>{
    const rows = data.rows.filter(r=>r.entity_ref===entity_ref && r.metric_id===metric.metric_id && (!params.period_start || r.period_start===params.period_start) && (!params.period_end || r.period_end===params.period_end));
    return {entity_ref,metric_id:metric.metric_id,source_datapoint_id:metric.source_datapoint_id,clause_kind:metric.clause_kind || 'original_datapoint',extension_id:metric.extension_id || null,label_zh:metric.label_zh,status:rows.length?'published':'not_available',published_count:rows.length,periods:rows.map(row=>({period_start:row.period_start,period_end:row.period_end,status:'published',published_count:1}))};
  }))),coverage_state:'published_snapshot',publication_as_of:data.as_of};
}
async function publicationStatus(db, params) {
  const limit = params.limit || 200;
  return {states:(await db.query('SELECT * FROM esg.publication_states ORDER BY last_evaluated_at DESC LIMIT $1',[limit])).rows,
    derived_states:(await db.query('SELECT * FROM esg.derived_publication_states ORDER BY evaluated_at DESC LIMIT $1',[limit])).rows,
    events:(await db.query('SELECT * FROM esg.outbox_events ORDER BY created_at DESC LIMIT $1',[limit])).rows,receipts:(await db.query('SELECT * FROM plat.import_receipts ORDER BY created_at DESC LIMIT $1',[limit])).rows,datasets:await datasets(db)};
}

async function reportSummaries(db) {
  return {items:(await db.query(`SELECT r.report_ref,count(*)::int AS candidate_mapping_count,
    count(*) FILTER (WHERE d.quality_status <> 'approved' OR m.review_status <> 'approved' OR c.context_id IS NULL)::int AS review_count,
    count(*) FILTER (WHERE p.publication_status='published')::int AS published_count,
    count(*) FILTER (WHERE p.publication_status='blocked')::int AS blocked_count
    FROM esg.esg_disclosures d JOIN esg.source_report_refs r USING(source_report_id)
    JOIN esg.disclosure_standard_mappings m USING(disclosure_id)
    LEFT JOIN LATERAL (SELECT context_id FROM esg.measurement_contexts c WHERE c.mapping_id=m.disclosure_standard_mapping_id ORDER BY revision DESC LIMIT 1) c ON true
    LEFT JOIN esg.publication_states p ON p.mapping_id=m.disclosure_standard_mapping_id
    GROUP BY r.report_ref ORDER BY r.report_ref`)).rows};
}
function axis(row, key) { return Object.hasOwn(row, key) ? row[key] : row.dimensions?.[key]; }
export function preferEquivalentOrigins(rows,metric) {
  const alternatives=[],removed=new Set();
  if(metric.clause_kind!=='project_extension')return {rows,alternatives};
  for(const entity_ref of new Set(rows.map(row=>row.entity_ref))) {
    const values=rows.filter(row=>row.entity_ref===entity_ref);
    if(values.length!==2 || new Set(values.map(row=>row.value_origin)).size!==2)continue;
    const reported=values.find(row=>row.value_origin==='reported'),derived=values.find(row=>row.value_origin==='derived');
    if(!reported || !derived || ![reported,derived].every(row=>row.value_numeric!=null&&row.lower_bound==null&&row.upper_bound==null&&[null,undefined,'exact'].includes(row.precision_qualifier)))continue;
    if(['metric_id','definition_version','period_start','period_end','boundary_code','value_role','measurement_method','canonical_unit'].some(key=>reported[key]!==derived[key]) || stable(reported.dimensions)!==stable(derived.dimensions))continue;
    if(exactDecimalSum([reported.value_numeric,'0'])!==exactDecimalSum([derived.value_numeric,'0']))continue;
    removed.add(derived.observation_id);
    alternatives.push({entity_ref,entity_name:reported.entity_name,selected_observation_id:reported.observation_id,
      alternative_observation_id:derived.observation_id,alternative_value_origin:'derived',reason:'equal_value_same_context_prefer_reported'});
  }
  return {rows:rows.filter(row=>!removed.has(row.observation_id)),alternatives};
}
function missing(value) { return value == null || value === '' || ['unknown', 'unspecified', '未知'].includes(String(value).toLowerCase()); }
const CORE_AXES = ['definition_version','period_start','period_end','boundary_code','value_role','entity_kind'];

async function comparisons(db, params) {
  const data = await snapshot(db, params.dataset_version);
  const {comparison_contract_version: contractVersion} = await dictionaryFile();
  const metric = data.metrics.find(m => m.metric_id === params.metric_id);
  if (!metric) throw error('Unknown canonical metric_id', 'not_found');
  const excluded = [], eligible = [];
  const candidates = data.rows.filter(r => r.metric_id === params.metric_id && params.entity_refs.includes(r.entity_ref));
  const keys = [...new Set([...CORE_AXES, ...(metric.required_dimensions || []),
    ...(metric.comparison_dimensions || [])])];
  for (const row of candidates) {
    const reasons = [];
    if (metric.value_type !== 'numeric') reasons.push('structured_display_only');
    if (row.value_numeric == null) reasons.push('numeric_point_required');
    if (row.value_role !== 'actual' && row.value_role !== 'restated') reasons.push('actual_measure_required');
    if (row.comparison_eligibility === 'not_comparable') reasons.push(...(row.comparison_reasons || []));
    // Comparison needs every declared axis known: an unknown refinement axis is
    // never assumed equal to another company's value.
    for (const key of keys) if (missing(axis(row, key))) reasons.push(`missing_comparison_dimension:${key}`);
    for (const key of ['period_start','period_end','boundary_code','measurement_method']) {
      if (params[key] && axis(row, key) !== params[key]) reasons.push(`request_dimension_differs:${key}`);
    }
    for (const [key, expected] of Object.entries(params.dimensions || {})) {
      if (stable(axis(row, key)) !== stable(expected)) reasons.push(`request_dimension_differs:${key}`);
    }
    if (reasons.length) excluded.push({ observation_id: row.observation_id, entity_ref: row.entity_ref, reasons: [...new Set(reasons)] });
    else eligible.push(row);
  }
  const cohorts = new Map();
  for (const row of eligible) {
    const signature = stable(Object.fromEntries(keys.map(key => [key, axis(row, key)])));
    if (!cohorts.has(signature)) cohorts.set(signature, []);
    cohorts.get(signature).push(row);
  }
  const available_cohorts = [...cohorts.entries()].map(([signature, rows]) => ({
    dimensions: JSON.parse(signature), observation_count: rows.length, entity_refs: [...new Set(rows.map(r => r.entity_ref))],
  }));
  // No arbitrary first-company reference, no majority cohort, no automatic averaging.
  if (cohorts.size > 1) {
    for (const row of eligible) excluded.push({ observation_id: row.observation_id, entity_ref: row.entity_ref,
      reasons: ['comparison_contract_required:multiple_cohorts'] });
  }
  let rows = cohorts.size === 1 ? [...cohorts.values()][0] : [];
  const originChoice=preferEquivalentOrigins(rows,metric);rows=originChoice.rows;
  const duplicates = new Set(rows.filter(r => rows.filter(other => other.entity_ref === r.entity_ref).length > 1).map(r => r.entity_ref));
  rows = rows.filter(row => {
    if (!duplicates.has(row.entity_ref)) return true;
    excluded.push({ observation_id: row.observation_id, entity_ref: row.entity_ref, reasons: ['one_observation_per_entity_required'] });
    return false;
  });
  for (const entity_ref of params.entity_refs) {
    if (!candidates.some(r => r.entity_ref === entity_ref)) excluded.push({ entity_ref, reasons: ['no_published_observation_in_dataset'] });
  }
  // The contract compares like with like; when the surviving rows still differ
  // on a descriptive axis the reader must see that, not a silent average.
  const caveats = [];
  if (new Set(rows.map(r => r.measurement_method).filter(Boolean)).size > 1) caveats.push('measurement_method_differs_across_entities');
  if (new Set(rows.map(r => r.boundary_code).filter(Boolean)).size > 1) caveats.push('boundary_code_differs_across_entities');
  if (new Set(rows.map(r => r.value_role).filter(Boolean)).size > 1) caveats.push('value_role_differs_across_entities');
  return { dataset_version: data.dataset_version, dictionary_version: data.dictionary_version,
    contract_version: contractVersion, metric_id: metric.metric_id, source_datapoint_id: metric.source_datapoint_id,
    rows: rows.map(row => publicObservation(row,metric,data.as_of)), excluded, caveats,
    available_cohorts,equivalent_alternatives:originChoice.alternatives };
}

async function datasets(db) {
  const items = (await db.query(`SELECT v.*,count(m.observation_id)::int AS observation_count
    FROM esg.dataset_versions v LEFT JOIN esg.dataset_membership m USING(dataset_version)
    GROUP BY v.dataset_version ORDER BY v.dataset_version DESC`)).rows;
  return { items: items.map(publicDataset),
    latest: items.length ? Number(items[0].dataset_version) : 0 };
}

async function registerEntity(db, input) {
  const existing = (await db.query('SELECT * FROM plat.entities WHERE entity_ref=$1', [input.entity_ref])).rows[0];
  if (existing) {
    if (['name_zh','entity_kind','industry_code','country_code','is_demo'].some(key => existing[key] !== input[key]))
      throw error('Entity identity already exists with different registry fields; use a new verified identity', 'entity_identity_conflict');
    return existing;
  }
  return (await db.query(`INSERT INTO plat.entities(entity_ref,name_zh,entity_kind,industry_code,country_code,is_demo,metadata)
    VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING *`, [input.entity_ref,input.name_zh,input.entity_kind,input.industry_code,
    input.country_code,input.is_demo || false,input.metadata || {}])).rows[0];
}

async function context(db, input) {
  const entity = (await db.query('SELECT * FROM plat.entities WHERE entity_ref=$1', [input.entity_ref])).rows[0];
  if (!entity) throw error('Register and verify the reporting entity before approving context', 'entity_not_registered');
  if (input.dimensions?.entity_kind && input.dimensions.entity_kind !== entity.entity_kind)
    throw error('Context entity_kind disagrees with the entity registry');
  const definition=await measurementDefinitionForMapping(db,input.mapping_id);
  return saveMeasurementContext(db, { ...input, dimensions: { ...input.dimensions,
    ...(definition && scientificDimensionKeys(definition).has('entity_kind') ? {entity_kind:entity.entity_kind} : {}) } });
}

async function importCandidate(db, input) {
  const target = await realpath(input.bundle_path);
  const configured = process.env.PLATESG_IMPORT_ROOTS?.split(path.delimiter).filter(Boolean)
    || [path.join(platformRoot, '..', 'ExtractESG'), path.join(platformRoot, '.local')];
  const allowed = await Promise.all(configured.map(root => realpath(root)));
  if (!allowed.some(root => target === root || target.startsWith(root + path.sep)))
    throw error('Bundle is outside configured import roots', 'import_path_forbidden');
  if (!(await db.query('SELECT 1 FROM plat.entities WHERE entity_ref=$1', [input.report_metadata.organization_ref])).rows.length)
    throw error('Import organization_ref must reference a verified registered entity', 'entity_not_registered');
  const bundle = await loadBundle(target);
  const dict = await dictionaryFile();
  const profile = JSON.parse(await readFile(path.join(platformRoot, 'catalog/extraction-profile.e1-e3.json'), 'utf8'));
  const selected = bundle.request.metric_ids || [];
  const knownIds=new Set((bundle.executionMetrics || bundle.standard.metrics).map(m=>m.metric_id));
  if (!selected.length || new Set(selected).size!==selected.length || selected.some(id=>!knownIds.has(id)))
    throw error('Bundle requires valid explicit selected output metrics');
  for (const row of [...bundle.selectedRecords.quantitative_observations,...bundle.selectedRecords.qualitative_assertions])
    {
      if (!selected.includes(row.metric_id)) throw error('Bundle contains an output fact outside its selected scope', 'output_scope_mismatch');
      const extension=bundle.runtimeSnapshot?.selections.find(selection=>selection.extension_id===row.metric_id);
      if(extension&&!extension.value_origins.includes('reported'))throw error('Derived-only selection cannot claim a PDF-reported Core value','output_origin_mismatch');
    }
  const originalIds = bundle.standard.metrics.filter(metric => selected.includes(metric.metric_id)).map(metric => metric.source_datapoint_id);
  const activePath = path.join(platformRoot, '../ExtractESG/standard_packages/dist', bundle.request.package_id,
    bundle.request.package_version, 'package.json');
  let expected;
  try { expected = JSON.parse(await readFile(activePath, 'utf8')); }
  catch { throw error('Import requires an available immutable compiled standard snapshot', 'standard_snapshot_missing'); }
  if (stable(expected) !== stable(bundle.standard)) throw error('Bundle standard content differs from the compiled snapshot', 'standard_snapshot_mismatch');
  if(input.selection_snapshot && bundle.request.selection_snapshot
      && stable(input.selection_snapshot)!==stable(bundle.request.selection_snapshot))
    throw error('Delivery scope differs from the original frozen Bundle scope','import_scope_conflict');
  const scope = structuredClone(input.selection_snapshot || bundle.request.selection_snapshot || {
    profile_id: 'explicit_bundle_selection', profile_version: '1.0.0', profile_digest: hash(stable(originalIds)),
    dictionary_version: 'unassigned', source_datapoint_ids: originalIds,
    selection_kind: 'explicit', package_id: bundle.request.package_id, package_version: bundle.request.package_version,
    scope_provenance:'legacy_explicit_without_frozen_catalog',
  });
  const scopeProvenance=bundle.request.selection_snapshot ? 'bundle_frozen'
    : input.selection_snapshot ? 'externally_declared' : 'legacy_explicit_without_frozen_catalog';
  if(scopeProvenance==='externally_declared')scope.scope_provenance=scopeProvenance;
  if(bundle.runtimeSnapshot) {
    if(scope.extension_selections && stable(scope.extension_selections)!==stable(bundle.runtimeSnapshot.selections))
      throw error('Import extension scope differs from the frozen task','extension_scope_mismatch');
    scope.extension_selections=bundle.runtimeSnapshot.selections;
    scope.runtime_standard_digest=bundle.runtimeSnapshot.runtime_digest;
    scope.original_output_count=originalIds.length;scope.extension_output_count=bundle.runtimeSnapshot.selections.length;
  }
  if(bundle.internalScope)scope.internal_dependency_scope={metric_ids:bundle.internalScope.metric_ids,
    source_datapoint_ids:bundle.internalScope.source_datapoint_ids,required_by_extensions:bundle.internalScope.required_by_extensions,
    scope_role:'internal_dependency',output_policy:'excluded_from_selected_output_and_coverage'};
  if (!scope.profile_id || !scope.profile_version || !/^[0-9a-f]{64}$/.test(scope.profile_digest || '')
      || !Array.isArray(scope.source_datapoint_ids) || (!scope.source_datapoint_ids.length&&!bundle.runtimeSnapshot?.selections.length))
    throw error('Import requires an explicit versioned output selection');
  if ((!originalIds.length&&!bundle.runtimeSnapshot?.selections.length) || originalIds.some(id => !scope.source_datapoint_ids.includes(id)))
    throw error('Bundle output selection differs from its scope snapshot');
  if(new Set(scope.source_datapoint_ids).size!==scope.source_datapoint_ids.length) throw error('Duplicate original ID in scope');
  let importCatalog={permission:'legacy_explicit',contract_id:null,dictionary_version:null};
  if(scope.profile_id===dict.profile_id) {
    const profileBytes=await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/profiles/esrs/2023-set1/platform-major.json'),'utf8');
    const {file_sha256,...currentDictionary}=dict;
    importCatalog=await resolveImportCatalog(db,scope,{dictionary:currentDictionary,dictionary_file_sha256:file_sha256,
      profile:JSON.parse(profileBytes),profile_file_sha256:hash(profileBytes)});
    validateMajorImportScope(scope,importCatalog,bundle,originalIds);
  } else if(scope.profile_id==='explicit_bundle_selection') {
    if(scope.profile_digest!==hash(stable(originalIds)) || stable(scope.source_datapoint_ids)!==stable(originalIds))
      throw error('Explicit bundle selection identity is invalid');
    if(scope.dictionary_version && scope.dictionary_version!=='unassigned') {
      const profileBytes=await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/profiles/esrs/2023-set1/platform-major.json'),'utf8');
      const {file_sha256,...currentDictionary}=dict;
      importCatalog=await resolveImportCatalog(db,scope,{dictionary:currentDictionary,dictionary_file_sha256:file_sha256,
        profile:JSON.parse(profileBytes),profile_file_sha256:hash(profileBytes)});
    } else if(scope.dictionary_digest) throw error('Unassigned explicit scope cannot claim a frozen dictionary digest','import_scope_conflict');
  } else throw error('Unregistered extraction profile');
  const delivery_identity=await deliveryDescriptor(bundle,input.report_metadata);
  return db.transaction(async tx=>{
    await assertImportSourcesAvailable(tx,{job_ids:[bundle.manifest.job_id || path.basename(bundle.root)],
      ir_run_ids:[bundle.request.ir_run_id],source_sha256:input.report_metadata.source_sha256});
    const imported=await importBundle(tx,bundle,input.report_metadata,{inTransaction:true});
    const execution_status=bundle.manifest.execution_status || bundle.manifest.status || 'unknown';
    const result_state=execution_status==='completed' && !bundle.manifest.is_partial
      && !bundle.manifest.unfinished_metric_ids?.length && !bundle.manifest.not_executed_metric_ids?.length
      && !bundle.manifest.processing_metric_ids?.length && !bundle.manifest.evidence_gap_count ? 'complete':'partial';
    const saved = await tx.query('SELECT scope_snapshot FROM plat.extraction_scopes WHERE import_batch_id=$1', [imported.import_batch_id]);
    if (saved.rows.length && stable(saved.rows[0].scope_snapshot) !== stable(scope))
      throw error('Import scope cannot change for an existing batch', 'import_scope_conflict');
    await tx.query(`INSERT INTO plat.extraction_scopes VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING`,
      [imported.import_batch_id,scope.profile_id,scope.profile_version,scope.profile_digest,scope.dictionary_version,
        scope.source_datapoint_ids,result_state,scope]);
    await tx.query(`UPDATE plat.extraction_scopes SET result_state=$1 WHERE import_batch_id=$2`,[result_state,imported.import_batch_id]);
    await tx.query(`INSERT INTO plat.import_stage_events VALUES($1,$2,$3,$4,$5,DEFAULT) ON CONFLICT DO NOTHING`,
      [imported.import_batch_id,hash(stable(bundle.manifest)),execution_status,result_state,bundle.manifest]);
    return { ...imported, result_state, selection_snapshot: scope,
      ...(delivery_identity?{delivery_identity}:{}),
      import_contract:{contract_id:importCatalog.contract_id,permission:importCatalog.permission,
        dictionary_version:importCatalog.dictionary?.dictionary_version || null,scope_provenance:scopeProvenance},
      platform_major_count: dict.metrics.length,executable_major_count: profile.source_datapoint_ids.length };
  }).catch(failure=>{if(delivery_identity)failure.delivery_identity=delivery_identity;throw failure;});
}

async function receipt(db, params) {
  if (!params.status) {
    const original=(await db.query('SELECT * FROM plat.import_receipts WHERE receipt_id=$1',[params.receipt_id])).rows[0];
    if(!original)return null;
    const attempts=(await db.query('SELECT * FROM plat.import_receipt_attempts WHERE receipt_id=$1 ORDER BY created_at,attempt_id',[params.receipt_id])).rows;
    const current=attempts.at(-1);
    return {...original,attempts,current_status:current?.status || original.status,
      current_detail:current?.detail || original.detail,
      current_import_batch_id:current?.detail?.import_batch_id || original.import_batch_id};
  }
  return (await db.query(`INSERT INTO plat.import_receipts(receipt_id,status,import_batch_id,detail)
    VALUES($1,$2,$3,$4) ON CONFLICT(receipt_id) DO NOTHING RETURNING *`,
    [params.receipt_id,params.status,params.import_batch_id || null,params.detail || {}])).rows[0] || null;
}

async function retryReceipt(db, params) {
  const original=await receipt(db,params);
  if(!original)throw error('Import receipt not found','not_found');
  if(original.current_status==='imported')return original;
  if(!original.detail.request)throw error('Invalid descriptor must be corrected and delivered with a new content ID');
  let status='imported',detail;
  try{detail=await importCandidate(db,original.detail.request);}catch(failure){status='failed';detail={message:failure.message,code:failure.code || 'database_error',
    ...(failure.delivery_identity?{delivery_identity:failure.delivery_identity}:{})};}
  const attempt=(await db.query(`INSERT INTO plat.import_receipt_attempts VALUES($1,$2,$3,$4,$5,$6,DEFAULT) RETURNING *`,
    [randomUUID(),params.receipt_id,status,params.operator_ref,params.reason,detail])).rows[0];
  return {receipt_id:params.receipt_id,current_status:status,attempt};
}

async function deliverCandidate(db,input) {
  const receipt_id=hash(stable(input));
  const existing=await receipt(db,{receipt_id});
  if(existing?.current_status==='imported') return {receipt_id,status:'imported',...(existing.current_detail || existing.detail),idempotent:true};
  try {
    const imported=await importCandidate(db,input);
    if(existing) {
      const attempt=await db.query(`INSERT INTO plat.import_receipt_attempts VALUES($1,$2,'imported','delivery-service','幂等交付恢复',$3,DEFAULT) RETURNING *`,
        [randomUUID(),receipt_id,imported]);
      return {receipt_id,status:'imported',...imported,attempt:attempt.rows[0]};
    }
    await receipt(db,{receipt_id,status:'imported',import_batch_id:imported.import_batch_id,detail:imported});
    return {receipt_id,status:'imported',...imported};
  } catch(failure) {
    const detail={request:input,message:failure.message,code:failure.code || 'database_error',
      ...(failure.delivery_identity?{delivery_identity:failure.delivery_identity}:{})};
    await receipt(db,{receipt_id,status:'failed',detail});
    return {receipt_id,status:'failed',detail};
  }
}

async function candidateList(db, params) {
  return { items: (await db.query(`SELECT d.disclosure_id,d.raw_indicator_name,d.raw_disclosure_text,d.source_identity,d.source_payload,d.source_ir_run_id,d.source_ir_revision,
    d.period_start,d.period_end,d.quality_status AS fact_review_status,r.organization_ref,r.report_title_snapshot,
    m.disclosure_standard_mapping_id AS mapping_id,m.review_status AS mapping_review_status,m.mapping_relation,m.satisfaction_status,
    m.mapping_reason,CASE WHEN s.clause_kind='original_datapoint' THEN s.requirement_code ELSE NULL END AS source_datapoint_id,
    s.clause_kind,s.extension_id,s.requirement_title AS clause_label,v.numeric_value::text,v.raw_unit,v.raw_value_text,
    r.report_ref,r.report_version_ref,r.source_artifact_ref,r.source_sha256,r.upstream_system,r.metadata AS report_metadata,
    p.publication_status,p.reason_codes,p.observation_id,
    (SELECT row_to_json(c) FROM esg.measurement_contexts c WHERE c.mapping_id=m.disclosure_standard_mapping_id ORDER BY c.revision DESC LIMIT 1) AS context,
    (SELECT jsonb_agg(jsonb_build_object('dimension_key',dimension_key,'ordinal',ordinal,'raw_value',raw_value) ORDER BY dimension_key,ordinal)
      FROM esg.disclosure_dimensions dd WHERE dd.disclosure_id=d.disclosure_id) AS raw_property_dimensions,
    (SELECT jsonb_agg(row_to_json(e)) FROM esg.disclosure_sources e WHERE e.disclosure_id=d.disclosure_id) AS sources
    FROM esg.esg_disclosures d JOIN esg.source_report_refs r USING(source_report_id)
    JOIN esg.disclosure_standard_mappings m USING(disclosure_id)
    JOIN esg.standard_requirements s USING(requirement_id)
    LEFT JOIN esg.structured_values v USING(disclosure_id)
    LEFT JOIN esg.publication_states p ON p.mapping_id=m.disclosure_standard_mapping_id
    WHERE ($1::text IS NULL OR r.organization_ref=$1) AND ($2::text IS NULL OR r.report_ref=$2)
    AND ($3::text IS NULL OR s.requirement_code=$3) ORDER BY d.created_at,d.disclosure_id LIMIT $4`,
    [params.entity_ref || null,params.report_ref || null,params.source_datapoint_id || null,params.limit || 200])).rows.map(row=>{
      let detail;try{detail=JSON.parse(row.mapping_reason);}catch{}
      const {source_payload,raw_property_dimensions,...candidate}=row;
      // A newly delivered candidate has no publication state until the
      // processor runs. Saying "blocked, not evaluated" is honest; returning
      // null makes an unexamined row look like it has no conditions at all.
      const evaluated = candidate.publication_status !== null && candidate.publication_status !== undefined;
      return {...candidate,
        publication_status: evaluated ? candidate.publication_status : 'blocked',
        reason_codes: evaluated ? candidate.reason_codes : ['publication_not_evaluated'],
        available_reported_properties:availableReportedProperties({source_payload,
        dimensions:raw_property_dimensions || [],sources:row.sources || []}),
        candidate_scope_role:detail?.candidate_scope_role || 'historical_output',
        required_by_extension_ids:detail?.required_by_extension_ids || []};
    }) };
}

async function coverageDecision(db, input) {
  if (!(await dictionaryFile()).metrics.some(m => m.source_datapoint_id === input.source_datapoint_id)) throw error('Unknown original major datapoint ID');
  if (['not_disclosed_in_reviewed_scope','not_applicable','source_unreadable'].includes(input.status) && !Object.keys(input.evidence_scope || {}).length)
    throw error('This missing state requires a reviewed evidence scope and basis');
  return (await db.query(`INSERT INTO plat.coverage_decisions VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,DEFAULT) RETURNING *`,
    [randomUUID(),input.entity_ref,input.source_datapoint_id,input.status,input.period_start,input.period_end,
      input.evidence_scope,input.operator_ref,input.reason])).rows[0];
}

export async function dispatch(db, method, params) {
  switch (method) {
    case 'delivery_status': return readDeliveryStatus(db,params,{importRoots:process.env.PLATESG_IMPORT_ROOTS?.split(path.delimiter).filter(Boolean)
      || [path.join(platformRoot,'../ExtractESG'),path.join(platformRoot,'.local')]});
    case 'import_catalogs': return listImportContracts(db);
    case 'register_import_catalog': return registerImportContract(db,params);
    case 'source_facts': return readReportSourceFacts(db,params);
    case 'lifecycle_references': return lifecycleReferences(db,params);
    case 'lifecycle_reserve': return lifecycleReserve(db,params);
    case 'lifecycle_reservation': return lifecycleReservation(db,params);
    case 'health': return { status: 'ok', database: process.env.PLATESG_DATABASE_URL ? 'postgresql' : 'pglite', ...(await datasets(db)) };
    case 'entities': return entities(db, params);
    case 'metrics': return metrics(db, params);
    case 'observations': return observations(db, params);
    case 'detail': return detail(db, params);
    case 'sources': return sources(db,params);
    case 'source_document': return sourceDocument(db,params);
    case 'internal_detail': return internalDetail(db,params);
    case 'internal_coverage': return internalCoverage(db,params);
    case 'internal_metrics': return internalMetrics(db, params);
    case 'extension_registry': return listExtensionRegistry(db);
    case 'extension_propose': return saveExtensionProposal(db,params);
    case 'extension_decide': {
      if(!params.authority_roles?.includes('admin'))throw error('Definition approval requires authenticated admin','forbidden');
      return recordExtensionDefinitionDecision(db,{receipt:params.receipt},{actor_ref:params.receipt.actor_ref,authority_roles:['approve_extension_definition']});
    }
    case 'extension_register': return registerExtensionRelease(db,params);
    case 'extension_platform_selection': return seedMetricDictionary(db,params.dictionary);
    case 'extension_derive': return requestExtensionDerivation(db,params.payload,{requested_by:params.operator_ref});
    case 'extension_retry_derivation': return retryExtensionDerivation(db,params);
    case 'extension_review_calculation': return reviewDerivedCandidate(db,params);
    case 'extension_calculations': return {items:(await db.query(`SELECT r.*,c.candidate_id,c.numeric_value::text,c.canonical_unit,c.reviewed_axes,c.lineage_snapshot,
      COALESCE((SELECT decision FROM esg.extension_candidate_reviews v WHERE v.candidate_id=c.candidate_id ORDER BY review_order DESC LIMIT 1),'pending') AS review_status,
      p.publication_status,p.reason_codes AS publication_reasons FROM esg.extension_derivation_requests r
      LEFT JOIN esg.extension_derived_candidates c USING(request_id) LEFT JOIN esg.derived_publication_states p USING(candidate_id)
      ORDER BY r.created_at DESC LIMIT 1000`)).rows};
    case 'internal_entities': return {items:(await db.query('SELECT * FROM plat.entities ORDER BY entity_ref')).rows};
    case 'publication_status': return publicationStatus(db,params);
    case 'report_summaries': return reportSummaries(db);
    case 'coverage': return coverage(db, params);
    case 'comparisons': return comparisons(db, params);
    case 'datasets': return datasets(db);
    case 'register_entity': return registerEntity(db, params);
    case 'context': return context(db, params);
    case 'import': return importCandidate(db, params);
    case 'deliver': return deliverCandidate(db,params);
    case 'candidates': return candidateList(db, params);
    case 'candidate_proposal': return candidateProposal(db, params);
    case 'candidate_proposals': return candidateProposals(db, params);
    case 'machine_verify': return machineVerify(db, params);
    case 'artifact_inventory': return artifactInventory(db);
    case 'purge_artifacts': return purgeArtifacts(db, params);
    case 'review': return reviewRecord(db, params);
    case 'coverage_decision': return coverageDecision(db, params);
    case 'receipt': return receipt(db, params);
    case 'retry_receipt': return retryReceipt(db,params);
    case 'seed_demo': {
      if (process.env.PLATESG_ALLOW_DEMO_SEED !== '1') throw error('Synthetic fixture loading is disabled');
      const { seedDemo } = await import('./demo.mjs');
      return seedDemo(db);
    }
    case 'publish': return processOutbox(db, { limit: params.limit || 100 });
    default: throw error('Unsupported database operation', 'unknown_operation');
  }
}
