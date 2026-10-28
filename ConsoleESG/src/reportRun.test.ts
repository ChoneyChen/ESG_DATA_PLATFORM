import {describe,expect,it} from 'vitest';
import {buildReportRuns,runCounts,stageChips,taskMatchesAsset} from './reportRun';
import type {Row} from './api';

const asset=(overrides:Row={}):Row=>({
 asset_id:'asset-1',file_name:'2025 中国石化可持续发展报告.pdf',sha256:'a'.repeat(64),
 report_id:'report-1',
 report_metadata:{title:'2025中国石化可持续发展报告',publisher_name:'中国石化',publisher_entity_ref:'org:sinopec',
  reporting_year:2025,period_start:'2025-01-01',period_end:'2025-12-31',language:'zh',report_kind:'esg'},
 ocr_versions:[{run_id:'ocr-1',status:'completed',ir_versions:[{run_id:'ir-1'}]}],
 ...overrides,
});
const session=(overrides:Row={}):Row=>({session_id:'session-1',name:'批量报告生产 · 2025中国石化可持续发展报告',
 execution_status:'finished',result_status:'completed',database_status:'imported',updated_at:'2026-10-28T10:00:00Z',
 request:{input:{asset_id:'asset-1'}},counts:{selected_metrics:34},...overrides});
const publication=(overrides:Row={}):Row=>({report_ref:'report-1',status:'draft',publication_version:0,
 candidate_count:16,approved_count:11,pending_count:5,blocked_count:0,observation_count:0,public_visible:false,...overrides});

describe('report run projection',()=>{
 it('keeps extraction available while identity is incomplete',()=>{
  const {runs}=buildReportRuns({assets:[asset({report_metadata:{title:'未登记'}})]});
  expect(runs[0].identity.state).toBe('incomplete');
  expect(runs[0].nextAction.code).toBe('extract');
 });
 it('requires identity before delivery and publishing',()=>{
  const undelivered=buildReportRuns({assets:[asset({report_metadata:{title:'未登记'}})],sessions:[session({database_status:'not_imported'})]}).runs[0];
  expect(undelivered.nextAction.code).toBe('identity');
  const approved=buildReportRuns({assets:[asset({report_metadata:{title:'未登记'}})],sessions:[session()],
   publications:[publication({approved_count:16,pending_count:0})]}).runs[0];
  expect(approved.nextAction.code).toBe('identity');
  expect(approved.blockingReason).toContain('报告身份缺');
 });
 it('reads an unread report as needing extraction',()=>{
  const {runs}=buildReportRuns({assets:[asset({ocr_versions:[]})]});
  expect(runs[0].evidence.state).toBe('none');
  expect(runs[0].nextAction.code).toBe('extract');
 });
 it('shows a running page read as evidence progress, not extraction',()=>{
  const {runs}=buildReportRuns({assets:[asset({processing_status:'running',ocr_versions:[]})]});
  expect(runs[0].evidence.state).toBe('running');
  expect(runs[0].nextAction.code).toBe('evidence_progress');
 });
 it('separates extraction, handoff, review and publication axes',()=>{
  const {runs}=buildReportRuns({assets:[asset()],sessions:[session()],publications:[publication()]});
  const run=runs[0];
  expect(run.evidence.state).toBe('ready');
  expect(run.extraction.state).toBe('ready');
  expect(run.handoff.state).toBe('delivered');
  expect(run.review).toMatchObject({state:'review',total:16,approved:11,pending:5});
  expect(run.nextAction.code).toBe('review');
 });
 it('routes a fully approved report to publishing',()=>{
  const {runs}=buildReportRuns({assets:[asset()],sessions:[session()],
   publications:[publication({approved_count:16,pending_count:0})]});
  expect(runs[0].review.state).toBe('ready');
  expect(runs[0].nextAction.code).toBe('publish');
 });
 it('keeps published reports published with their version',()=>{
  const {runs}=buildReportRuns({assets:[asset()],sessions:[session()],
   publications:[publication({status:'published',publication_version:3,observation_count:16,public_visible:true,approved_count:16,pending_count:0})]});
  expect(runs[0].publication).toMatchObject({state:'published',version:3,observations:16,publicVisible:true});
  expect(runs[0].nextAction.code).toBe('publication_view');
 });
 it('surfaces blocked candidates before offering a review',()=>{
  const {runs}=buildReportRuns({assets:[asset()],sessions:[session()],
   publications:[publication({blocked_count:2,pending_count:0})]});
  expect(runs[0].review.state).toBe('blocked');
  expect(runs[0].nextAction.code).toBe('review_blocked');
  expect(runs[0].blockingReason).toContain('2 条候选存在发布阻塞');
 });
 it('reports an undelivered result as a handoff failure, not as zero candidates',()=>{
  const {runs}=buildReportRuns({assets:[asset()],sessions:[session({database_status:'not_imported'})]});
  expect(runs[0].handoff.state).toBe('failed');
  expect(runs[0].nextAction.code).toBe('handoff');
 });
 it('groups tasks by the report they name and keeps unknown tasks separate',()=>{
  const tasks=[{task_id:'t1',title:'报告提取 · 2025 中国石化可持续发展报告.pdf',status:'running'},
   {task_id:'t2',title:'IR 定向修复 · ir-1',status:'running'}];
  const {runs,unlinkedTasks}=buildReportRuns({assets:[asset()],tasks});
  expect(runs[0].activeTasks.map(task=>task.task_id)).toEqual(['t1']);
  expect(unlinkedTasks.map(task=>task.task_id)).toEqual(['t2']);
  expect(taskMatchesAsset(tasks[1],asset())).toBe(false);
 });
 it('counts the library axes consistently',()=>{
  const runs=buildReportRuns({assets:[asset(),asset({asset_id:'asset-2',file_name:'另一份.pdf',report_metadata:{title:'另一份'}})]}).runs;
  expect(runCounts(runs)).toMatchObject({all:2,identity:1,evidence:0,review:0});
 });
 it('drives the same six stage chips as the row axes',()=>{
  const {runs}=buildReportRuns({assets:[asset()],sessions:[session()],publications:[publication({approved_count:16,pending_count:0})]});
  const chips=stageChips(runs[0]);
  expect(chips.map(chip=>chip.key)).toEqual(['identity','evidence','extraction','handoff','review','publication']);
  expect(chips.find(chip=>chip.key==='publication')?.state).toBe('active');
  expect(chips.find(chip=>chip.key==='evidence')?.state).toBe('done');
 });
});
