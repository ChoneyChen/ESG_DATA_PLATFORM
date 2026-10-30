import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../runtime/frozen_schema.mjs';
import {importBundle} from '../scripts/frozen-bundle.mjs';
import {reviewRecord} from '../scripts/review-decisions.mjs';
import {seedMetricDictionary,saveMeasurementContext,processOutbox,readDataset,NORMALIZATION_RULE_VERSION} from '../scripts/publication.mjs';
import {dispatch} from '../queries/service.mjs';
import {applyPlatformMigrations} from '../runtime/connection.mjs';
import {saveExtensionProposal,recordExtensionDefinitionDecision,registerExtensionRelease,listExtensionRegistry,
  extensionContractDigest,requestExtensionDerivation,retryExtensionDerivation,reviewDerivedCandidate,
  projectDerivedCandidates,exactDecimalSum,exactUnitConversion} from '../scripts/extension-derivation.mjs';

// The registered 1.0.0 extension pins its original 2.1.1 parent. Keep that
// immutable package/operand fixture; new-parent releases need separate approval.
const dictionary=JSON.parse(await readFile(new URL('../../database/catalog/history/metric-dictionary.1.0.0.json',import.meta.url),'utf8'));
// These isolated derivation fixtures explicitly register a new scientific
// definition. Extra operand axes cannot be smuggled into the older contract.
dictionary.dictionary_version='synthetic-scientific-axes-1.1.0';
for(const definition of dictionary.metrics.filter(item=>['E1-6_07','E1-6_09','E1-6_10'].includes(item.source_datapoint_id))) {
  definition.definition_version='1.1.0';
  definition.comparison_dimensions=[...new Set([...definition.comparison_dimensions,'amount_basis','accounting_standard'])];
  definition.comparison_allowed_values={...definition.comparison_allowed_values,amount_basis:['gross']};
}
const authored=JSON.parse(await readFile(new URL('./fixtures/frozen-extension-proposal.json',import.meta.url),'utf8'));
const metric=id=>dictionary.metrics.find(m=>m.source_datapoint_id===id);
const authority={actor_ref:'synthetic_definition_reviewer',authority_roles:['approve_extension_definition']};
const targetMetricId='ghg.scope1_scope2.total';
const one=async(db,sql,params)=>(await db.query(sql,params)).rows[0];

