import { createHash, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const UNIT_RULES = JSON.parse(await readFile(new URL('./unit-conversions.v1.json',import.meta.url),'utf8'));
export const NORMALIZATION_RULE_VERSION = UNIT_RULES.rule_version;
const stableJSON = value => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v)
  ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b))) : v);
const digest = value => createHash('sha256').update(value).digest('hex');
const UNIT_RULE_DIGEST=digest(stableJSON(UNIT_RULES));
const uuid = value => { const h = digest(value); return `${h.slice(0,8)}-${h.slice(8,12)}-5${h.slice(13,16)}-a${h.slice(17,20)}-${h.slice(20,32)}`; };
const nonempty = value => typeof value === 'string' && value.trim().length > 0;
const present = value => value != null && value !== '' && !['unknown','unspecified','not_reported','unverified'].includes(value);
const dateText = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);

export async function seedMetricDictionary(db, dictionary) {
  if (!nonempty(dictionary.dictionary_version) || !nonempty(dictionary.profile_id) || !Array.isArray(dictionary.metrics) || !dictionary.metrics.length)
    throw new Error('A versioned metric dictionary and profile are required');
  if (new Set(dictionary.metrics.map(m => m.source_datapoint_id)).size !== dictionary.metrics.length)
    throw new Error('Duplicate original datapoint ID in metric dictionary');
  return db.transaction(async tx => {
    for (const m of dictionary.metrics) {
      if (![m.metric_id,m.definition_version,m.source_datapoint_id].every(nonempty) || !['ready','planned'].includes(m.readiness)
          || !['numeric','statement'].includes(m.value_type) || !Array.isArray(m.required_dimensions) || !Array.isArray(m.comparison_dimensions))
        throw new Error(`Invalid metric definition: ${m.source_datapoint_id}`);
      const immutable = {...m,profile_id:dictionary.profile_id};
      const hash = digest(stableJSON(immutable));
      const known = (await tx.query('SELECT definition_digest,dictionary_version FROM esg.metric_definition_versions WHERE metric_id=$1 AND definition_version=$2',
        [m.metric_id,m.definition_version])).rows[0];
      if (known && (known.definition_digest !== hash || known.dictionary_version !== dictionary.dictionary_version))
        throw new Error(`Immutable metric definition differs: ${m.metric_id}@${m.definition_version}`);
      if (!known) await tx.query(`INSERT INTO esg.metric_definition_versions(metric_id,definition_version,dictionary_version,source_datapoint_id,readiness,definition,definition_digest)
        VALUES($1,$2,$3,$4,$5,$6,$7)`, [m.metric_id,m.definition_version,dictionary.dictionary_version,m.source_datapoint_id,m.readiness,immutable,hash]);
    }
    const eventId = uuid(`dictionary:${dictionary.dictionary_version}:${digest(stableJSON(dictionary))}`);
    await tx.query(`INSERT INTO esg.outbox_events(event_id,event_type,target_ref) VALUES($1,'dictionary',$2) ON CONFLICT DO NOTHING`,[eventId,dictionary.dictionary_version]);
    return {dictionary_version:dictionary.dictionary_version,metrics:dictionary.metrics.length};
  });
}

