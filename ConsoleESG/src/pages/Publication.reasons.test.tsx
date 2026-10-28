import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {credentials} from '../api';
import {WorkspaceProvider} from '../state';
import {blockerText} from '../publicationReasons';
import {Publication} from './Publication';

const codes=['fact_pending','mapping_review_required','reviewed_context_missing','standard_snapshot_mismatch','extension_execution_snapshot_mismatch','source_unit_unresolved','missing_required_dimension:pollution_medium','unrecognized_future_reason'];
let calls:string[];
beforeEach(()=>{credentials.set('anonymous-test-credential','test');calls=[];
 vi.stubGlobal('EventSource',class{onopen=null;onerror=null;onmessage=null;addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn((input:string)=>{const url=String(input);calls.push(url);
  const data=url.includes('publication-status')?{events:[],datasets:[]}:url.includes('candidates')?{items:[{mapping_id:'mapping/1',report_ref:'report / old',source_datapoint_id:'E1-6_07',raw_indicator_name:'范围1',reason_codes:codes,publication_status:'blocked'}]}:{};
  return Promise.resolve(new Response(JSON.stringify(data),{status:200}));
 }));
});
afterEach(()=>{cleanup();credentials.clear();window.location.hash='';vi.unstubAllGlobals();vi.restoreAllMocks();});

describe('发布门禁原因使用实际科学口径，不混淆比较排除',()=>{
 it.each([
  ['fact_pending','报告事实尚待审核'],['mapping_rejected','条款映射已被拒绝'],
  ['reviewed_context_missing','主体、期间与测量口径尚未审核确认'],
  ['standard_snapshot_mismatch','提取所用标准快照与发布指标绑定版本不一致'],
  ['source_unit_unresolved','报告原始单位尚未确定或不能安全换算'],
  ['missing_required_dimension:pollutant','缺少必要维度：污染物'],
  ['missing_required_context:measurement_method','缺少必要测量口径：测量方法'],
  ['fixed_dimension_mismatch:ghg_scope','维度不符合指标固定口径：温室气体排放范围'],
  ['dimension_value_mismatch:counting_basis','维度不符合指标允许值：计数依据'],
 ])('%s有可核对的中文说明',(code,label)=>expect(blockerText(code)).toBe(label));
 it('当前完整指标字典的必要科学字段都可读，未知字段不伪造含义',()=>{
  const dictionary=JSON.parse(readFileSync('../PlatESG/catalog/metric-dictionary.v1.json','utf8'));
  for(const metric of dictionary.metrics){for(const key of new Set<string>([...(metric.required_dimensions || []),...(metric.required_context_fields || []),...Object.keys(metric.fixed_dimensions || {}),...Object.keys(metric.dimension_constraints || {}),...Object.keys(metric.typed_context_constraints || {})])){
   expect(blockerText(`missing_required_dimension:${key}`),key).not.toContain('尚需核对的科学字段');
  }}
  expect(blockerText('missing_required_dimension:future_axis')).toBe('缺少必要维度：尚需核对的科学字段');
  expect(blockerText('future_gate')).toBe('尚需核对的发布条件');
  expect(blockerText('not_comparable')).toBe('尚需核对的发布条件');
 });
 it('实际发布页服务器端按报告过滤，未知code只保留诊断，版本冲突不引导直接批准，审核链接保留报告',async()=>{
  render(<WorkspaceProvider><Publication params={new URLSearchParams('report=report%20%2F%20old')}/></WorkspaceProvider>);
  fireEvent.click(await screen.findByRole('tab',{name:'发布条件与阻塞'}));
  expect(await screen.findByText('缺少必要维度：污染介质')).toBeDefined();
  expect(screen.getByText('发布条件候选 · 1')).toBeDefined();
  expect(screen.getByRole('button',{name:'进入报告审核'})).toBeDefined();
  expect(calls.find(url=>url.includes('/candidates?'))).toContain('&report_ref=report%20%2F%20old');
  expect(screen.getByText('尚需核对的发布条件')).toBeDefined();
  expect(screen.getByText(/当前只显示该报告；发布、撤下与删除都以这份报告为单位/)).toBeDefined();
  expect(screen.getByText('待发布报告')).toBeDefined();
  fireEvent.click(screen.getByRole('tab',{name:'记录与诊断'}));
  expect(screen.getByText('持久发布事件（所有报告）')).toBeDefined();
  fireEvent.click(screen.getByRole('tab',{name:'发布条件与阻塞'}));
  expect(screen.getByText(/批准事实或口径不能改变既有任务所用的版本/)).toBeDefined();
  fireEvent.click(screen.getByText('原始发布原因代码'));
  expect(screen.getByText(/unrecognized_future_reason/)).toBeDefined();
  fireEvent.click(screen.getByRole('button',{name:'查看审核与来源'}));
  expect(window.location.hash).toBe('#/review/mapping%2F1?report=report%20%2F%20old');
  expect(screen.queryByRole('button',{name:'处理审核条件'})).toBeNull();
 });
});
