// Labeled synthetic fixtures for integration and local preview only.
// This never loads or approves user reports, and is not extraction accuracy evidence.
import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { dictionaryFile, dispatch, platformRoot } from './service.mjs';
import { processOutbox } from '../../ExtractESG/esg_database/scripts/publication.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
const json = async (file, value) => { await mkdir(path.dirname(file), { recursive: true }); await writeFile(file, JSON.stringify(value, null, 2)+'\n'); };
// Keep the displayed energy subtotal and disclosed ratio consistent without
// passing decimal fixture values through floating point arithmetic.
const renewableEnergy = total => {
  const [whole,fraction='']=total.split('.');
  const scale=fraction.length+5;
  const digits=(BigInt(whole+fraction)*25125n).toString().padStart(scale+1,'0');
  return `${digits.slice(0,-scale)}.${digits.slice(-scale)}`.replace(/0+$/,'').replace(/\.$/,'');
};
const values = { energy_accounting_basis:'demo_energy_accounting_v1',gwp_basis:'demo_100_year_gwp',consolidation_approach:'operational_control',
  aggregation_role:'total',scope3_category:'total',category_coverage:'all_15_categories',estimation_method:'demo_estimate_v1',
  pollutant_definition:'demo_pollutant_definition_v1',release_boundary:'own_operations',substance:'demo_substance',hazard_class:'demo_hazard_class',
  material_flow:'procured',classification_basis:'demo_classification_v1',water_definition:'demo_water_definition_v1',geography:'demo_risk_region',
  water_risk_class:'high',risk_classification_method:'demo_risk_method_v1' };

export function demoContext(metric, mapping_id, entity_ref, year) {
  const dimensions = Object.fromEntries([...new Set([...metric.required_dimensions,...metric.comparison_dimensions])]
    .filter(key => !['measurement_method','entity_kind'].includes(key))
    .map(key => [key,metric.fixed_dimensions?.[key] || values[key] || 'demo_defined_code']));
  for (const [key,value] of Object.entries(metric.fixed_dimensions || {})) if (key !== 'measurement_method') dimensions[key]=value;
  if (dimensions.pollution_medium) dimensions.pollutant=dimensions.pollution_medium==='air'?'SO2':'COD';
  return { mapping_id,entity_ref,period_start:`${year}-01-01`,period_end:`${year}-12-31`,boundary_code:'own_operations',
    value_role:metric.value_role,measurement_method:metric.fixed_dimensions?.measurement_method || 'demo_measurement_v1',dimensions,
    reported_unit:metric.canonical_unit==='ratio'?'%':metric.canonical_unit,
    operator_ref:'synthetic-fixture-reviewer',reason:'合成测试事实：明确主体、期间、单位及口径，仅用于验证软件链路' };
}