// Context is an explicit reviewer decision. No company, dates, units or methods
// are filled from a report name or from a standard's expected answer.
export async function saveMeasurementContext(db, payload) {
  for (const key of ['mapping_id','entity_ref','boundary_code','operator_ref','reason'])
    if (!nonempty(payload[key])) throw new Error(`Reviewed context requires ${key}`);
  if (!dateText(payload.period_start) || !dateText(payload.period_end) || payload.period_start > payload.period_end)
    throw new Error('Reviewed context requires an ordered ISO reporting period');
  if (!['actual','restated','context'].includes(payload.value_role)) throw new Error('Unsupported reviewed value role');
  if (payload.value_role === 'restated' && !payload.supersedes_observation_id) throw new Error('Restatement requires explicit supersession');
  if (!payload.dimensions || Array.isArray(payload.dimensions) || typeof payload.dimensions !== 'object')
    throw new Error('Reviewed dimensions must be an object');
  if (Object.values(payload.dimensions).some(v => typeof v !== 'string' || !v.trim()))
    throw new Error('Dimensions must contain explicit nonempty normalized codes');
  if ('measurement_method' in payload.dimensions) throw new Error('Measurement method belongs to the typed context field');
  const method = payload.measurement_method?.trim() || null;
  const unit = payload.reported_unit?.trim() || null;
  return db.transaction(async tx => {
    // Serialize revisions of this mapping even on a full PostgreSQL server.
    const target = (await tx.query('SELECT disclosure_id FROM esg.disclosure_standard_mappings WHERE disclosure_standard_mapping_id=$1 FOR UPDATE',[payload.mapping_id])).rows[0];
    if (!target) throw new Error('Context mapping does not exist');
    const prior=(await tx.query(`SELECT *,period_start::text,period_end::text FROM esg.measurement_contexts WHERE mapping_id=$1 ORDER BY revision DESC LIMIT 1`,[payload.mapping_id])).rows[0];
    const reviewed={entity_ref:payload.entity_ref.trim(),period_start:payload.period_start,period_end:payload.period_end,boundary_code:payload.boundary_code.trim(),value_role:payload.value_role,
      measurement_method:method,dimensions:payload.dimensions,reported_unit:unit,operator_ref:payload.operator_ref.trim(),reason:payload.reason.trim(),supersedes_observation_id:payload.supersedes_observation_id || null};
    if(prior && Object.keys(reviewed).every(key=>stableJSON(prior[key])===stableJSON(reviewed[key])))
      return {context_id:prior.context_id,revision:prior.revision,idempotent:true};
    const revision = (await tx.query('SELECT COALESCE(max(revision),0)+1 AS revision FROM esg.measurement_contexts WHERE mapping_id=$1',[payload.mapping_id])).rows[0].revision;
    const context_id = randomUUID();
    await tx.query(`INSERT INTO esg.measurement_contexts(context_id,mapping_id,revision,entity_ref,period_start,period_end,boundary_code,value_role,
      measurement_method,dimensions,reported_unit,operator_ref,reason,supersedes_observation_id) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`,
      [context_id,payload.mapping_id,revision,payload.entity_ref.trim(),payload.period_start,payload.period_end,payload.boundary_code.trim(),payload.value_role,
        method,payload.dimensions,unit,payload.operator_ref.trim(),payload.reason.trim(),payload.supersedes_observation_id || null]);
    await tx.query(`INSERT INTO esg.outbox_events(event_id,event_type,target_ref) VALUES($1,'context',$2)`,[context_id,payload.mapping_id]);
    return {context_id,revision,publication_status:'not_published'};
  });
}

