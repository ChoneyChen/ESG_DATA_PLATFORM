import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {applyPlatformMigrations} from '../database.mjs';
import {applyMigrations} from '../../../database/scripts/db.mjs';
import {importBundle} from '../../../database/scripts/frozen-bundle.mjs';
import {seedMetricDictionary} from '../../../database/scripts/publication.mjs';
import {publishReport,withdrawReport,deleteReport,reportPublications,readPublished} from '../../../database/scripts/publication.mjs';
import {dispatch} from '../service.mjs';
import {publicObservation} from '../public_contracts.mjs';
import {candidateProposal,machineVerify,VERIFICATION_RULE_VERSION} from '../../../database/scripts/candidate-review.mjs';
import {autoReview,AUTO_REVIEW_OPERATOR} from '../../../database/scripts/auto-review.mjs';
import {reportArtifacts,deleteReportCandidates} from '../../../database/scripts/report-artifacts.mjs';
import {processOutbox} from '../../../database/scripts/publication.mjs';
import {dictionaryFile,platformRoot} from '../service.mjs';

const requireDB=createRequire(path.join(platformRoot,'../database/package.json'));
const {PGlite}=await import(pathToFileURL(requireDB.resolve('@electric-sql/pglite')).href);
let sequence=0;

async function database() {
  const db=await PGlite.create();
  await applyMigrations(db);
  await applyPlatformMigrations(db);
  const {file_sha256,...dictionary}=await dictionaryFile();
  await seedMetricDictionary(db,dictionary);
  await db.query(`INSERT INTO plat.entities(entity_ref,name_zh,entity_kind,industry_code,country_code,metadata)
    VALUES('review-company','评审测试集团','listed_company','C39','CN','{}'::jsonb)`);
  return db;
}

async function importFact(db, metricId, {value='10', unit=null, boundary=null, reviewStatus='pending',
                                          identity:extraIdentity={}, kind='quantitative', statement=null, label=null,
                                          reportRef=null}={}) {
  const dictionary=await dictionaryFile();
  const metric=dictionary.metrics.find(m=>m.source_datapoint_id===metricId);
  const binding=metric.standard_binding,n=++sequence;
  const identity={reporting_period:'2025年度',source_row_label:label||`合成指标 ${n}`,...extraIdentity};
  if (boundary) identity.reporting_boundary=boundary;
  const isStatement=kind==='statement';
  const printed=statement||`合成陈述 ${n}`;
  const bundle={root:`/synthetic/${n}`,manifest:{job_id:`synthetic-${n}`},bundleDigest:String(n).padStart(64,'0'),
    request:{package_id:binding.package_id,package_version:binding.package_version},
    standard:{manifest:{standard:{framework_id:'esrs',framework_version:'2023-set1'}},compilation:{source_digest:binding.digest},
      metrics:[{metric_id:binding.metric_id,source_datapoint_id:metricId,labels:{zh:metric.label_zh}}],elements:[]},
    sourceMappings:{core_bindings:[{core_record_id:`r-${n}`,source_fact_id:`source-${n}`,match:'match'}]},
    sourceFacts:{facts:[{source_fact_id:`source-${n}`,kind:isStatement?'assertion':'quantitative',document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1,
      source_row_label:identity.source_row_label,value_raw:isStatement?null:value,statement_raw:isStatement?printed:null,
      unit_raw:isStatement?null:(unit||metric.canonical_unit),identity_raw:identity,quality_status:'grounded',
      review_status:'candidate',physical_sources:[{ir_object_id:`cell-${n}`,pdf_page_index:0,printed_page_label:'1',bbox:{x0:1,y0:1,x1:2,y1:2},
        document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1}]}]},
    records:{reporting_tasks:[],
      quantitative_observations:isStatement?[]:[{observation_id:`r-${n}`,metric_id:binding.metric_id,value_raw:value,value_numeric:value,
        comparator:'exact',unit_raw:unit||metric.canonical_unit,review_status:reviewStatus}],
      qualitative_assertions:isStatement?[{assertion_id:`r-${n}`,metric_id:binding.metric_id,statement_raw:printed,review_status:reviewStatus}]:[],
      attribute_values:[],dimension_values:[],
      evidence_references:[{evidence_id:`e-${n}`,source_record_id:`r-${n}`,document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1,
        ir_object_id:`cell-${n}`,pdf_page_index:0,evidence_excerpt:isStatement?printed:`合成来源值 ${value}`}]}};
  const imported=await importBundle(db,bundle,{upstream_system:'review_fixture',organization_ref:'review-company',
    report_ref:reportRef||`review-report-${n}`,report_version_ref:reportRef?`${reportRef}:asset:1`:`review-report-${n}:asset:1`,source_artifact_ref:reportRef?`pdf-${reportRef}`:`pdf-${n}`,
    report_period_start:'2025-01-01',report_period_end:'2025-12-31'});
  return (await db.query(`SELECT i.disclosure_id,m.disclosure_standard_mapping_id AS mapping_id,m.mapping_relation,m.satisfaction_status,
      d.period_start::text AS period_start,d.period_end::text AS period_end
    FROM esg.import_items i JOIN esg.disclosure_standard_mappings m USING(disclosure_id) JOIN esg.esg_disclosures d USING(disclosure_id)
    WHERE i.import_batch_id=$1`,[imported.import_batch_id])).rows[0];
}