async function dbFixture() {
  const db=await PGlite.create();await applyMigrations(db);await seedMetricDictionary(db,dictionary);return db;
}
async function approveDefinition(db) {
  const proposal={...structuredClone(authored),proposal_id:'synthetic-proposal-'+randomUUID(),proposer_kind:'human',proposer_ref:'synthetic_author'};
  const receipt={schema_version:'project-extension-approval-1.0',approval_id:randomUUID(),proposal_id:proposal.proposal_id,
    definition_digest:extensionContractDigest(proposal.definition),decision:'approved',actor_ref:authority.actor_ref,
    reason:'Explicit approval of a labeled synthetic database test fixture',decided_at:'2026-10-05T00:00:00+00:00'};
  await saveExtensionProposal(db,{proposal,submitted_by:'synthetic_author'});
  await recordExtensionDefinitionDecision(db,{receipt},authority);
  const body={schema_version:'project-extension-release-1.0',definition:proposal.definition,definition_digest:receipt.definition_digest,approval:receipt};
  const release={...body,release_digest:extensionContractDigest(body)};
  await registerExtensionRelease(db,{release});return {proposal,receipt,release};
}
function targetDefinition(release) {
  const d=release.definition;
  return {metric_id:targetMetricId,definition_version:d.definition_version,clause_kind:'project_extension',source_datapoint_id:null,
    extension_id:d.extension_id,label_zh:d.labels.zh,topic:'E1',readiness:'ready',value_type:'numeric',canonical_unit:d.canonical_unit,
    required_dimensions:['ghg_scope','aggregation_role'],comparison_dimensions:d.comparison_contract.required_axes,
    required_context_fields:[],fixed_dimensions:{},dimension_constraints:{},typed_context_constraints:{},value_role:'actual',
    standard_binding:{package_id:d.parent_package_id,package_version:d.parent_package_version,metric_id:d.extension_id,digest:d.parent_source_digest},
    value_constraints:d.value_constraints,extension_binding:{extension_id:d.extension_id,definition_version:d.definition_version,
      release_digest:release.release_digest,definition_digest:release.definition_digest,parent_package_id:d.parent_package_id,
      parent_package_version:d.parent_package_version,parent_source_digest:d.parent_source_digest}};
}
async function activateExtensionDictionary(db,release) {
  const next={...structuredClone(dictionary),dictionary_version:'synthetic-extension-dictionary-1.1.0',metrics:[...dictionary.metrics,targetDefinition(release)]};
  await seedMetricDictionary(db,next);await processOutbox(db);return next;
}
async function publishedOperand(db,id,value,{dimensions={},entity='synthetic_group',period_start='2025-01-01',period_end='2025-12-31',
  boundary='group_consolidated',unit='tCO2e',measurement_method=null,definition=null,extension_release=null,comparator='exact'}={}) {
  const def=definition || metric(id),binding=def.standard_binding,n=randomUUID();
  const bundle={root:`/synthetic/${n}`,manifest:{job_id:`synthetic-${n}`},bundleDigest:extensionContractDigest(n),
    request:{package_id:binding.package_id,package_version:binding.package_version},
    standard:{manifest:{standard:{framework_id:'esrs',framework_version:'2023-set1'}},compilation:{source_digest:binding.digest},
      metrics:[{metric_id:binding.metric_id,source_datapoint_id:def.source_datapoint_id,clause_kind:def.clause_kind || 'original_datapoint',
        extension_id:def.extension_id || null,labels:{zh:def.label_zh}}],elements:[]},
    runtimeSnapshot:null,
    records:{reporting_tasks:[],quantitative_observations:[{observation_id:`r-${n}`,metric_id:binding.metric_id,value_raw:value,value_numeric:value,unit_raw:unit,comparator}],
      qualitative_assertions:[],attribute_values:[],dimension_values:[],evidence_references:[{evidence_id:`e-${n}`,source_record_id:`r-${n}`,
        document_id:`doc-${n}`,ir_run_id:`ir-${n}`,ir_revision:1,ir_object_id:`cell-${n}`,pdf_page_index:0,evidence_excerpt:`Synthetic fixture only: ${value}`} ]}};
  if(extension_release) {
    const d=extension_release.definition,selection={extension_id:d.extension_id,definition_version:d.definition_version,
      release_digest:extension_release.release_digest,selection_kind:'custom_extension',value_origins:['reported'],derivation_rule_ids:[]};
    const body={schema_version:'runtime-standard-snapshot-1.0',parent_package_id:d.parent_package_id,
      parent_package_version:d.parent_package_version,parent_source_digest:d.parent_source_digest,
      selections:[selection],extension_releases:[extension_release]};
    bundle.runtimeSnapshot={...body,runtime_digest:extensionContractDigest(body)};
    bundle.request.extension_selections=[selection];
    const source_fact_id=`synthetic-source-${n}`,evidence=bundle.records.evidence_references[0];
    bundle.sourceFacts={facts:[{source_fact_id,source_key:`synthetic-key-${n}`,document_id:evidence.document_id,
      ir_run_id:evidence.ir_run_id,ir_revision:evidence.ir_revision,source_object_id:evidence.ir_object_id,source_row_id:`row-${n}`,
      source_row_label:def.label_zh,kind:'quantitative',value_raw:value,unit_raw:unit,identity_raw:{},
      physical_sources:[{ir_object_id:evidence.ir_object_id,pdf_page_index:0,evidence_excerpt:evidence.evidence_excerpt}],
      interpretation_version:'synthetic-fixture-v1',review_status:'candidate'}]};
    bundle.sourceMappings={core_bindings:[{core_record_id:`r-${n}`,source_fact_id,metric_id:binding.metric_id,
      match:'match',record_type:'quantitative_observation',package_id:d.parent_package_id,
      package_version:d.parent_package_version,package_digest:d.parent_source_digest,clause_kind:'project_extension',
      extension_id:d.extension_id,extension_definition_version:d.definition_version,
      extension_release_digest:extension_release.release_digest,runtime_standard_digest:bundle.runtimeSnapshot.runtime_digest}]};
  }
  const imported=await importBundle(db,bundle,{upstream_system:'synthetic_test',organization_ref:entity,report_ref:`report-${n}`,
    report_version_ref:`report-${n}-v1`,source_artifact_ref:`synthetic-${n}.pdf`});
  const item=await one(db,`SELECT i.disclosure_id,m.disclosure_standard_mapping_id AS mapping_id FROM esg.import_items i
    JOIN esg.disclosure_standard_mappings m USING(disclosure_id) WHERE i.import_batch_id=$1`,[imported.import_batch_id]);
  const context={mapping_id:item.mapping_id,entity_ref:entity,period_start,period_end,boundary_code:boundary,value_role:'actual',
    measurement_method:extension_release?(measurement_method || null):(measurement_method || def.fixed_dimensions.measurement_method || 'scope1_fuel_measurement'),
    dimensions:{entity_kind:'group',ghg_scope:def.fixed_dimensions.ghg_scope,aggregation_role:'total',amount_basis:'gross',
      gwp_basis:'IPCC_AR6_GWP100',consolidation_approach:'operational_control',accounting_standard:'GHG_Protocol',...dimensions},
    reported_unit:unit,operator_ref:'synthetic_context_reviewer',reason:'Verified labeled synthetic fixture context'};
  const saved=await saveMeasurementContext(db,context);
  await reviewRecord(db,{disclosure_id:item.disclosure_id,decision:'approved',operator_ref:'synthetic_fact_reviewer',reason:'Synthetic source checked'});
  await reviewRecord(db,{mapping_id:item.mapping_id,decision:'approved',operator_ref:'synthetic_mapping_reviewer',reason:'Synthetic semantics checked',mapping_relation:'exact',satisfaction_status:'supports'});
  await processOutbox(db);
  const source=await one(db,`SELECT o.observation_id,s.source_snapshot FROM esg.observation_sources s JOIN esg.curated_observations o USING(observation_id)
    WHERE s.context_id=$1`,[saved.context_id]);
  assert.ok(source,`Fixture ${id} was not published`);
  return {...item,context,observation_id:source.observation_id,context_id:saved.context_id,
    fact_review_decision_id:source.source_snapshot.fact_review_decision_id,mapping_review_decision_id:source.source_snapshot.mapping_review_decision_id};
}
function request(release,version,scope1,scope2,method='location',key=randomUUID()) {
  const references=(operand_id,input)=>({operand_id,observation_id:input.observation_id,context_id:input.context_id,
    fact_review_decision_id:input.fact_review_decision_id,mapping_review_decision_id:input.mapping_review_decision_id});
  return {idempotency_key:key,input_dataset_version:version,extension_id:release.definition.extension_id,
    definition_version:release.definition.definition_version,release_digest:release.release_digest,
    rule_id:`${release.definition.extension_id}.derivation.${method}`,target_metric_id:targetMetricId,target_definition_version:'1.0.0',
    operands:[references('scope1',scope1),references('scope2',scope2)]};
}
async function projection(db,dictionaryVersion) {
  const data=await readDataset(db);const ids=data.observations.filter(o=>o.value_origin!=='derived').map(o=>o.observation_id);
  return db.transaction(async tx=>{await tx.query('SELECT singleton FROM esg.publication_control FOR UPDATE');
    return projectDerivedCandidates(tx,{dictionary_version:dictionaryVersion,current_reported_observation_ids:ids});});
}