// Versioned, immutable rules supply all aliases and factors. Unknown source
// units cannot be replaced by a reviewer's chosen canonical display unit.
const cleanUnit = u => (u || '').replace(/\s+/gu,'');
const seenAliases=new Set();
for(const rule of UNIT_RULES.conversions) {
  if(!['MWh','tCO2e','tonne','m3','ratio'].includes(rule.canonical_unit) || !Array.isArray(rule.aliases) || !rule.aliases.length
      || ![rule.multiplier,rule.divisor].every(v=>typeof v==='string' && /^\d+(?:\.\d+)?$/.test(v) && /[1-9]/.test(v)))
    throw new Error('Invalid versioned unit conversion rule');
  for(const alias of rule.aliases) {
    if(!nonempty(alias))throw new Error('Empty unit alias');
    const key=`${rule.canonical_unit}:${cleanUnit(alias)}`;
    if(seenAliases.has(key))throw new Error(`Ambiguous unit alias: ${alias}`);
    seenAliases.add(key);
  }
}
const unitRule=(canonical,unit)=>UNIT_RULES.conversions.find(rule=>rule.canonical_unit===canonical && rule.aliases.some(alias=>cleanUnit(alias)===cleanUnit(unit)));
async function registerUnitRules(tx) {
  const known=(await tx.query('SELECT rule_digest FROM esg.normalization_rule_versions WHERE rule_version=$1',[NORMALIZATION_RULE_VERSION])).rows[0];
  if(known && known.rule_digest!==UNIT_RULE_DIGEST) throw new Error(`Immutable normalization rule differs: ${NORMALIZATION_RULE_VERSION}`);
  if(!known) {
    await tx.query('INSERT INTO esg.normalization_rule_versions(rule_version,rule_digest,definition) VALUES($1,$2,$3)',[NORMALIZATION_RULE_VERSION,UNIT_RULE_DIGEST,UNIT_RULES]);
    await tx.query(`INSERT INTO esg.outbox_events(event_id,event_type,target_ref) VALUES($1,'normalization',$2) ON CONFLICT DO NOTHING`,
      [uuid(`normalization:${NORMALIZATION_RULE_VERSION}:${UNIT_RULE_DIGEST}`),NORMALIZATION_RULE_VERSION]);
  }
}
async function normalizedValue(tx, row, definition) {
  if (definition.value_type === 'statement') {
    if (!nonempty(row.statement_raw)) return {error:'statement_missing'};
    return {numeric_value:null,lower_bound:null,upper_bound:null,statement:row.statement_raw,precision_qualifier:null};
  }
  if (row.value_type !== 'numeric') return {error:'numeric_value_missing'};
  // Reviewer must explicitly confirm the unit; candidates cannot supply it
  // silently. Different raw display units remain available as evidence.
  const sourceRule=unitRule(definition.canonical_unit,row.raw_unit);
  if(!row.raw_unit || !sourceRule) return {error:'source_unit_unresolved'};
  if (!row.reported_unit) return {error:'reported_unit_not_reviewed'};
  const reviewedRule=unitRule(definition.canonical_unit,row.reported_unit);
  if(!reviewedRule) return {error:'unit_incompatible_or_unknown'};
  const factor=[sourceRule.multiplier,sourceRule.divisor];
  if(stableJSON(factor)!==stableJSON([reviewedRule.multiplier,reviewedRule.divisor])) return {error:'reported_unit_disagrees_with_source'};
  const numeric = (await tx.query(`SELECT
    trim_scale(numeric_value*$2::numeric/$3::numeric)::text AS numeric_value,
    trim_scale(lower_bound*$2::numeric/$3::numeric)::text AS lower_bound,
    trim_scale(upper_bound*$2::numeric/$3::numeric)::text AS upper_bound,
    precision_qualifier
    FROM esg.structured_values WHERE disclosure_id=$1`,[row.disclosure_id,...factor])).rows[0];
  if ([numeric.numeric_value,numeric.lower_bound,numeric.upper_bound].filter(v=>v!=null).some(v=>!/^[-+]?\d+(?:\.\d+)?$/.test(v)))
    return {error:'non_finite_numeric_value'};
  const bounds=definition.value_constraints || {};
  if(bounds.minimum!=null || bounds.maximum!=null) {
    const valid=(await tx.query(`SELECT COALESCE(bool_and(($4::numeric IS NULL OR v >= $4::numeric) AND ($5::numeric IS NULL OR v <= $5::numeric)),false) AS valid FROM
      (VALUES($1::numeric),($2::numeric),($3::numeric)) AS valueset(v) WHERE v IS NOT NULL`,[numeric.numeric_value,numeric.lower_bound,numeric.upper_bound,bounds.minimum ?? null,bounds.maximum ?? null])).rows[0].valid;
    if(!valid) return {error:definition.canonical_unit==='ratio'?'ratio_out_of_range':'metric_value_out_of_range'};
  }
  return {...numeric,statement:null,unit_conversion:{rule_version:NORMALIZATION_RULE_VERSION,rule_digest:UNIT_RULE_DIGEST,
    source_unit:row.raw_unit,reviewed_unit:row.reported_unit,canonical_unit:definition.canonical_unit,multiplier:factor[0],divisor:factor[1],
    arithmetic:sourceRule.arithmetic || 'exact_decimal_multiplication'}};
}
function contextValue(row,key) { return key in row && ['measurement_method','entity_ref','boundary_code','period_start','period_end','value_role'].includes(key) ? row[key] : row.dimensions?.[key]; }
function contractFailures(row,def) {
  const failures=[];
  for(const key of def.required_dimensions || []) if(!present(contextValue(row,key))) failures.push(`missing_required_dimension:${key}`);
  for(const key of def.required_context_fields || []) if(!present(contextValue(row,key))) failures.push(`missing_required_context:${key}`);
  for(const [key,rule] of Object.entries({...def.dimension_constraints,...def.typed_context_constraints})) {
    const value=contextValue(row,key);
    if(present(value) && rule.allowed_values && !rule.allowed_values.includes(value)) failures.push(`dimension_value_mismatch:${key}`);
  }
  for(const [key,value] of Object.entries(def.fixed_dimensions || {}))
    if(contextValue(row,key)!==value) failures.push(`fixed_dimension_mismatch:${key}`);
  const expectedRole=def.value_role || 'actual';
  if(row.value_role!==expectedRole && !(expectedRole==='actual' && row.value_role==='restated' && row.supersedes_observation_id)) failures.push('value_role_mismatch');
  return failures;
}