test('a guard-accepted fact imports as an exact mapping and takes the report period', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E1-6_07');
    assert.equal(item.mapping_relation,'exact');
    assert.equal(item.satisfaction_status,'supports');
    assert.equal(item.period_start,'2025-01-01');
    assert.equal(item.period_end,'2025-12-31');
  }finally{await db.close();}
});

test('a printed narrower boundary imports as narrower, never as exact', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E1-6_07',{boundary:'part_of_group'});
    assert.equal(item.mapping_relation,'narrower');
    assert.equal(item.satisfaction_status,'partially_supports');
  }finally{await db.close();}
});

test('the proposal fills what the definition proves and names what only a person can decide', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E1-6_07');
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.rule_version,VERIFICATION_RULE_VERSION);
    assert.deepEqual(proposal.blockers,[]);
    assert.equal(proposal.resolvable,true);
    assert.equal(proposal.suggested.entity_ref,'review-company');
    assert.equal(proposal.suggested.period_start,'2025-01-01');
    assert.equal(proposal.suggested.boundary_code,'group');
    assert.equal(proposal.boundary_source,'report_identity_group');
    assert.equal(proposal.suggested.dimensions.ghg_scope,'scope_1');
    assert.equal(proposal.suggested.dimensions.aggregation_role,'total');

    const headcount=await importFact(db,'S1-6_02',{value:'100',unit:'人'});
    const stated=await candidateProposal(db,{mapping_id:headcount.mapping_id});
    assert.equal(stated.resolvable,true);
    assert.equal(stated.suggested.dimensions.statistical_time_basis,'not_stated');
    assert.ok(stated.unstated_dimensions.includes('statistical_time_basis'));
  }finally{await db.close();}
});