export async function buildDemoBundles(root, { entities=['demo-alpha','demo-beta'], years=[2024,2025] } = {}) {
  const dictionary = await dictionaryFile();
  const ready = dictionary.metrics.filter(m=>m.readiness==='ready');
  const routes = new Map();
  for (const metric of ready) {
    const id=metric.standard_binding.package_id;
    if(!routes.has(id)) routes.set(id,[]);
    routes.get(id).push(metric);
  }
  const deliveries=[];
  for(const entity_ref of entities) for(const year of years) for(const [package_id, metrics] of routes) {
    const package_version=metrics[0].standard_binding.package_version;
    const {readFile}=await import('node:fs/promises');
    const standard=JSON.parse(await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/dist',package_id,package_version,'package.json'),'utf8'));
    const bundleRoot=path.join(root,`${entity_ref}-${year}`,package_id);
    const jobId=`synthetic-${entity_ref}-${year}-${package_id}`;
    const document_id=`synthetic-report-${entity_ref}-${year}`;
    const request={ir_run_id:`synthetic-ir-${entity_ref}-${year}`,package_id,package_version,
      metric_ids:metrics.map(m=>m.standard_binding.metric_id)};
    const records={reporting_tasks:[],quantitative_observations:[],qualitative_assertions:[],attribute_values:[],dimension_values:[],evidence_references:[]};
    const facts=[],bindings=[];
    for(const [index,metric] of metrics.entries()) {
      const recordId=`synthetic-record-${metric.source_datapoint_id}`;
      const sourceId=`synthetic-source-${metric.source_datapoint_id}`;
      const total=entity_ref==='demo-alpha'?(year===2024?'1000.125':'1100.375'):(year===2024?'950.250':'1050.500');
      const rawValue=metric.value_type==='statement'?'合成方法说明：以虚构监测流程验证定性证据展示，不对应真实企业。':
        metric.canonical_unit==='ratio'?'25.125':metric.source_datapoint_id==='E1-5_05'?renewableEnergy(total):total;
      const unit=metric.canonical_unit==='ratio'?'%':metric.canonical_unit;
      const common={metric_id:metric.standard_binding.metric_id,period_start:`${year}-01-01`,period_end:`${year}-12-31`};
      if(metric.value_type==='statement') records.qualitative_assertions.push({...common,assertion_id:recordId,statement_raw:rawValue});
      else records.quantitative_observations.push({...common,observation_id:recordId,value_raw:rawValue,value_numeric:rawValue,unit_raw:unit,comparator:'exact'});
      const quote=`合成测试证据（虚构）：${metric.source_datapoint_id} ${metric.label_zh}，${rawValue} ${unit || ''}，${year}年。`;
      records.evidence_references.push({evidence_id:`synthetic-evidence-${index}`,source_record_id:recordId,document_id,
        ir_run_id:request.ir_run_id,ir_revision:1,ir_object_id:`synthetic-cell-${metric.source_datapoint_id}`,pdf_page_index:index,
        evidence_excerpt:quote,quote_sha256:sha(quote.replace(/\s+/gu,' ').trim()),bbox_x0:0,bbox_y0:0,bbox_x1:100,bbox_y1:100});
      facts.push({source_fact_id:sourceId,source_key:sourceId,document_id,ir_run_id:request.ir_run_id,ir_revision:1,
        source_object_id:`synthetic-cell-${metric.source_datapoint_id}`,source_row_label:metric.label_zh,kind:metric.value_type==='statement'?'assertion':'quantitative',
        ...(metric.value_type==='statement'?{statement_raw:rawValue}:{value_raw:rawValue,unit_raw:unit}),
        identity_raw:{reporting_entity:entity_ref,period_start:`${year}-01-01`,period_end:`${year}-12-31`},
        physical_sources:[{ir_object_id:`synthetic-cell-${metric.source_datapoint_id}`,pdf_page_index:index}],interpretation_version:'synthetic-fixture-v1'});
      bindings.push({core_record_id:recordId,source_fact_id:sourceId,metric_id:metric.standard_binding.metric_id,
        package_id,package_version,package_digest:metric.standard_binding.digest});
    }
    await json(path.join(bundleRoot,'manifest.json'),{job_id:jobId,status:'completed',schema_version:'targeted-extraction-result-v1',
      bundle_layout_version:'targeted-result-bundle-v1',contract_validation:{status:'passed',basis:'synthetic-import-contract-fixture'},document:{document_id},synthetic:true});
    await json(path.join(bundleRoot,'input/request.json'),request);
    await json(path.join(bundleRoot,'input/standard-package.json'),standard);
    const layouts={reporting_tasks:'reporting-tasks',quantitative_observations:'quantitative-observations',qualitative_assertions:'qualitative-assertions',
      attribute_values:'attribute-values',dimension_values:'dimension-values',evidence_references:'evidence-references'};
    for(const [key,file] of Object.entries(layouts)) {
      await mkdir(path.join(bundleRoot,'results'),{recursive:true});
      await writeFile(path.join(bundleRoot,'results',`${file}.jsonl`),records[key].map(row=>JSON.stringify(row)).join('\n')+'\n');
    }
    await json(path.join(bundleRoot,'results/source-facts.json'),{facts});
    await json(path.join(bundleRoot,'results/metric-source-mappings.json'),{core_bindings:bindings});
    deliveries.push({bundle_path:bundleRoot,report_metadata:{upstream_system:'synthetic-demo',organization_ref:entity_ref,
      report_ref:document_id,report_version_ref:document_id,source_artifact_ref:`demo://synthetic/${document_id}`,
      source_sha256:sha(document_id),report_title:`合成测试报告（虚构）${entity_ref} ${year}`,language_code:'zh',
      report_period_start:`${year}-01-01`,report_period_end:`${year}-12-31`}});
  }
  return deliveries;
}

export async function seedDemo(db) {
  const entities=['demo-alpha','demo-beta'];
  for(const [index,entity_ref] of entities.entries()) await dispatch(db,'register_entity',{entity_ref,
    name_zh:index===0?'示例甲公司（合成）':'示例乙公司（合成）',entity_kind:'listed_company',industry_code:'demo-sector',country_code:'CN',is_demo:true});
  const root=path.join(platformRoot,'.local/demo-bundles');
  const batches=[];
  for(const delivery of await buildDemoBundles(root)) batches.push((await dispatch(db,'import',delivery)).import_batch_id);
  const dictionary=await dictionaryFile();
  const allowed=(await db.query(`SELECT DISTINCT i.disclosure_id,m.disclosure_standard_mapping_id AS mapping_id,
    s.requirement_code FROM esg.import_items i
    JOIN esg.import_batches b USING(import_batch_id) JOIN esg.source_report_refs r USING(source_report_id)
    JOIN esg.disclosure_standard_mappings m ON m.disclosure_id=i.disclosure_id
    JOIN esg.standard_requirements s USING(requirement_id) JOIN esg.framework_versions fv USING(framework_version_id)
    JOIN esg.esg_disclosures d ON d.disclosure_id=i.disclosure_id
    WHERE i.import_batch_id=ANY($1::uuid[]) AND r.upstream_system='synthetic-demo'
      AND d.source_fact_ref='synthetic-source-' || s.requirement_code
      AND fv.version_label LIKE '%' || b.package_id || '@' || b.package_version`,[batches])).rows;
  const candidates=(await dispatch(db,'candidates',{limit:1000})).items.filter(r=>allowed.some(a=>a.mapping_id===r.mapping_id));
  for(const candidate of candidates) {
    const metric=dictionary.metrics.find(m=>m.source_datapoint_id===candidate.source_datapoint_id);
    const year=new Date(candidate.period_start).getUTCFullYear();
    await dispatch(db,'review',{disclosure_id:candidate.disclosure_id,decision:'approved',operator_ref:'synthetic-fixture-reviewer',reason:'只批准明确标记的合成测试事实'});
    await dispatch(db,'review',{mapping_id:candidate.mapping_id,decision:'approved',mapping_relation:'exact',satisfaction_status:'supports',
      operator_ref:'synthetic-fixture-reviewer',reason:'合成测试的原编号及指标语义明确对应'});
    await dispatch(db,'context',demoContext(metric,candidate.mapping_id,candidate.organization_ref,year));
  }
  return {synthetic:true,...await processOutbox(db,{limit:1000}),fixture_fact_count:candidates.length};
}