test('standards-compatible digest and arithmetic keep long values exact without floating point',()=>{
  assert.equal(extensionContractDigest(authored.definition),'f9a526b68ae13416c6b55092f94cd1d569f905bf6af6668c6aacbdd813a6f4fe');
  // Expected hashes are independently generated by Python sort_keys with LF.
  assert.equal(extensionContractDigest({'\u{10000}':'supplementary','\ue000':'bmp','中文':'label'}),
    '7bf356132499f0b05dc25370f8e75cb471e5bc853d86716f287b514adac3fcc7');
  assert.equal(extensionContractDigest({'10':'ten','2':'two'}),'fae646de8df1dd70254d339264c5b87a2f205f1bb78aa99f2fb44766074e7ccb');
  assert.equal(exactDecimalSum(['900719925474099312345.678901','0.000009','-0.001']),'900719925474099312345.67791');
  assert.equal(exactDecimalSum(['0.1','0.2']),'0.3');
  assert.equal(exactUnitConversion('12345678901234567890.123456','10000','1'),'123456789012345678901234.56');
  assert.equal(exactUnitConversion('12','1','3'),'4');
  assert.throws(()=>exactUnitConversion('1','1','3'),/Non-terminating/);
  assert.throws(()=>exactDecimalSum([0.1,'0.2']),/decimal strings/);
});

test('definition approval is bound to stored proposal, trusted actor, immutable release and active decision',async()=>{
  const db=await dbFixture();try {
    const proposal={...structuredClone(authored),proposal_id:'synthetic-'+randomUUID()};
    const receipt={schema_version:'project-extension-approval-1.0',approval_id:randomUUID(),proposal_id:proposal.proposal_id,
      definition_digest:extensionContractDigest(proposal.definition),decision:'approved',actor_ref:authority.actor_ref,reason:'Synthetic fixture approval',decided_at:'2026-10-05T00:00:00Z'};
    await assert.rejects(recordExtensionDefinitionDecision(db,{receipt},authority),/stored proposal/);
    await saveExtensionProposal(db,{proposal,submitted_by:'synthetic_author'});
    await assert.rejects(recordExtensionDefinitionDecision(db,{receipt},undefined),/Trusted/);
    await assert.rejects(recordExtensionDefinitionDecision(db,{receipt},{...authority,actor_ref:'client_impersonation'}),/actor/);
    await recordExtensionDefinitionDecision(db,{receipt},authority);
    const body={schema_version:'project-extension-release-1.0',definition:proposal.definition,definition_digest:receipt.definition_digest,approval:receipt};
    const release={...body,release_digest:extensionContractDigest(body)};
    await recordExtensionDefinitionDecision(db,{receipt:{...receipt,approval_id:randomUUID(),decision:'rejected'}},authority);
    await assert.rejects(registerExtensionRelease(db,{release}),/current approval/);
    const accepted=await approveDefinition(db);
    assert.equal((await registerExtensionRelease(db,{release:accepted.release})).idempotent,true);
    await assert.rejects(recordExtensionDefinitionDecision(db,{receipt:{...accepted.receipt,approval_id:randomUUID(),decision:'rejected'}},authority),/new version/);
    const registry=await listExtensionRegistry(db);assert.equal(registry.releases.length,1);assert.equal(registry.proposals.length,2);
    await assert.rejects(db.query('DELETE FROM esg.extension_releases'),/immutable/);
    await assert.rejects(db.query('UPDATE esg.extension_definition_decisions SET reason=$1',['tamper']),/immutable/);
  }finally{await db.close();}
});

