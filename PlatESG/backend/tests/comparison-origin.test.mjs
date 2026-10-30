import test from 'node:test';
import assert from 'node:assert/strict';
import {preferEquivalentOrigins} from '../../../database/queries/service.mjs';

const metric={clause_kind:'project_extension'};
const row={observation_id:'reported',entity_ref:'a',entity_name:'企业A',metric_id:'project.total',definition_version:'1.0.0',period_start:'2024-01-01',period_end:'2024-12-31',boundary_code:'group',value_role:'actual',measurement_method:null,canonical_unit:'tCO2e',dimensions:{amount_basis:'gross',scope_2_method:'location_based'},value_numeric:'9007199254740993.000001',value_origin:'reported'};
const derived={...row,observation_id:'derived',value_origin:'derived'};
test('equal extension values use reported comparison row and retain a linked calculated alternative',()=>{
  const result=preferEquivalentOrigins([derived,row],metric);
  assert.deepEqual(result.rows,[row]);assert.equal(result.alternatives[0].alternative_observation_id,'derived');
  assert.equal(result.alternatives[0].selected_observation_id,'reported');
});
test('different exact values, contexts, intervals and same-origin duplicates remain unresolved',()=>{
  for(const changed of [{value_numeric:'9007199254740993.000002'},{dimensions:{amount_basis:'gross',scope_2_method:'market_based'}},{lower_bound:'1'},{value_origin:'reported'}]) {
    const input=[row,{...derived,...changed}];assert.deepEqual(preferEquivalentOrigins(input,metric),{rows:input,alternatives:[]});
  }
  assert.deepEqual(preferEquivalentOrigins([row,derived],{clause_kind:'original_datapoint'}).rows,[row,derived]);
});
