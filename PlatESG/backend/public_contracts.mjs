/** Public values are built from an explicit schema, never from internal row spreads. */
import {scientificFieldKey} from '../../database/scripts/scientific-properties.mjs';
const pick = (value, keys) => Object.fromEntries(keys.filter(k => value[k] !== undefined).map(k => [k, value[k]]));
export function publicMetric(metric) {
  return pick(metric, ['metric_id','source_datapoint_id','label_zh','topic','canonical_unit','value_type',
    'clause_kind','extension_id','related_source_datapoint_ids','parent_package_id','parent_package_version','allowed_value_origins',
    'unit_dimension','description','original_name','definition_version','required_dimensions',
    'required_context_fields','comparison_conditions','comparison_dimensions','comparison_allowed_values','fixed_dimensions',
    'dimension_constraints','typed_context_constraints','value_constraints','aggregation','exclusions','comparison_mode']);
}
export function publicEntity(entity) {
  return pick(entity, ['entity_ref','name_zh','entity_kind','industry_code','country_code','is_demo','coverage_count']);
}
export function publicObservation(row, metric, publishedAt) {
  const result = pick(row, ['observation_id','entity_ref','entity_name','entity_kind','is_demo','metric_id',
    'source_datapoint_id','label_zh','topic','definition_version','period_start','period_end','boundary_code',
    'clause_kind','extension_id','related_source_datapoint_ids','parent_package_id','value_origin',
    'measurement_method','value_role','canonical_unit','value_numeric','lower_bound','upper_bound',
    'precision_qualifier','statement_text','raw_value_text','raw_unit','source_count','comparison_eligibility',
    'comparison_reasons','publication_status','dataset_version','report_version','created_at',
    'identity_evidence','source_fact_ref']);
  const axes = new Set(['entity_kind', ...(metric?.required_dimensions || []), ...(metric?.comparison_dimensions || []),
    ...Object.keys(metric?.fixed_dimensions || {}), ...Object.keys(metric?.dimension_constraints || {}),
    ...(metric?.required_context_fields || [])]);
  result.dimensions = Object.fromEntries(Object.entries(row.dimensions || {}).filter(([key]) => axes.has(key)));
  // Printed classification, kept unfiltered by the clause axes: a report's own
  // vocabulary (energy_carrier=solar) is evidence, not a contract violation.
  result.reported_dimensions = Object.fromEntries(Object.entries(row.reported_dimensions || {})
    .map(([key, values]) => [key, Array.isArray(values) ? values : [values]]));
  result.source_row_label = row.source_row_label ?? null;
  result.reported_properties = (row.reported_properties || []).map(publicReportedProperty).filter(Boolean);
  result.identity_evidence = Object.fromEntries([...new Set((row.sources || []).flatMap(source => Object.keys(source.identity_evidence || {})))].sort()
    .map(key => [key, (row.sources || []).flatMap(source => (source.identity_evidence?.[key] || []).map(evidence => ({
      ir_object_id: evidence.ir_object_id, pdf_page_index: evidence.pdf_page_index,
      evidence_role: evidence.evidence_role, raw_text: evidence.raw_text,
    })))]));
  result.source_fact_refs = [...new Set((row.sources || []).map(source => source.source_fact_ref).filter(Boolean))].sort();
  // Publication time belongs to the frozen dataset version, not to the row's
  // import time, so list and detail views agree on the same observation.
  result.published_at = publishedAt || row.created_at;
  return result;
}
export function publicReportedProperty(property) {
  if(!property || typeof property!=='object' || Array.isArray(property)
    || !scientificFieldKey(property.field_key)
    || !(typeof property.value_raw==='string' || Array.isArray(property.value_raw)&&property.value_raw.every(value=>typeof value==='string'))
    || !['text','number','boolean','text_array'].includes(property.value_type)
    || !['field','source'].includes(property.evidence_scope))return null;
  return {...pick(property,['field_key','label_zh','value_raw','value_type','unit_raw','evidence_scope','report_title','source_role','source_label']),
    evidence:(Array.isArray(property.evidence)?property.evidence:[]).map(evidence=>({
      ...pick(evidence,['page_number','source_quote']),bbox:evidence.bbox && typeof evidence.bbox==='object'
        ? pick(evidence.bbox,['x0','y0','x1','y1']):null}))};
}
export function publicEvidence(evidence, source) {
  return { ...pick(evidence, ['page_number','printed_page_label','source_quote','bbox']),
    ...pick(source, ['raw_value_text','raw_unit']),
    unit_conversion: source.unit_conversion ? pick(source.unit_conversion,
      ['source_unit','canonical_unit','multiplier','divisor']) : null };
}
export function publicDataset(row) {
  return { ...pick(row, ['dictionary_version','created_at','observation_count']),
    dataset_version: Number(row.dataset_version), published_at: row.created_at,
    reason: '经审核的发布快照' };
}