test('extension identity never gets an official ID and old dictionary memberships remain exact',async()=>{
  const db=await dbFixture();try {
    const old=await readDataset(db),{release}=await approveDefinition(db);
    await activateExtensionDictionary(db,release);
    const rows=(await db.query('SELECT source_datapoint_id,clause_kind,extension_id FROM esg.metric_definition_versions')).rows;
    assert.equal(rows.filter(r=>r.clause_kind==='original_datapoint').length,49);
    assert.equal(rows.find(r=>r.clause_kind==='project_extension').source_datapoint_id,null);
    const members=await one(db,'SELECT count(*)::int AS n FROM esg.dictionary_membership WHERE dictionary_version=$1',[old.dictionary_version]);
    assert.equal(members.n,49);
    await assert.rejects(db.query(`INSERT INTO esg.metric_definition_versions(metric_id,definition_version,dictionary_version,source_datapoint_id,
      readiness,definition,definition_digest,clause_kind,extension_id) VALUES('forged','1.0.0','forged','E1-6_36','ready','{}',$1,'project_extension',$2)`,
      ['a'.repeat(64),release.definition.extension_id]),/metric_clause_identity_ck/);
    assert.deepEqual(await readDataset(db,old.dataset_version),old);
  }finally{await db.close();}
});

test('direct SQL rejects incomplete runtime snapshots, wrong parents and forged release or selection authority',async()=>{
  const db=await dbFixture();try {
    const {release}=await approveDefinition(db);await activateExtensionDictionary(db,release);
    const original=await publishedOperand(db,'E1-6_07','10');
    const reported=await publishedOperand(db,null,'30',{definition:targetDefinition(release),extension_release:release,
      dimensions:{ghg_scope:'scope_1_2',scope_2_method:'location_based'}});
    const emptyBatch=(await one(db,'SELECT import_batch_id FROM esg.import_items WHERE disclosure_id=$1',[original.disclosure_id])).import_batch_id;
    const saved=await one(db,`SELECT overlay.runtime_snapshot FROM esg.extension_import_snapshots overlay
      JOIN esg.import_items item USING(import_batch_id) WHERE item.disclosure_id=$1`,[reported.disclosure_id]);
    const snapshot=saved.runtime_snapshot;
    const insertSnapshot=(tx,value)=>{
      const payload=structuredClone(value);
      if(payload && typeof payload==='object' && !Array.isArray(payload) && 'runtime_digest' in payload)
        payload.runtime_digest=extensionContractDigest(Object.fromEntries(Object.entries(payload).filter(([key])=>key!=='runtime_digest')));
      return tx.query(`INSERT INTO esg.extension_import_snapshots(import_batch_id,runtime_digest,runtime_snapshot)
        VALUES($1,$2,$3::jsonb)`,[emptyBatch,payload?.runtime_digest || snapshot.runtime_digest,JSON.stringify(payload)]);
    };
    for(const key of ['schema_version','runtime_digest','parent_package_id','parent_package_version','parent_source_digest','selections','extension_releases']) {
      const missing=structuredClone(snapshot);delete missing[key];
      await assert.rejects(insertSnapshot(db,missing),/check constraint|extension import parent differs/);
    }
    for(const [key,value] of [['parent_package_id','esrs.2023-set1.e1-5'],['parent_package_version','99.0.0'],['parent_source_digest','a'.repeat(64)]]) {
      await assert.rejects(insertSnapshot(db,{...snapshot,[key]:value}),/extension import parent differs/);
    }
    for(const value of [null,[],'invalid',1,true,{...snapshot,selections:{}},{...snapshot,extension_releases:{}}])
      await assert.rejects(insertSnapshot(db,value),/check constraint|extension import parent differs/);
    await assert.rejects(db.query(`INSERT INTO esg.extension_import_snapshots(import_batch_id,runtime_digest,runtime_snapshot)
      VALUES($1,$2,$3)`,[emptyBatch,'a'.repeat(64),snapshot]),/check constraint/);
    const insertFrozenSelection=async(tx,value,overrides={})=>{
      await insertSnapshot(tx,value);
      const selection={extension_id:release.definition.extension_id,definition_version:release.definition.definition_version,
        release_digest:release.release_digest,include_reported:true,derivation_rule_ids:[],...overrides};
      await tx.query(`INSERT INTO esg.extension_import_releases(import_batch_id,extension_id,definition_version,release_digest,include_reported,derivation_rule_ids)
        VALUES($1,$2,$3,$4,$5,$6)`,[emptyBatch,selection.extension_id,selection.definition_version,selection.release_digest,
          selection.include_reported,selection.derivation_rule_ids]);
    };
    const rejectedSelection=async(value,overrides={})=>assert.rejects(db.transaction(tx=>insertFrozenSelection(tx,value,overrides)),
      /extension import release identity mismatch|extension import selection/);
    await rejectedSelection(snapshot,{release_digest:'a'.repeat(64)});
    const wrongSelection=structuredClone(snapshot);wrongSelection.selections[0].definition_version='99.0.0';
    await rejectedSelection(wrongSelection);
    await rejectedSelection(snapshot,{include_reported:false});
    const changedRelease=structuredClone(snapshot);changedRelease.extension_releases[0].approval.actor_ref='forged_actor';
    await rejectedSelection(changedRelease);
    const extendedRelease=structuredClone(snapshot);extendedRelease.extension_releases[0].unapproved_extra_field=true;
    await rejectedSelection(extendedRelease);
    const unknownRule=structuredClone(snapshot);unknownRule.selections[0].value_origins=['reported','derived'];
    unknownRule.selections[0].derivation_rule_ids=['not.a.registered.rule'];
    await rejectedSelection(unknownRule,{derivation_rule_ids:['not.a.registered.rule']});
    for(const origins of [null,['reported','unknown'],['reported',null],['reported','reported'],['derived']]) {
      const invalid=structuredClone(snapshot);invalid.selections[0].value_origins=origins;
      await rejectedSelection(invalid,{include_reported:Array.isArray(origins)&&origins.includes('reported')});
    }
    const ruleId=release.definition.derivation_rules[0].rule_id;
    for(const [origins,rules] of [[['reported'],[ruleId]],[['reported','derived'],[ruleId,ruleId]]]) {
      const invalid=structuredClone(snapshot);invalid.selections[0].value_origins=origins;invalid.selections[0].derivation_rule_ids=rules;
      await rejectedSelection(invalid,{derivation_rule_ids:rules});
    }
    assert.equal((await one(db,'SELECT count(*)::int AS n FROM esg.extension_import_snapshots WHERE import_batch_id=$1',[emptyBatch])).n,0);
    const valid=structuredClone(snapshot);valid.selections[0].value_origins=['derived'];valid.selections[0].derivation_rule_ids=[ruleId];
    await db.transaction(tx=>insertFrozenSelection(tx,valid,{include_reported:false,derivation_rule_ids:[ruleId]}));
    const accepted=await one(db,'SELECT * FROM esg.extension_import_releases WHERE import_batch_id=$1',[emptyBatch]);
    assert.equal(accepted.include_reported,false);assert.deepEqual(accepted.derivation_rule_ids,[ruleId]);
    await assert.rejects(db.query('DELETE FROM esg.extension_import_snapshots WHERE import_batch_id=$1',[emptyBatch]),/immutable/);
  }finally{await db.close();}
});