async function evaluate(tx, row, metric) {
  const reasons=[];
  if(row.quality_status!=='approved') reasons.push(`fact_${row.quality_status}`);
  if(row.review_status!=='approved') reasons.push(`mapping_${row.review_status}`);
  if(row.mapping_relation!=='exact' || row.satisfaction_status!=='supports') reasons.push('mapping_not_equivalent');
  if(!metric) reasons.push('not_in_major_profile');
  if(metric?.readiness!=='ready') reasons.push('metric_not_ready');
  if(!row.context_id) reasons.push('reviewed_context_missing');
  if((await tx.query('SELECT 1 FROM esg.disclosure_sources WHERE disclosure_id=$1 LIMIT 1',[row.disclosure_id])).rows.length===0) reasons.push('physical_source_missing');
  if((await tx.query(`SELECT 1 FROM esg.esg_disclosures WHERE supersedes_disclosure_id=$1 AND quality_status='approved' LIMIT 1`,[row.disclosure_id])).rows.length) reasons.push('source_fact_superseded');
  if(reasons.length) return {row,metric,reasons};
  const def=metric.definition;
  reasons.push(...contractFailures(row,def));
  const b=def.standard_binding;
  if(!b || !(await tx.query(`SELECT 1 FROM esg.import_batches b JOIN esg.import_items i USING(import_batch_id)
      JOIN esg.framework_versions fv ON fv.framework_version_id=$5
      WHERE i.disclosure_id=$1 AND b.package_id=$2 AND b.package_version=$3 AND b.package_digest=$4
      AND fv.version_label LIKE '%' || b.package_id || '@' || b.package_version LIMIT 1`,[row.disclosure_id,b.package_id,b.package_version,b.digest,row.framework_version_id])).rows.length)
    reasons.push('standard_snapshot_mismatch');
  if(reasons.length) return {row,metric,reasons};
  const value=await normalizedValue(tx,row,def);
  if(value.error) return {row,metric,reasons:[value.error]};
  if(row.supersedes_observation_id) {
    const previous=(await tx.query('SELECT entity_ref,metric_id,definition_version,period_start::text,period_end::text,boundary_code,measurement_method,dimensions FROM esg.curated_observations WHERE observation_id=$1',[row.supersedes_observation_id])).rows[0];
    if(!previous || ['entity_ref','period_start','period_end','boundary_code','measurement_method'].some(k=>previous[k]!==row[k])
        || previous.metric_id!==metric.metric_id || previous.definition_version!==metric.definition_version || stableJSON(previous.dimensions)!==stableJSON(row.dimensions))
      return {row,metric,reasons:['invalid_supersession_identity']};
  }
  const identity={entity_ref:row.entity_ref,metric_id:metric.metric_id,definition_version:metric.definition_version,
    period_start:row.period_start,period_end:row.period_end,boundary_code:row.boundary_code,value_role:row.value_role,
    measurement_method:row.measurement_method,dimensions:row.dimensions};
  const comparisonReasons=[];
  if(def.value_type==='statement') comparisonReasons.push('qualitative_display_only');
  if(value.numeric_value==null && def.value_type==='numeric') comparisonReasons.push('interval_not_point');
  for(const key of def.comparison_dimensions || []) if(!present(contextValue(row,key))) comparisonReasons.push(`missing_comparison_dimension:${key}`);
  if(row.value_role==='restated') comparisonReasons.push('restated_value_requires_matching_revision');
  const {unit_conversion,...canonicalValue}=value;
  return {row,metric,reasons:[],value,identity,identity_key:digest(stableJSON(identity)),value_key:digest(stableJSON(canonicalValue)),
    comparison_eligibility:comparisonReasons.length?'not_comparable':'comparable',comparison_reasons:comparisonReasons};
}

