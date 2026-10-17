/** Reviewer-selected, verbatim scientific properties from persisted sources.
 * No reviewer value, unit, evidence or comparison axis is accepted here.
 */
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.trim().length > 0;
const MAX_PROPERTIES = 64;
const MAX_TEXT = 4096;
const MAX_ARRAY = 32;
const labelSuggestions = {
  reporting_entity:'披露主体说明',reporting_boundary:'报告边界说明',boundary:'边界说明',
  reporting_period:'报告期间说明',reporting_year:'披露年份',period_start:'期间起始',period_end:'期间结束',
  measurement_method:'测量方法说明',scope2_method:'范围2方法说明',gwp_basis:'GWP依据说明',
  consolidation_approach:'合并方法说明',accounting_standard:'核算标准说明',
  pollutant:'污染物',medium:'排放介质',site:'场址',business_segment:'业务分部',
  population:'统计人群',denominator:'分母说明',additional_breakdown:'补充分组说明',
  boundary_note:'边界注释',boundary_notes:'边界注释',method_note:'方法注释',method_notes:'方法注释',
};
// Only source scientific fields are eligible. Technical identities must never
// become public properties, even if a caller supplies a scientific-looking label.
export function scientificFieldKey(key) {
  return text(key) && key.length <= 128 && /^[\p{L}\p{N}][\p{L}\p{N}_ -]*$/u.test(key)
    && !/(?:^|[_ -])(?:id|ids|uuid|ref|refs|path|file|filename|url|uri|digest|hash|version|schema|engine|provider|model|token|secret|key|job|task|span|locator|bbox|object|anchor|execution|interpretation)(?:$|[_ -])/i.test(key)
    && !/^source(?:$|[_ -])/i.test(key)
    && !['__proto__','prototype','constructor'].includes(key);
}
function rawValue(raw, {legacy=false}={}) {
  if(legacy && typeof raw === 'string' && /^[\[{]/.test(raw.trim())) {
    try {raw=JSON.parse(raw);} catch { /* ordinary source text is not JSON */ }
  }
  let value_type,value_raw;
  if(typeof raw === 'string') {value_type='text';value_raw=raw;}
  else if(typeof raw === 'number' && Number.isFinite(raw)) {value_type='number';value_raw=String(raw);}
  else if(typeof raw === 'boolean') {value_type='boolean';value_raw=String(raw);}
  else if(Array.isArray(raw) && raw.length && raw.length<=MAX_ARRAY && raw.every(value=>typeof value==='string')) {
    value_type='text_array';value_raw=[...raw];
  } else return {value_raw:null,value_type:'unsupported',reason_code:'unsupported_property_value'};
  const values=Array.isArray(value_raw)?value_raw:[value_raw];
  if(values.some(value=>!text(value)||value.length>MAX_TEXT) || values.reduce((n,value)=>n+value.length,0)>16384)
    return {value_raw:null,value_type:'unsupported',reason_code:'property_value_out_of_bounds'};
  if(values.some(value=>/^(?:file:\/\/|\/(?:Users|home|tmp|private|var)\/|[A-Z]:\\)/i.test(value)
      || /^[a-f0-9]{64}$/i.test(value) || /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value)))
    return {value_raw:null,value_type:'unsupported',reason_code:'technical_property_value'};
  return {value_raw,value_type};
}
function box(raw) {
  if(!object(raw))return null;
  const result=Object.fromEntries(['x0','y0','x1','y1'].map(key=>[key,raw[key]]));
  return Object.values(result).every(value=>typeof value==='number'&&Number.isFinite(value))
    && result.x1>=result.x0 && result.y1>=result.y0 ? result:null;
}
function sourceEvidence(sources) {
  return (Array.isArray(sources)?sources:[]).filter(source=>Number.isInteger(source.page_number)
    && source.page_number>0 && text(source.source_quote) && source.source_quote.length<=20000)
    .map(source=>({page_number:source.page_number,source_quote:source.source_quote,bbox:box(source.bbox)}));
}
const compact = value => String(value).replace(/\s+/gu,'');
function supportsValue(source, value) {
  const needle=compact(value),haystack=String(source);
  if(!needle)return false;
  const escaped=Array.from(needle,char=>char.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('\\s*');
  // Keep original word boundaries while allowing OCR whitespace inside the
  // value: "Example Group 2024" supports the name without joining it to 2024.
  // Years/counts cannot be cut from longer numbers, nor codes from other codes.
  const left=/^[+-]?\d/.test(needle)?'(?<![\\d.,+-])':/^[A-Za-z_]/.test(needle)?'(?<![A-Za-z0-9_])':'';
  const right=/\d$/.test(needle)?'(?!\\d|[.,]\\d)':/[A-Za-z_]$/.test(needle)?'(?![A-Za-z0-9_])':'';
  return new RegExp(left+escaped+right,'u').test(haystack);
}
function fieldEvidence(fact,key,value) {
  const items=fact?.identity_evidence?.[key];
  if(!Array.isArray(items))return [];
  const values=Array.isArray(value)?value:[value];
  const matched=items.filter(item=>item.evidence_contract_version==='bound-identity-evidence-v1'
    && ['source_text','column_header'].includes(item.evidence_origin)
    && text(fact.document_id) && item.source_identity?.document_id===fact.document_id
    && text(fact.ir_run_id) && item.source_identity?.ir_run_id===fact.ir_run_id
    && Number.isInteger(fact.ir_revision) && item.source_identity?.ir_revision===fact.ir_revision
    && text(item.text) && item.text.length<=20000 && Array.isArray(item.locators)
    && values.some(value=>supportsValue(item.text,value)));
  // Every selected array item must be supported before claiming field evidence.
  const located=matched.map(item=>({...item,locators:item.locators.filter(locator=>Number.isInteger(locator.pdf_page_index)
    && locator.pdf_page_index>=0 && text(locator.ir_object_id)
    && (locator.document_id==null || locator.document_id===fact.document_id)
    && (locator.ir_run_id==null || locator.ir_run_id===fact.ir_run_id)
    && (locator.ir_revision==null || locator.ir_revision===fact.ir_revision))})).filter(item=>item.locators.length);
  if(!values.every(value=>located.some(item=>supportsValue(item.text,value))))return [];
  return located.flatMap(item=>item.locators
    .map(locator=>({page_number:locator.pdf_page_index+1,source_quote:item.text,
      bbox:box(locator.bbox ?? {x0:locator.bbox_x0,y0:locator.bbox_y0,x1:locator.bbox_x1,y1:locator.bbox_y1})})));
}
function uniqueEvidence(evidence) {
  const seen=new Set();
  return evidence.filter(item=>{const key=JSON.stringify(item);if(seen.has(key))return false;seen.add(key);return true;});
}
/** Internal candidates expose these selectors, never free-form source paths. */
export function availableReportedProperties({source_payload={},dimensions=[],sources=[]}={}) {
  const fact=object(source_payload.source_fact)?source_payload.source_fact:null;
  const physical=sourceEvidence(sources);
  const fields=fact && object(fact.identity_raw)
    ? Object.entries(fact.identity_raw).map(([key,raw])=>({key,raw,ordinal:1,source_field:`identity_raw.${key}`}))
    : (Array.isArray(dimensions)?dimensions:[]).map(item=>({key:item.dimension_key,raw:item.raw_value,
        ordinal:item.ordinal,source_field:`legacy_dimensions.${item.dimension_key}`,legacy:true}));
  if(!fact) for(const key of ['reporting_period_raw','reporting_boundary'])
    if(source_payload[key]!=null && !fields.some(item=>item.key===key))
      fields.push({key,raw:source_payload[key],ordinal:1,source_field:`legacy_payload.${key}`});
  const available=[];
  for(const item of fields) {
    if(!scientificFieldKey(item.key) || !Number.isInteger(item.ordinal)||item.ordinal<1||item.ordinal>1000)continue;
    const parsed=rawValue(item.raw,{legacy:item.legacy});
    const grounded=parsed.reason_code?[]:fieldEvidence(fact,item.key,parsed.value_raw);
    const evidence=uniqueEvidence(grounded.length?grounded:physical);
    const reason_code=parsed.reason_code || (!physical.length?'physical_source_missing':evidence.length>64?'property_evidence_out_of_bounds':null);
    available.push({source_field:item.source_field,ordinal:item.ordinal,field_key:item.key,
      label_suggestion:labelSuggestions[item.key] || item.key,...parsed,unit_raw:null,
      evidence_scope:grounded.length?'field':'source',evidence:reason_code?[]:evidence,
      selectable:!reason_code,...(reason_code?{reason_code}:{})});
  }
  return available.sort((a,b)=>a.source_field.localeCompare(b.source_field)||a.ordinal-b.ordinal);
}
export function resolveReportedProperties(source, selections=[]) {
  if(!Array.isArray(selections)||selections.length>MAX_PROPERTIES)
    throw new Error(`Property selections must be an array of at most ${MAX_PROPERTIES} entries`);
  const available=availableReportedProperties(source),seen=new Set(),selected=[];
  for(const selection of selections) {
    if(!object(selection)||Object.keys(selection).some(key=>!['source_field','ordinal','label_zh'].includes(key))
      || !text(selection.source_field)||selection.source_field.length>240
      || !text(selection.label_zh)||selection.label_zh.trim().length>160
      || /(?:file:\/\/|\/Users\/|\/tmp\/|https?:\/\/|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})/i.test(selection.label_zh))
      throw new Error('Property selection requires only source_field, ordinal and a readable label_zh; values are server-resolved');
    const ordinal=selection.ordinal ?? 1;
    if(!Number.isInteger(ordinal)||ordinal<1||ordinal>1000)throw new Error('Property ordinal must be an integer from 1 to 1000');
    const key=`${selection.source_field}:${ordinal}`;
    if(seen.has(key))throw new Error('Duplicate property selection');
    seen.add(key);
    const property=available.find(item=>item.source_field===selection.source_field && item.ordinal===ordinal);
    if(!property)throw new Error(`Property source field is unavailable or technical: ${selection.source_field}`);
    if(!property.selectable)throw new Error(`Property source cannot be selected: ${property.reason_code}`);
    selected.push({source_field:property.source_field,ordinal,field_key:property.field_key,
      label_zh:selection.label_zh.trim(),value_raw:property.value_raw,value_type:property.value_type,
      unit_raw:property.unit_raw,evidence_scope:property.evidence_scope,evidence:property.evidence});
  }
  return selected.sort((a,b)=>a.source_field.localeCompare(b.source_field)||a.ordinal-b.ordinal);
}