test('both methods create independent pending calculations with authoritative operand lineage and idempotent retries',async()=>{
  const db=await dbFixture();try {
    const {release}=await approveDefinition(db),next=await activateExtensionDictionary(db,release);
    const s1=await publishedOperand(db,'E1-6_07','900719925474099312345.678901'),s2l=await publishedOperand(db,'E1-6_09','0.000009'),
      s2m=await publishedOperand(db,'E1-6_10','20');
    const input=await readDataset(db),payload=request(release,input.dataset_version,s1,s2l);
    const first=await requestExtensionDerivation(db,payload,{requested_by:'synthetic_requester'});
    assert.equal(first.status,'candidate_created');assert.equal(first.candidate.review_status,'pending');
    assert.equal(first.candidate.numeric_value,'900719925474099312345.67891');
    assert.equal(first.candidate.reviewed_axes.scope_2_method,'location_based');
    const repeated=await requestExtensionDerivation(db,payload,{requested_by:'synthetic_requester'});
    assert.equal(repeated.candidate.candidate_id,first.candidate.candidate_id);
    const newKey=await requestExtensionDerivation(db,{...payload,idempotency_key:randomUUID(),operands:[...payload.operands].reverse()},{requested_by:'another_synthetic_requester'});
    assert.equal(newKey.candidate.candidate_id,first.candidate.candidate_id);
    await assert.rejects(requestExtensionDerivation(db,{...payload,rule_id:release.definition.derivation_rules[1].rule_id},{requested_by:'synthetic_requester'}),/Idempotency/);
    const market=await requestExtensionDerivation(db,request(release,input.dataset_version,s1,s2m,'market'),{requested_by:'synthetic_requester'});
    assert.notEqual(market.candidate.candidate_id,first.candidate.candidate_id);assert.equal(market.candidate.reviewed_axes.scope_2_method,'market_based');
    const lineage=(await db.query('SELECT * FROM esg.extension_operand_lineage WHERE candidate_id=$1 ORDER BY operand_id',[first.candidate.candidate_id])).rows;
    assert.equal(lineage[0].platform_metric_id,'ghg.scope1');assert.equal(lineage[0].standard_metric_id,'esrs.2023-set1.e1-6.dp07');
    assert.equal(lineage[0].fact_review_decision_id,s1.fact_review_decision_id);
    assert.equal(lineage[0].normalization_rule_version,NORMALIZATION_RULE_VERSION);assert.ok(lineage[0].normalization_rule_digest);
    assert.equal((await projection(db,next.dictionary_version)).observation_ids.length,0);
    await assert.rejects(db.query('UPDATE esg.extension_operand_lineage SET standard_metric_id=$1',['forged']),/immutable/);
    const columns=Object.keys(lineage[0]),placeholders=columns.map((_,i)=>`$${i+1}`).join(',');
    for(const tamper of [{operand_id:'undeclared_operand'},{converted_numeric_value:'999'},
      {operand_snapshot:{...lineage[0].operand_snapshot,source_snapshot:{evidence:[]}}}]) {
      const forged={...lineage[0],...tamper};
      await assert.rejects(db.query(`INSERT INTO esg.extension_operand_lineage(${columns.join(',')}) VALUES(${placeholders})`,
        columns.map(key=>forged[key])),/operand differs from frozen calculation receipt|operand lineage differs from authoritative source/);
    }
    assert.deepEqual(await readDataset(db,input.dataset_version),input);
  }finally{await db.close();}
});