async function reconcile(tx) {
  const metrics=(await tx.query('SELECT * FROM esg.metric_definition_versions ORDER BY created_at DESC,metric_id')).rows;
  const metricMap=new Map();
  for(const m of metrics) if(!metricMap.has(m.source_datapoint_id)) metricMap.set(m.source_datapoint_id,m);
  const dictionaryVersion=metrics[0]?.dictionary_version || 'uninitialized';
  const rows=(await tx.query(`SELECT m.disclosure_standard_mapping_id AS mapping_id,m.disclosure_id,m.review_status,m.mapping_relation,m.satisfaction_status,
    r.requirement_code,r.framework_version_id,d.quality_status,d.supersedes_disclosure_id,
    v.value_type,v.raw_unit,q.statement_raw,
    c.context_id,c.revision,c.entity_ref,c.period_start::text,c.period_end::text,c.boundary_code,c.value_role,c.measurement_method,
    c.dimensions,c.reported_unit,c.supersedes_observation_id
    FROM esg.disclosure_standard_mappings m JOIN esg.standard_requirements r USING(requirement_id)
    JOIN esg.esg_disclosures d USING(disclosure_id)
    LEFT JOIN esg.structured_values v USING(disclosure_id) LEFT JOIN esg.qualitative_claims q USING(disclosure_id)
    LEFT JOIN LATERAL (SELECT * FROM esg.measurement_contexts c WHERE c.mapping_id=m.disclosure_standard_mapping_id ORDER BY revision DESC LIMIT 1) c ON true
    ORDER BY m.disclosure_standard_mapping_id`)).rows;
  const candidates=[];
  for(const row of rows) candidates.push(await evaluate(tx,row,metricMap.get(row.requirement_code)));
  const explicitReplacements=new Set(candidates.filter(c=>!c.reasons.length).map(c=>c.row.supersedes_observation_id).filter(Boolean));
  for(const c of candidates.filter(c=>!c.reasons.length)) {
    const oldIds=(await tx.query('SELECT observation_id FROM esg.observation_sources WHERE context_id=$1',[c.row.context_id])).rows.map(x=>x.observation_id);
    if(oldIds.some(id=>explicitReplacements.has(id))) c.reasons.push('explicitly_superseded');
  }
  const groups=new Map();
  for(const c of candidates.filter(c=>!c.reasons.length)) {
    if(!groups.has(c.identity_key)) groups.set(c.identity_key,[]);
    groups.get(c.identity_key).push(c);
  }
  const published=[];
  for(const group of groups.values()) {
    if(new Set(group.map(c=>c.value_key)).size>1) { for(const c of group) c.reasons.push('unresolved_source_conflict'); continue; }
    const c=group[0];
    // Supporting contexts are part of the immutable revision. Late evidence
    // creates another observation, preserving prior datasets byte for byte.
    const observation_id=uuid(stableJSON({identity:c.identity,value_key:c.value_key,contexts:group.map(x=>x.row.context_id).sort(),
      normalization_rule_version:NORMALIZATION_RULE_VERSION,normalization_rule_digest:UNIT_RULE_DIGEST}));
    const comparisonReasons=[...new Set(group.flatMap(x=>x.comparison_reasons))];
    await tx.query(`INSERT INTO esg.curated_observations(observation_id,metric_id,definition_version,identity_key,entity_ref,period_start,period_end,boundary_code,
      value_role,measurement_method,dimensions,canonical_unit,numeric_value,lower_bound,upper_bound,precision_qualifier,statement,comparison_eligibility,comparison_reasons,normalization_rule_version)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20) ON CONFLICT DO NOTHING`,
      [observation_id,c.metric.metric_id,c.metric.definition_version,c.identity_key,c.row.entity_ref,c.row.period_start,c.row.period_end,c.row.boundary_code,
        c.row.value_role,c.row.measurement_method,c.row.dimensions,c.metric.definition.canonical_unit,c.value.numeric_value,c.value.lower_bound,c.value.upper_bound,
        c.value.precision_qualifier,c.value.statement,comparisonReasons.length?'not_comparable':'comparable',comparisonReasons,NORMALIZATION_RULE_VERSION]);
    for(const source of group) {
      source.observation_id=observation_id;
      const snapshot=(await tx.query(`SELECT d.raw_indicator_name,d.raw_disclosure_text,v.raw_value_text,v.raw_unit,q.statement_raw,
        r.source_artifact_ref,r.source_sha256,r.report_title_snapshot,c.revision AS context_revision,
        c.operator_ref AS context_reviewer,c.reason AS context_review_reason,c.reported_unit,
        (SELECT jsonb_agg(jsonb_build_object('page_number',e.page_number,'printed_page_label',e.printed_page_label,'source_node_id',e.source_node_id,'source_quote',e.source_quote,'bbox',e.bbox) ORDER BY e.source_role,e.ordinal)
          FROM esg.disclosure_sources e WHERE e.disclosure_id=d.disclosure_id) AS evidence,
        (SELECT decision_id FROM esg.review_decisions WHERE disclosure_id=d.disclosure_id ORDER BY created_at DESC,decision_id DESC LIMIT 1) AS fact_review_decision_id,
        (SELECT decision_id FROM esg.review_decisions WHERE mapping_id=c.mapping_id ORDER BY created_at DESC,decision_id DESC LIMIT 1) AS mapping_review_decision_id
        FROM esg.measurement_contexts c JOIN esg.disclosure_standard_mappings m ON m.disclosure_standard_mapping_id=c.mapping_id
        JOIN esg.esg_disclosures d USING(disclosure_id) JOIN esg.source_report_refs r USING(source_report_id)
        LEFT JOIN esg.structured_values v USING(disclosure_id) LEFT JOIN esg.qualitative_claims q USING(disclosure_id)
        WHERE c.context_id=$1`,[source.row.context_id])).rows[0];
      snapshot.unit_conversion=source.value.unit_conversion || null;
      await tx.query(`INSERT INTO esg.observation_sources(observation_id,context_id,disclosure_id,mapping_id,source_snapshot) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING`,
        [observation_id,source.row.context_id,source.row.disclosure_id,source.row.mapping_id,snapshot]);
    }
    published.push(observation_id);
  }
  const superseded=new Set(candidates.filter(c=>c.observation_id && !c.reasons.length).map(c=>c.row.supersedes_observation_id).filter(Boolean));
  const desired=published.filter(id=>!superseded.has(id)).sort();
  for(const c of candidates) {
    const prior=(await tx.query('SELECT observation_id,publication_status FROM esg.publication_states WHERE mapping_id=$1',[c.row.mapping_id])).rows[0];
    let status=c.reasons.includes('explicitly_superseded')?'superseded':c.reasons.length ? (prior?.observation_id?'withdrawn':'blocked') : superseded.has(c.observation_id)?'superseded':'published';
    if(status==='superseded') c.reasons.push('explicitly_superseded');
    await tx.query(`INSERT INTO esg.publication_states(mapping_id,fact_review_status,mapping_review_status,publication_status,reason_codes,observation_id)
      VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(mapping_id) DO UPDATE SET fact_review_status=EXCLUDED.fact_review_status,
      mapping_review_status=EXCLUDED.mapping_review_status,publication_status=EXCLUDED.publication_status,reason_codes=EXCLUDED.reason_codes,
      observation_id=EXCLUDED.observation_id,last_evaluated_at=CURRENT_TIMESTAMP`,
      [c.row.mapping_id,c.row.quality_status,c.row.review_status,status,c.reasons,c.observation_id || prior?.observation_id || null]);
  }
  const contentDigest=digest(stableJSON({dictionary_version:dictionaryVersion,observations:desired}));
  const latest=(await tx.query('SELECT dataset_version,content_digest FROM esg.dataset_versions ORDER BY dataset_version DESC LIMIT 1')).rows[0];
  let version=latest.dataset_version;
  if(latest.content_digest!==contentDigest) {
    version=(await tx.query('INSERT INTO esg.dataset_versions(dictionary_version,content_digest) VALUES($1,$2) RETURNING dataset_version',[dictionaryVersion,contentDigest])).rows[0].dataset_version;
    for(const id of desired) await tx.query('INSERT INTO esg.dataset_membership(dataset_version,observation_id) VALUES($1,$2)',[version,id]);
    await tx.query('UPDATE esg.dataset_versions SET sealed=true WHERE dataset_version=$1',[version]);
  }
  return {dataset_version:Number(version),published:desired.length,blocked:candidates.filter(c=>c.reasons.length).length};
}

