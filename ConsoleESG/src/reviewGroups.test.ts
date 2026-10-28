import {describe,expect,it} from 'vitest';
import {groupReviewCandidates} from './reviewGroups';

const row=(codes:string[], extra:Record<string,unknown>={})=>({mapping_id:codes.join('-')||'none',reason_codes:codes,...extra});

describe('review grouping',()=>{
 it('puts a conflict above every other reason',()=>{
  const groups=groupReviewCandidates([row(['mapping_pending','consistent_value'])]);
  expect(groups.map(group=>group.key)).toEqual(['conflict']);
 });
 it('separates unit, context, mapping and fact work',()=>{
  const groups=groupReviewCandidates([
   row(['unsupported_unit']),row(['missing_boundary']),row(['mapping_not_equivalent']),row(['numeric_value_missing']),
  ]);
  expect(groups.map(group=>group.key)).toEqual(['unit','context','mapping','fact']);
  expect(groups.every(group=>group.rows.length===1)).toBe(true);
 });
 it('ignores the not-yet-evaluated publication gate and groups by the real missing step',()=>{
  const groups=groupReviewCandidates([row(['publication_not_evaluated'],{context:null,fact_review_status:'candidate'})]);
  expect(groups[0].key).toBe('fact');
 });
 it('groups by the missing review step when no reason code is recorded',()=>{
  expect(groupReviewCandidates([row([],{context:null})])[0].key).toBe('context');
  expect(groupReviewCandidates([row([],{context:{entity_ref:'x'},fact_review_status:'pending'})])[0].key).toBe('fact');
  expect(groupReviewCandidates([row([],{context:{entity_ref:'x'},fact_review_status:'approved',mapping_review_status:'review_required'})])[0].key).toBe('mapping');
 });
 it('keeps an approved candidate with a saved context out of the blocked groups',()=>{
  const groups=groupReviewCandidates([row([],{context:{entity_ref:'x'}})]);
  expect(groups[0].key).toBe('other');
 });
});