test('foreign review IDs and absent dataset operands produce explicit persisted blockers without candidate fabrication',async()=>{
  const db=await dbFixture();try {
    const {release}=await approveDefinition(db);await activateExtensionDictionary(db,release);
    const before=await readDataset(db),s1=await publishedOperand(db,'E1-6_07','10'),s2=await publishedOperand(db,'E1-6_09','20'),input=await readDataset(db);
    const forged=request(release,input.dataset_version,s1,{...s2,fact_review_decision_id:s1.fact_review_decision_id});
    const blocked=await requestExtensionDerivation(db,forged,{requested_by:'synthetic_requester'});
    assert.equal(blocked.status,'blocked');assert.equal(blocked.candidate,null);
    assert.ok(blocked.reason_codes.includes('operand:scope2:fact_approval_reference_invalid'));
    const absent=await requestExtensionDerivation(db,request(release,before.dataset_version,s1,s2),{requested_by:'synthetic_requester'});
    assert.ok(absent.reason_codes.includes('operand:scope1:not_in_input_dataset_or_context'));
    assert.equal((await retryExtensionDerivation(db,{request_id:blocked.request_id})).candidate,null);
    assert.equal((await one(db,'SELECT count(*)::int AS n FROM esg.extension_derived_candidates')).n,0);
  }finally{await db.close();}
});

test('unknown axes, wrong scope2 method, intervals, different identities and net amount cannot produce a result',async()=>{
  for(const [id,override,expected] of [
    ['E1-6_09',{dimensions:{gwp_basis:'unknown'}},'shared_axis_mismatch_or_missing:gwp_basis'],
    ['E1-6_09',{period_start:'2024-01-01',period_end:'2024-12-31'},'shared_axis_mismatch_or_missing:period_start'],
    ['E1-6_09',{dimensions:{amount_basis:'net'}},'operand:scope2:selector_mismatch:amount_basis'],
    ['E1-6_10',{},'operand:scope2:selector_mismatch:measurement_method'],
    ['E1-6_09',{comparator:'less_than'},'operand:scope2:point_value_required'],
    ['E1-6_09',{entity:'other_synthetic_group'},'shared_axis_mismatch_or_missing:entity_ref'],
    ['E1-6_09',{boundary:'financial_control'},'shared_axis_mismatch_or_missing:boundary_code'],
  ]) {
    const db=await dbFixture();try {
      const {release}=await approveDefinition(db);await activateExtensionDictionary(db,release);
      const s1=await publishedOperand(db,'E1-6_07','10'),s2=await publishedOperand(db,id,'20',override),input=await readDataset(db);
      const result=await requestExtensionDerivation(db,request(release,input.dataset_version,s1,s2),{requested_by:'synthetic_requester'});
      assert.equal(result.candidate,null);assert.ok(result.reason_codes.includes(expected),JSON.stringify(result.reason_codes));
    }finally{await db.close();}
  }
});

test('processOutbox seals an approved derived result with operand evidence; withdrawal creates a new dataset and preserves history',async()=>{
  const db=await dbFixture();try {
    const {release}=await approveDefinition(db);await activateExtensionDictionary(db,release);
    const s1=await publishedOperand(db,'E1-6_07','10'),s2=await publishedOperand(db,'E1-6_09','20'),input=await readDataset(db);
    const created=await requestExtensionDerivation(db,request(release,input.dataset_version,s1,s2),{requested_by:'synthetic_requester'});
    const pending=await processOutbox(db);assert.equal(pending.blocked,1);
    assert.deepEqual(await readDataset(db),input);
    const decision=await reviewDerivedCandidate(db,{candidate_id:created.candidate.candidate_id,decision:'approved',operator_ref:'synthetic_calculation_reviewer',reason:'Independent review of operands and formula'});
    assert.equal((await one(db,'SELECT status FROM esg.outbox_events WHERE event_id=$1',[decision.decision_id])).status,'pending');
    const published=await processOutbox(db),frozen=await readDataset(db);
    assert.equal(frozen.dataset_version,published.dataset_version);assert.ok(frozen.dataset_version>input.dataset_version);
    const product=frozen.observations.find(o=>o.value_origin==='derived');assert.ok(product);
    assert.equal(product.source_datapoint_id,null);assert.equal(product.extension_id,release.definition.extension_id);
    assert.equal(product.calculation.input_dataset_version,input.dataset_version);assert.equal(product.calculation.output_numeric_value,'30');
    assert.equal(product.sources.length,2);assert.ok(product.sources.every(s=>s.evidence[0].source_quote.startsWith('Synthetic fixture only:')));
    assert.deepEqual(product.calculation.operands.map(o=>o.source_datapoint_id),['E1-6_07','E1-6_09']);
    assert.equal(product.sources.find(s=>s.operand_id==='scope1').fact_review_decision_id,s1.fact_review_decision_id);
    const obs=await one(db,'SELECT *,numeric_value::text FROM esg.curated_observations WHERE observation_id=$1',[product.observation_id]);
    assert.equal(obs.value_origin,'derived');assert.equal(obs.numeric_value,'30');assert.equal(obs.dimensions.scope_2_method,'location_based');
    assert.equal((await one(db,'SELECT count(*)::int AS n FROM esg.observation_sources WHERE observation_id=$1',[obs.observation_id])).n,0);
    const receipt=await one(db,'SELECT * FROM esg.derived_observation_lineage WHERE observation_id=$1',[obs.observation_id]);
    assert.equal(receipt.calculation_review_decision_id,decision.decision_id);assert.equal(receipt.lineage_snapshot.operands.length,2);
    assert.equal((await processOutbox(db)).processed,0);assert.deepEqual(await readDataset(db),frozen);
    await reviewRecord(db,{disclosure_id:s1.disclosure_id,decision:'rejected',operator_ref:'synthetic_fact_reviewer',reason:'Source withdrawn by explicit reviewer'});
    const withdrawn=await processOutbox(db),current=await readDataset(db);
    assert.equal(withdrawn.dataset_version,current.dataset_version);assert.ok(current.dataset_version>frozen.dataset_version);
    assert.equal(current.observations.filter(o=>o.value_origin==='derived').length,0);
    const state=await one(db,'SELECT * FROM esg.derived_publication_states WHERE candidate_id=$1',[created.candidate.candidate_id]);
    assert.equal(state.publication_status,'withdrawn');assert.ok(state.reason_codes.includes('operand:scope1:not_currently_published'));
    assert.deepEqual(await readDataset(db,input.dataset_version),input);
    assert.deepEqual(await readDataset(db,frozen.dataset_version),frozen);
    assert.equal((await one(db,'SELECT numeric_value::text AS value FROM esg.curated_observations WHERE observation_id=$1',[obs.observation_id])).value,'30');
  }finally{await db.close();}
});