export async function processOutbox(db,{limit=100}={}) {
  if(!Number.isInteger(limit) || limit<1 || limit>1000) throw new Error('Outbox limit must be 1–1000');
  try {
    return await db.transaction(async tx=>{
      // A singleton row provides portable serialisation in PostgreSQL/PGlite.
      await tx.query('SELECT singleton FROM esg.publication_control FOR UPDATE');
      await registerUnitRules(tx);
      const events=(await tx.query(`SELECT event_id FROM esg.outbox_events WHERE status='pending' ORDER BY created_at,event_id LIMIT $1 FOR UPDATE`,[limit])).rows;
      if(!events.length) { const latest=(await tx.query('SELECT dataset_version FROM esg.dataset_versions ORDER BY dataset_version DESC LIMIT 1')).rows[0]; return {processed:0,dataset_version:Number(latest.dataset_version)}; }
      const result=await reconcile(tx);
      for(const {event_id} of events) await tx.query(`UPDATE esg.outbox_events SET status='processed',attempts=attempts+1,last_error=NULL,processed_at=CURRENT_TIMESTAMP WHERE event_id=$1`,[event_id]);
      return {processed:events.length,...result};
    });
  } catch(error) {
    // The failed transaction leaves decisions/events intact and retryable.
    await db.query(`UPDATE esg.outbox_events SET attempts=attempts+1,last_error=$1 WHERE event_id IN
      (SELECT event_id FROM esg.outbox_events WHERE status='pending' ORDER BY created_at,event_id LIMIT $2)`,[String(error.message).slice(0,2000),limit]);
    throw error;
  }
}