test('machine verification approves only what every requirement proves, and publication stays explicit', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E1-6_07');
    const dry=await machineVerify(db,{mapping_ids:[item.mapping_id],apply:false,operator_ref:'review-operator'});
    assert.equal(dry.applied,0);
    assert.equal(dry.items[0].dry_run,true);
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.measurement_contexts')).rows[0].n,0);

    const applied=await machineVerify(db,{mapping_ids:[item.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(applied.applied,1);
    const row=(await db.query(`SELECT d.quality_status,m.review_status,m.mapping_relation,m.satisfaction_status,
      (SELECT count(*)::int FROM esg.measurement_contexts c WHERE c.mapping_id=m.disclosure_standard_mapping_id) AS contexts
      FROM esg.esg_disclosures d JOIN esg.disclosure_standard_mappings m USING(disclosure_id) WHERE m.disclosure_standard_mapping_id=$1`,
      [item.mapping_id])).rows[0];
    assert.equal(row.quality_status,'approved');
    assert.equal(row.review_status,'approved');
    assert.equal(row.mapping_relation,'exact');
    assert.equal(row.contexts,1);
    const decision=(await db.query(`SELECT reason FROM esg.review_decisions
      WHERE disclosure_id=$1 AND decision='approved' ORDER BY created_at LIMIT 1`,[item.disclosure_id])).rows[0];
    assert.match(decision.reason,new RegExp(VERIFICATION_RULE_VERSION));
    // Verification approves the candidate but never publishes a dataset itself.
    assert.equal((await db.query('SELECT count(*)::int AS n FROM esg.curated_observations')).rows[0].n,0);
  }finally{await db.close();}
});

test('machine verification leaves an unstated concept axis for a person', async()=>{
  const db=await database();try{
    const narrower=await importFact(db,'E1-6_07',{boundary:'part_of_group'});
    const first=await machineVerify(db,{mapping_ids:[narrower.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(first.applied,0);
    assert.ok(first.items[0].blockers.includes('mapping_exact'));

    const unlabelled=await importFact(db,'E2-4_02',{value:'10',unit:'吨'});
    const second=await machineVerify(db,{mapping_ids:[unlabelled.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(second.applied,0);
    assert.ok(second.items[0].blockers.includes('dimensions_complete'));
    assert.ok(second.items[0].proposal.missing.includes('pollutant'));
  }finally{await db.close();}
});

test('the pollutant the extraction read becomes the context axis, not a human question', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E2-4_02',{value:'1530.14',unit:'吨',identity:{pollutant:'氮氧化物（NOx）'}});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.dimensions.pollutant,'nox');
    assert.equal(proposal.suggested.dimensions.pollution_medium,'air');
    assert.ok(['source_identity','extraction_dimension_raw'].includes(proposal.dimension_sources.pollutant));
    assert.deepEqual(proposal.blockers,[]);
    const applied=await machineVerify(db,{mapping_ids:[item.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(applied.applied,1);
    const context=(await db.query('SELECT dimensions FROM esg.measurement_contexts WHERE mapping_id=$1',[item.mapping_id])).rows[0];
    assert.equal(context.dimensions.pollutant,'nox');
  }finally{await db.close();}
});

test('a row label alone can resolve the pollutant when the identity is silent', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E2-4_02',{value:'5856.94',unit:'吨',label:'二氧化硫（SO2）排放量'});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.dimensions.pollutant,'so2');
    assert.equal(proposal.dimension_sources.pollutant,'row_label');
    assert.equal(proposal.resolvable,true);
  }finally{await db.close();}
});

test('headcount axes resolve from the metric and row labels', async()=>{
  const db=await database();try{
    const item=await importFact(db,'S1-6_02',{value:'5895',unit:'人',label:'员工总数（年末）'});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.dimensions.population,'employees');
    assert.equal(proposal.suggested.dimensions.statistical_time_basis,'period_end');
    assert.equal(proposal.resolvable,true);
  }finally{await db.close();}
});

test('evidence that contradicts a definitional axis still needs a person', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E5-5_15',{value:'100',unit:'吨',label:'一般废弃物总量',
      identity:{hazard_classification:'一般废弃物'}});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.dimensions.hazard_classification,'hazardous');
    assert.ok(proposal.fixed_dimension_mismatch.includes('hazard_classification'));
    assert.ok(proposal.blockers.includes('dimensions_complete'));
  }finally{await db.close();}
});