test('reported and derived conflicts block computation; equal decimals retain both origins and unknown methods stay distinct',async()=>{
  const db=await dbFixture();try {
    await applyPlatformMigrations(db);
    await dispatch(db,'register_entity',{entity_ref:'synthetic_group',name_zh:'明确标注的合成主体',entity_kind:'group',industry_code:'synthetic',country_code:'CN',is_demo:true});
    const {release}=await approveDefinition(db);await activateExtensionDictionary(db,release);
    const s1=await publishedOperand(db,'E1-6_07','10'),s2=await publishedOperand(db,'E1-6_09','20'),input=await readDataset(db);
    const created=await requestExtensionDerivation(db,request(release,input.dataset_version,s1,s2),{requested_by:'synthetic_requester'});
    await reviewDerivedCandidate(db,{candidate_id:created.candidate.candidate_id,decision:'approved',operator_ref:'synthetic_calculation_reviewer',reason:'Independent synthetic calculation review'});
    await processOutbox(db);const frozen=await readDataset(db),definition=targetDefinition(release);
    const reportedOptions={definition,extension_release:release,dimensions:{ghg_scope:'scope_1_2',scope_2_method:'location_based'}};
    const conflicting=await publishedOperand(db,null,'31',reportedOptions),blocked=await readDataset(db);
    assert.equal(blocked.observations.filter(o=>o.metric_id===targetMetricId && o.value_origin==='derived').length,0);
    assert.equal(blocked.observations.find(o=>o.observation_id===conflicting.observation_id).numeric_value,'31');
    const state=await one(db,'SELECT * FROM esg.derived_publication_states WHERE candidate_id=$1',[created.candidate.candidate_id]);
    assert.ok(state.reason_codes.includes('unresolved_reported_derived_conflict'));assert.equal(state.publication_status,'withdrawn');
    const conflictComparison=await dispatch(db,'comparisons',{metric_id:targetMetricId,entity_refs:['synthetic_group'],dataset_version:blocked.dataset_version,dimensions:{}});
    assert.deepEqual(conflictComparison.equivalent_alternatives,[]);assert.equal(conflictComparison.rows[0].value_numeric,'31');
    await reviewRecord(db,{disclosure_id:conflicting.disclosure_id,decision:'rejected',operator_ref:'synthetic_fact_reviewer',reason:'Resolve synthetic source conflict'});
    await processOutbox(db);
    const equal=await publishedOperand(db,null,'30.0',reportedOptions),equalData=await readDataset(db);
    const twins=equalData.observations.filter(o=>o.metric_id===targetMetricId);
    assert.equal(twins.length,2);assert.deepEqual(twins.map(o=>o.value_origin).sort(),['derived','reported']);
    assert.equal(exactDecimalSum([twins.find(o=>o.observation_id===equal.observation_id).numeric_value]),'30');
    const comparable=await dispatch(db,'comparisons',{metric_id:targetMetricId,entity_refs:['synthetic_group'],dataset_version:equalData.dataset_version,dimensions:{}});
    assert.equal(comparable.rows.length,1);assert.equal(comparable.rows[0].observation_id,equal.observation_id);
    assert.equal(comparable.rows[0].value_origin,'reported');assert.equal(comparable.excluded.length,0);
    assert.equal(comparable.equivalent_alternatives.length,1);
    assert.equal(comparable.equivalent_alternatives[0].selected_observation_id,equal.observation_id);
    assert.equal(comparable.equivalent_alternatives[0].alternative_observation_id,twins.find(o=>o.value_origin==='derived').observation_id);
    await publishedOperand(db,null,'19',{...reportedOptions,dimensions:{ghg_scope:'scope_1_2',scope_2_method:'unknown'}});
    const separate=await readDataset(db);assert.equal(separate.observations.filter(o=>o.metric_id===targetMetricId).length,3);
    assert.equal(separate.observations.filter(o=>o.metric_id===targetMetricId && o.value_origin==='derived').length,1);
    const unknownExcluded=await dispatch(db,'comparisons',{metric_id:targetMetricId,entity_refs:['synthetic_group'],dataset_version:separate.dataset_version,dimensions:{}});
    assert.equal(unknownExcluded.rows.length,1);assert.equal(unknownExcluded.equivalent_alternatives.length,1);
    assert.ok(unknownExcluded.excluded.some(o=>o.reasons.includes('missing_comparison_dimension:scope_2_method')));
    await publishedOperand(db,null,'30',{...reportedOptions,dimensions:{ghg_scope:'scope_1_2',scope_2_method:'market_based'}});
    const distinctMethod=await readDataset(db),mixed=await dispatch(db,'comparisons',{
      metric_id:targetMetricId,entity_refs:['synthetic_group'],dataset_version:distinctMethod.dataset_version,dimensions:{}});
    assert.equal(mixed.rows.length,0);assert.equal(mixed.available_cohorts.length,2);assert.deepEqual(mixed.equivalent_alternatives,[]);
    assert.ok(mixed.excluded.some(o=>o.reasons.includes('comparison_contract_required:multiple_cohorts')));
    assert.deepEqual(await readDataset(db,frozen.dataset_version),frozen);
  }finally{await db.close();}
});