export async function readDataset(db,version=null) {
  if(version!=null && (!/^\d+$/.test(String(version)) || !Number.isSafeInteger(Number(version)))) throw new Error('Invalid dataset version');
  const dataset=(await db.query(version==null?'SELECT * FROM esg.dataset_versions WHERE sealed ORDER BY dataset_version DESC LIMIT 1':'SELECT * FROM esg.dataset_versions WHERE sealed AND dataset_version=$1',version==null?[]:[String(version)])).rows[0];
  if(!dataset) {const error=new Error('Dataset version does not exist');error.code='not_found';throw error;}
  const observations=(await db.query(`SELECT o.observation_id,o.metric_id,o.definition_version AS metric_definition_version,d.source_datapoint_id,
    d.definition->>'label_zh' AS label_zh,d.definition->>'topic' AS topic,o.entity_ref,o.period_start::text,o.period_end::text,o.boundary_code,
    o.value_role,o.measurement_method,o.dimensions,o.canonical_unit,o.numeric_value::text,o.lower_bound::text,o.upper_bound::text,
    o.precision_qualifier,o.statement,o.comparison_eligibility,o.comparison_reasons,o.normalization_rule_version,o.created_at
    FROM esg.dataset_membership m JOIN esg.curated_observations o USING(observation_id)
    JOIN esg.metric_definition_versions d ON d.metric_id=o.metric_id AND d.definition_version=o.definition_version
    WHERE m.dataset_version=$1 ORDER BY o.entity_ref,o.metric_id,o.period_end,o.observation_id`,[String(dataset.dataset_version)])).rows;
  for(const o of observations) o.sources=(await db.query(`SELECT context_id,disclosure_id,mapping_id,source_snapshot
    FROM esg.observation_sources WHERE observation_id=$1 ORDER BY context_id`,[o.observation_id])).rows.map(({source_snapshot,...identity})=>({...identity,...source_snapshot}));
  return {dataset_version:Number(dataset.dataset_version),as_of:dataset.created_at,dictionary_version:dataset.dictionary_version,
    comparison_contract_version:'esg-comparison-1.0.0',observations};
}