test('definitional axes come from the metric, not from a reviewer', async()=>{
  const db=await database();try{
    const scope3=await importFact(db,'E1-6_11',{value:'111151357.75',unit:'吨二氧化碳当量',label:'范围三排放总量'});
    const total=await candidateProposal(db,{mapping_id:scope3.mapping_id});
    assert.equal(total.suggested.dimensions.scope3_category,'total');
    assert.equal(total.suggested.dimensions.aggregation_role,'total');
    assert.equal(total.dimension_sources.scope3_category,'definition_fixed');
    assert.equal(total.resolvable,true,JSON.stringify({blockers:total.blockers,missing:total.missing,mismatch:total.mismatched_dimensions}));

    const waste=await importFact(db,'E5-5_15',{value:'31711',unit:'吨',label:'危险废弃物生成总量'});
    const hazardous=await candidateProposal(db,{mapping_id:waste.mapping_id});
    assert.equal(hazardous.suggested.dimensions.hazard_classification,'hazardous');
    assert.equal(hazardous.suggested.dimensions.waste_scope,'total');
    assert.deepEqual(hazardous.blockers,[]);

    const board=await importFact(db,'GOV-1_06',{value:'22.22',unit:'%',label:'女性董事占比'});
    const governance=await candidateProposal(db,{mapping_id:board.mapping_id});
    assert.equal(governance.suggested.dimensions.gender,'female');
    assert.equal(governance.suggested.dimensions.board_definition,'board_of_directors');
    assert.equal(governance.resolvable,true);
  }finally{await db.close();}
});

test('a report component does not invent an undeclared scientific aggregation axis', async()=>{
  const db=await database();try{
    const item=await importFact(db,'S1-13_04',{value:'68.04',unit:'小时',label:'员工职业培训平均时长',
      identity:{component_其中_男性培训人均时长:'68.95 小时'}});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.dimensions.aggregation_role,undefined);
    assert.deepEqual(proposal.blockers,[]);
    const applied=await machineVerify(db,{mapping_ids:[item.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(applied.applied,1);
  }finally{await db.close();}
});

test('fixed axes and canonical projection codes are not replaced by source free text', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E1-6_07',{value:'100',unit:'吨二氧化碳当量',
      identity:{aggregation_role:'total'}});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.dimensions.aggregation_role,'total');
    assert.ok(['definition_fixed', 'extraction_dimension_raw'].includes(proposal.dimension_sources.aggregation_role));
    assert.deepEqual(proposal.blockers,[]);
    const applied=await machineVerify(db,{mapping_ids:[item.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(applied.applied,1);
  }finally{await db.close();}
});

test('consolidation approach from the source completes a total-emissions context', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E1-6_12',{value:'1751599.73',unit:'吨二氧化碳当量',
      identity:{consolidation_approach:'运营控制'}});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.dimensions.consolidation_approach,'operational_control');
    assert.equal(proposal.suggested.dimensions.aggregation_role,'total');
    assert.ok(['source_identity','extraction_dimension_raw'].includes(proposal.dimension_sources.consolidation_approach));
    assert.deepEqual(proposal.blockers,[]);
  }finally{await db.close();}
});