test('failed dataset publication rolls back derived rows, membership and event consumption and can be retried',async()=>{
  const db=await dbFixture();try {
    const {release}=await approveDefinition(db);await activateExtensionDictionary(db,release);
    const s1=await publishedOperand(db,'E1-6_07','10'),s2=await publishedOperand(db,'E1-6_09','20'),input=await readDataset(db);
    const created=await requestExtensionDerivation(db,request(release,input.dataset_version,s1,s2),{requested_by:'synthetic_requester'});
    const decision=await reviewDerivedCandidate(db,{candidate_id:created.candidate.candidate_id,decision:'approved',operator_ref:'synthetic_calculation_reviewer',reason:'Explicit synthetic calculation approval'});
    await db.exec("CREATE FUNCTION esg.test_fail_derived_lineage() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected publication failure'; END $$; CREATE TRIGGER test_fail_derived_lineage BEFORE INSERT ON esg.derived_observation_lineage FOR EACH ROW EXECUTE FUNCTION esg.test_fail_derived_lineage();");
    await assert.rejects(processOutbox(db),/injected publication failure/);
    assert.deepEqual(await readDataset(db),input);
    assert.equal((await one(db,"SELECT count(*)::int AS n FROM esg.curated_observations WHERE value_origin='derived'")).n,0);
    const pending=await one(db,'SELECT status,attempts,last_error FROM esg.outbox_events WHERE event_id=$1',[decision.decision_id]);
    assert.equal(pending.status,'pending');assert.equal(pending.attempts,1);assert.match(pending.last_error,/injected publication failure/);
    await db.exec('DROP TRIGGER test_fail_derived_lineage ON esg.derived_observation_lineage');
    await processOutbox(db);const current=await readDataset(db);
    assert.equal(current.observations.filter(o=>o.value_origin==='derived').length,1);
    assert.equal((await one(db,'SELECT status FROM esg.outbox_events WHERE event_id=$1',[decision.decision_id])).status,'processed');
  }finally{await db.close();}
});

test('candidate approval and pending publication event commit together and failed transaction rolls both back',async()=>{
  const db=await dbFixture();try {
    const {release}=await approveDefinition(db);await activateExtensionDictionary(db,release);
    const s1=await publishedOperand(db,'E1-6_07','10'),s2=await publishedOperand(db,'E1-6_09','20'),input=await readDataset(db);
    const created=await requestExtensionDerivation(db,request(release,input.dataset_version,s1,s2),{requested_by:'synthetic_requester'});
    await db.exec("CREATE FUNCTION esg.test_reject_extension_outbox() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.event_type='extension_review' THEN RAISE EXCEPTION 'injected outbox failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER test_reject_extension_outbox BEFORE INSERT ON esg.outbox_events FOR EACH ROW EXECUTE FUNCTION esg.test_reject_extension_outbox();");
    const payload={candidate_id:created.candidate.candidate_id,decision:'approved',operator_ref:'synthetic_calculation_reviewer',reason:'Synthetic independent review'};
    await assert.rejects(reviewDerivedCandidate(db,payload),/injected outbox failure/);
    assert.equal((await one(db,'SELECT count(*)::int AS n FROM esg.extension_candidate_reviews')).n,0);
    await db.exec('DROP TRIGGER test_reject_extension_outbox ON esg.outbox_events');
    const saved=await reviewDerivedCandidate(db,payload);
    assert.equal((await one(db,'SELECT status FROM esg.outbox_events WHERE event_id=$1',[saved.decision_id])).status,'pending');
    assert.equal((await reviewDerivedCandidate(db,payload)).decision_id,saved.decision_id);
  }finally{await db.close();}
});
