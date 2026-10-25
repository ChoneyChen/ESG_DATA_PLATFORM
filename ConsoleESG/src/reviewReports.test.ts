import {describe,expect,it} from 'vitest';
import {reviewReportUnits} from './reviewReports';

describe('报告级审核单元',()=>{
 it('同一报告的候选只形成一个单元，并分开统计待审、阻塞与已发布',()=>{
  const rows=[
   {mapping_id:'m1',report_ref:'report-a',report_title_snapshot:'报告 A',organization_ref:'org:a',fact_review_status:'candidate',mapping_review_status:'review_required'},
   {mapping_id:'m2',report_ref:'report-a',report_title_snapshot:'报告 A',organization_ref:'org:a',fact_review_status:'approved',mapping_review_status:'approved',context:{entity_ref:'org:a'},publication_status:'published'},
   {mapping_id:'m3',report_ref:'report-b',report_title_snapshot:'报告 B',organization_ref:'org:b',fact_review_status:'approved',mapping_review_status:'approved',publication_status:'blocked',reason_codes:['reviewed_context_missing']},
  ];
  const units=reviewReportUnits(rows,[{report_id:'report-a',metadata:{title:'报告 A',publisher_entity_ref:'org:a'},report_code:'org:a/2025'},{report_id:'report-b',metadata:{title:'报告 B'}}]);
  expect(units.map(unit=>unit.ref)).toEqual(['report-b','report-a']);
  const a=units.find(unit=>unit.ref==='report-a')!;
  expect(a.total).toBe(2);expect(a.pending).toBe(1);expect(a.published).toBe(1);expect(a.blocked).toBe(0);expect(a.progress).toBe(50);
  const b=units.find(unit=>unit.ref==='report-b')!;
  expect(b.blocked).toBe(1);expect(b.state).toBe('blocked');expect(b.title).toBe('报告 B');
 });
});