test('a statement records unstated axes instead of blocking a readable disclosure', async()=>{
  const db=await database();try{
    const item=await importFact(db,'S2-1_05',{kind:'statement',
      statement:'本集团要求所有供应商禁止使用强迫劳动和童工，并定期开展审计。'});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.value_type,'statement');
    assert.equal(proposal.suggested.dimensions.policy_scope,'not_stated');
    assert.equal(proposal.suggested.dimensions.worker_population,'not_stated');
    assert.deepEqual(proposal.unstated_dimensions.sort(),['policy_scope','worker_population']);
    assert.deepEqual(proposal.blockers,[]);
    const applied=await machineVerify(db,{mapping_ids:[item.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(applied.applied,1);
    const context=(await db.query('SELECT dimensions FROM esg.measurement_contexts WHERE mapping_id=$1',[item.mapping_id])).rows[0];
    assert.equal(context.dimensions.policy_scope,'not_stated');
    assert.match(applied.items[0].proposal.rule_version,new RegExp(VERIFICATION_RULE_VERSION));
  }finally{await db.close();}
});

test('an axis that contradicts a closed definition still needs a person', async()=>{
  const db=await database();try{
    const item=await importFact(db,'E1-6_09',{value:'7977357.46',unit:'吨二氧化碳当量',identity:{method:'市场法'}});
    const proposal=await candidateProposal(db,{mapping_id:item.mapping_id});
    assert.equal(proposal.suggested.measurement_method,'market_based');
    assert.ok([...proposal.fixed_dimension_mismatch, ...proposal.mismatched_dimensions].includes('measurement_method'));
    assert.ok(proposal.blockers.includes('dimensions_complete'));
    const applied=await machineVerify(db,{mapping_ids:[item.mapping_id],apply:true,operator_ref:'review-operator'});
    assert.equal(applied.applied,0);
  }finally{await db.close();}
});

test('automatic review approves only evidence-proven candidates and stays report-scoped', async()=>{
  const db=await database();try{
    const exact=await importFact(db,'E1-6_07',{reportRef:'report-auto-a'});
    const blocked=await importFact(db,'E2-4_02',{value:'10',unit:'吨',reportRef:'report-auto-b'});
    const first=await autoReview(db,{report_ref:'report-auto-a',apply:true});
    assert.equal(first.applied,1);
    assert.equal(first.needs_human,0);
    const fact=(await db.query('SELECT quality_status FROM esg.esg_disclosures WHERE disclosure_id=$1',[exact.disclosure_id])).rows[0];
    assert.equal(fact.quality_status,'approved');
    const other=(await db.query('SELECT quality_status FROM esg.esg_disclosures WHERE disclosure_id=$1',[blocked.disclosure_id])).rows[0];
    assert.equal(other.quality_status,'candidate');
    const operator=(await db.query('SELECT operator_ref FROM esg.review_decisions WHERE disclosure_id=$1 ORDER BY created_at LIMIT 1',
      [exact.disclosure_id])).rows[0];
    assert.equal(operator.operator_ref,AUTO_REVIEW_OPERATOR);

    const second=await autoReview(db,{report_ref:'report-auto-b',apply:true});
    assert.equal(second.applied,0);
    assert.equal(second.needs_human,1);
    assert.equal(second.blockers.dimensions_complete,1);
  }finally{await db.close();}
});

test('report artifacts locate the candidate layer and delete only unpublished rows without a context', async()=>{
  const db=await database();try{
    const approved=await importFact(db,'E1-6_07',{reportRef:'report-artifacts'});
    const pending=await importFact(db,'S1-6_02',{value:'100',unit:'人',reportRef:'report-artifacts'});
    await machineVerify(db,{mapping_ids:[approved.mapping_id],apply:true,operator_ref:'review-operator'});
    await processOutbox(db,{limit:100});
    const inventory=await reportArtifacts(db,{report_ref:'report-artifacts'});
    assert.equal(inventory.counts.candidates,2);
    assert.equal(inventory.counts.deletable,1);
    assert.equal(inventory.counts.blocked,1);
    assert.ok(inventory.extraction_batches.length>=1);
    assert.ok(inventory.extraction_batches[0].bundle_ref);
    const blocked=inventory.candidates.find(item=>item.mapping_id===approved.mapping_id);
    assert.equal(blocked.deletion_blocker,'published_observation');
    const dry=await deleteReportCandidates(db,{report_ref:'report-artifacts'});
    assert.equal(dry.applied,false);
    assert.equal(dry.deletable,1);
    const deleted=await deleteReportCandidates(db,{report_ref:'report-artifacts',apply:true,
      operator_ref:'admin',reason:'删除未进入口径的候选'});
    assert.equal(deleted.deleted,1);
    const after=await reportArtifacts(db,{report_ref:'report-artifacts'});
    assert.equal(after.counts.candidates,1);
    assert.equal(after.candidates[0].mapping_id,approved.mapping_id);
    assert.ok((await db.query('SELECT 1 FROM esg.esg_disclosures WHERE disclosure_id=$1',[pending.disclosure_id])).rows.length===0);
  }finally{await db.close();}
});

test('unit aliases resolve without guessing the metric meaning', async()=>{
  const db=await database();try{
    const kilograms=await importFact(db,'E2-4_02',{value:'102879',unit:'Kg',label:'二氧化硫（SO2）排放量'});
    const proposal=await candidateProposal(db,{mapping_id:kilograms.mapping_id});
    assert.deepEqual(proposal.blockers,[],JSON.stringify(proposal.blockers));

    const intensity=await importFact(db,'E1-6_30',{value:'25.34',unit:'吨二氧化碳当量/百万元营收',
      label:'温室气体排放强度（按营业收入）',identity:{consolidation_approach:'运营控制'}});
    const intensityProposal=await candidateProposal(db,{mapping_id:intensity.mapping_id});
    assert.equal(intensityProposal.suggested.dimensions.intensity_denominator,'revenue');
    assert.deepEqual(intensityProposal.blockers,[],JSON.stringify({blockers:intensityProposal.blockers,missing:intensityProposal.missing}));
  }finally{await db.close();}
});


test('publication is report-scoped: publish, withdraw and delete one report without touching another', async()=>{
  const db=await database();try{
    const first=await importFact(db,'E1-6_07',{reportRef:'report-public-a'});
    const second=await importFact(db,'E1-5_01',{value:'20',unit:'MWh',reportRef:'report-public-b'});
    await machineVerify(db,{mapping_ids:[first.mapping_id,second.mapping_id],apply:true,operator_ref:'review-operator'});

    const publishedA=await publishReport(db,{report_ref:'report-public-a',operator_ref:'publisher',reason:'publish A'});
    assert.equal(publishedA.status,'published');
    assert.equal(publishedA.publication_version,1);
    const afterA=await readPublished(db);
    assert.equal(afterA.observations.length,1);
    assert.equal(afterA.observations[0].entity_ref,'review-company');
    const sourceRef=(await db.query('SELECT source_fact_ref FROM esg.esg_disclosures WHERE disclosure_id=$1',[first.disclosure_id])).rows[0].source_fact_ref;
    assert.ok(sourceRef);
    assert.ok(afterA.observations[0].sources[0].source_fact_ref);
    const publicData=await dispatch(db,'detail',{observation_id:afterA.observations[0].observation_id});
    assert.deepEqual(publicData.source_fact_refs,[sourceRef]);
    const publicRow=publicObservation(afterA.observations[0],afterA.observations[0],afterA.as_of);
    assert.deepEqual(publicRow.source_fact_refs,[sourceRef]);
    assert.ok(Array.isArray(publicRow.identity_evidence.period || []));
    assert.equal(typeof publicRow.identity_evidence,'object');

    const list=await reportPublications(db);
    assert.equal(list.items.find(item=>item.report_ref==='report-public-a').status,'published');
    assert.equal(list.items.find(item=>item.report_ref==='report-public-b').status,'draft');

    await withdrawReport(db,{report_ref:'report-public-a',operator_ref:'publisher',reason:'withdraw A'});
    assert.equal((await readPublished(db)).observations.length,0);

    const publishedB=await publishReport(db,{report_ref:'report-public-b',operator_ref:'publisher',reason:'publish B'});
    assert.equal(publishedB.status,'published');
    const afterB=await readPublished(db);
    assert.equal(afterB.observations.length,1);

    await deleteReport(db,{report_ref:'report-public-a',operator_ref:'admin',reason:'delete A'});
    const visible=await reportPublications(db);
    assert.equal(visible.items.some(item=>item.report_ref==='report-public-a'),false);
    const all=await reportPublications(db,{include_deleted:true});
    assert.equal(all.items.find(item=>item.report_ref==='report-public-a').status,'deleted');
    assert.equal((await readPublished(db)).observations.length,1);
  }finally{await db.close();}
});
