import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {type Row} from '../api';
import {WorkspaceProvider,useWorkspace} from '../state';
import {Extractions} from '../pages/Extractions';
import {ScopeSelector,scopeFromSelection,type ParentSnapshot} from './ScopeSelector';
import {ExtensionSelector,extensionCompatibility} from './ExtensionSelector';

const packageId='esrs.2023-set1.e1-6',extensionId='project.esrs.2023-set1.e1-6.extension.scope1-scope2-total';
const oldParent:ParentSnapshot={package_id:packageId,package_version:'2.1.1',source_digest:'1'.repeat(64)};
const newParent:ParentSnapshot={package_id:packageId,package_version:'2.1.2',source_digest:'2'.repeat(64)};
function approved(definitionVersion='1.0.0',parent=oldParent):Row {
 const marker=definitionVersion==='1.0.0'?'3':'4';
 return {schema_version:'project-extension-release-1.0',definition_digest:marker.repeat(64),release_digest:(marker==='3'?'5':'6').repeat(64),
  approval:{schema_version:'project-extension-approval-1.0',approval_id:`approved-${definitionVersion}`,decision:'approved',definition_digest:marker.repeat(64)},
  definition:{extension_id:extensionId,definition_version:definitionVersion,parent_package_id:parent.package_id,
   parent_package_version:parent.package_version,parent_source_digest:parent.source_digest,labels:{zh:'范围1＋2合计'},description:'报告直接披露与审核后计算分开。',
   package_core:true,original_clause_links:[{source_datapoint_id:'E1-6_07'},{source_datapoint_id:'E1-6_09'},{source_datapoint_id:'E1-6_10'}],
   derivation_rules:[{rule_id:'sum-location',operands:[{source_datapoint_id:'E1-6_07'},{source_datapoint_id:'E1-6_09'}]}]}};
}
const originalIds=['E1-5_01','E1-5_05','E1-5_09','E1-6_07','E1-6_09','E1-6_10','E1-6_11','E2-4_02','E2-4_03','E2-4_09','E2-5_02','E3-4_01','E3-4_02','E3-4_03','E3-4_11','E3-4_12'];
const metricId=(id:string)=>`esrs.2023-set1.${id.split('_')[0].toLowerCase()}.dp${id.split('_')[1]}`;
const packages=['E1-5','E1-6','E2-4','E2-5','E3-4'].map(name=>({package_id:`esrs.2023-set1.${name.toLowerCase()}`,package_version:'2.1.2',
 source_digest:'2'.repeat(64),disclosure_requirement:name,status:'draft',core_source_datapoint_ids:originalIds.filter(id=>id.startsWith(name+'_')),
 metric_summaries:originalIds.filter(id=>id.startsWith(name+'_')).map(source_datapoint_id=>({metric_id:metricId(source_datapoint_id),source_datapoint_id,labels:{zh:source_datapoint_id}}))}));
const profile={overall_source_datapoint_ids:originalIds,pending_source_datapoint_ids:[],output_count:16,
 session_standards:packages.map(p=>({package_id:p.package_id,package_version:p.package_version,metric_ids:p.metric_summaries.map(m=>m.metric_id)})),
 selection_snapshot:{profile_id:'fixture-major',source_datapoint_ids:originalIds,package_digests:Object.fromEntries(packages.map(p=>[p.package_id,p.source_digest]))}};
let releases:Row[],calls:{url:string,options:RequestInit}[];
function mounted(node:React.ReactNode){return render(<WorkspaceProvider>{node}</WorkspaceProvider>);}
function Refresh(){const {refresh}=useWorkspace();return <button onClick={refresh}>刷新登记目录</button>;}
beforeEach(()=>{
 releases=[approved()];calls=[];
 vi.stubGlobal('EventSource',class{onopen=null;onerror=null;onmessage=null;addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn((input:string,options:RequestInit={})=>{
  const url=String(input);calls.push({url,options});let data:unknown;
  if(url==='/document/api/pipeline/extensions')data={items:releases};
  else if(url==='/document/api/pipeline/standards/catalog')data={packages};
  else if(url==='/document/api/pipeline/extraction-profiles/platform-major?topics=E1,E2,E3')data=profile;
  else if(url==='/document/api/report-assets')data={assets:[{asset_id:'fixture-pdf',file_name:'匿名测试报告.pdf'}]};
  else if(url==='/targeted/api/ir-runs')data={runs:[]};
  else if(url==='/document/api/pipeline/compatibility')data={ready:true};
  else if(url==='/document/api/report-sessions/preview')data={output_count:17,original_output_count:16,extension_output_count:1,
   internal_operand_metric_ids:[],selected_outputs:originalIds.map(source_datapoint_id=>({source_datapoint_id}))};
  else return Promise.resolve(new Response(JSON.stringify({detail:`Undeclared isolated fixture: ${url}`}),{status:404}));
  return Promise.resolve(new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}}));
 }));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});

describe('延伸选择绑定实际父包与批准收据',()=>{
 it('旧已批准1.0.0不能搭配本次2.1.2父包，直接披露与公式均禁用',async()=>{
  const changed=vi.fn();mounted(<ExtensionSelector parentSnapshots={[newParent]} onChange={changed}/>);
  const reported=await screen.findByLabelText('提取报告直接披露');
  expect((reported as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByLabelText('审核后计算：E1-6_07 ＋ E1-6_09') as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/本次已选父包 v2.1.2.*固定父包 v2.1.1/)).toBeTruthy();
  fireEvent.click(reported);expect(changed.mock.calls.at(-1)?.[0]).toEqual([]);
 });
 it('相同父版本但不同来源摘要仍被禁止，不能用标签代替身份',async()=>{
  mounted(<ExtensionSelector parentSnapshots={[{...oldParent,source_digest:'7'.repeat(64)}]} onChange={vi.fn()}/>);
  expect((await screen.findByLabelText('提取报告直接披露') as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/来源摘要与延伸固定父包不一致/)).toBeTruthy();
 });
 it('新版草稿不暗自启用，取得匹配批准与登记收据后才可显式选择',async()=>{
  const changed=vi.fn(),release=approved('1.0.1',newParent);releases=[{...release,approval:undefined}];
  mounted(<><Refresh/><ExtensionSelector parentSnapshots={[newParent]} onChange={changed}/></>);
  const reported=await screen.findByLabelText('提取报告直接披露');expect((reported as HTMLInputElement).disabled).toBe(true);
  expect(screen.getByText(/不能选择草稿/)).toBeTruthy();expect(changed.mock.calls.at(-1)?.[0]).toEqual([]);
  releases=[release];fireEvent.click(screen.getByRole('button',{name:'刷新登记目录'}));
  await waitFor(()=>expect((reported as HTMLInputElement).disabled).toBe(false));
  expect((reported as HTMLInputElement).checked).toBe(false);
  fireEvent.click(reported);
  await waitFor(()=>expect(changed.mock.calls.at(-1)?.[0][0]).toMatchObject({definition_version:'1.0.1',release_digest:release.release_digest,value_origins:['reported']}));
 });
 it('仅延伸选择明确展示自身固定父，保持原审批版本与零原条款输出',async()=>{
  const changed=vi.fn();mounted(<ExtensionSelector parentSnapshots={[]} onChange={changed}/>);
  const reported=await screen.findByLabelText('提取报告直接披露');expect((reported as HTMLInputElement).disabled).toBe(false);
  expect(screen.getByText(/勾选该延伸将明确使用它固定的父包 v2.1.1/)).toBeTruthy();
  expect(screen.getByText(oldParent.source_digest)).toBeTruthy();fireEvent.click(reported);
  await waitFor(()=>expect(changed.mock.calls.at(-1)?.[0][0]).toMatchObject({definition_version:'1.0.0',release_digest:approved().release_digest}));
 });
 it('显式历史父版本仍可使用旧批准延伸，不自动改用另一个已登记版本',async()=>{
  releases=[approved(),approved('1.0.1',newParent)];const changed=vi.fn();mounted(<ExtensionSelector parentSnapshots={[oldParent]} onChange={changed}/>);
  const reported=await screen.findAllByLabelText('提取报告直接披露');
  expect((reported[0] as HTMLInputElement).disabled).toBe(false);expect((reported[1] as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(reported[0]);await waitFor(()=>expect(changed.mock.calls.at(-1)?.[0][0].definition_version).toBe('1.0.0'));
 });
 it('选择后改变父身份保留旧选择但报告阻塞，可明确清空，不能自动重绑',async()=>{
  const changed=vi.fn();const result=render(<ExtensionSelector parentSnapshots={[oldParent]} onChange={changed}/>);
  fireEvent.click(await screen.findByLabelText('提取报告直接披露'));
  await waitFor(()=>expect(changed.mock.calls.at(-1)?.[0]).toHaveLength(1));
  result.rerender(<ExtensionSelector parentSnapshots={[newParent]} onChange={changed}/>);
  await waitFor(()=>expect(changed.mock.calls.at(-1)?.[1]).toContain('版本不兼容'));
  expect(changed.mock.calls.at(-1)?.[0][0].definition_version).toBe('1.0.0');
  fireEvent.click(screen.getByRole('button',{name:'清空延伸选择'}));
  await waitFor(()=>expect(changed.mock.calls.at(-1)).toEqual([[],undefined]));
 });
 it('批准摘要漂移、缺少父摘要或多个父身份都不能形成可用选择',()=>{
  const release=approved();
  expect(extensionCompatibility({...release,approval:{...release.approval,definition_digest:'0'.repeat(64)}},[]).compatible).toBe(false);
  expect(extensionCompatibility({...release,definition:{...release.definition,parent_source_digest:undefined}},[]).compatible).toBe(false);
  expect(extensionCompatibility(release,[oldParent,newParent]).compatible).toBe(false);
 });
 it('所选原条款携带精确父摘要，主要快照漂移或摘要缺失明确阻塞',()=>{
  const chosen=new Set(originalIds.map(metricId));const scope=scopeFromSelection(packages,chosen,profile.selection_snapshot);
  expect(scope.parent_snapshots?.find(p=>p.package_id===packageId)).toEqual(newParent);
  expect(Object.keys(scope.standards[0])).toEqual(['package_id','package_version','metric_ids']);
  expect(()=>scopeFromSelection(packages,chosen,{...profile.selection_snapshot,package_digests:{...profile.selection_snapshot.package_digests,[packageId]:'9'.repeat(64)}})).toThrow('来源摘要与主要配置快照不一致');
  expect(()=>scopeFromSelection(packages.map(p=>({...p,source_digest:undefined})),chosen)).toThrow('缺少有效来源摘要');
 });
 it('实际提取创建页面将16原条款的父身份接到延伸选择，预览只提交16＋1且辅助摘要不泄入standards',async()=>{
  releases=[approved(),approved('1.0.1',newParent)];mounted(<Extractions newTask params={new URLSearchParams('asset=fixture-pdf')}/>);
  fireEvent.click(await screen.findByRole('button',{name:'选择平台主要条款（E1–E3 16 项）'}));
  const reported=await screen.findAllByLabelText('提取报告直接披露');
  await waitFor(()=>expect((reported[0] as HTMLInputElement).disabled).toBe(true));
  expect((reported[1] as HTMLInputElement).disabled).toBe(false);fireEvent.click(reported[1]);
  const preview=screen.getByRole('button',{name:'预览后端实际计划'});
  await waitFor(()=>expect((preview as HTMLButtonElement).disabled).toBe(false));fireEvent.click(preview);
  await waitFor(()=>expect(calls.some(call=>call.url.endsWith('/report-sessions/preview')&&call.options.method==='POST')).toBe(true));
  const request=JSON.parse(String(calls.find(call=>call.url.endsWith('/report-sessions/preview')&&call.options.method==='POST')!.options.body));
  expect(request.standards.flatMap((row:Row)=>row.metric_ids)).toHaveLength(16);
  expect(request.extension_selections).toHaveLength(1);expect(request.extension_selections[0].definition_version).toBe('1.0.1');
  expect(request.standards.every((row:Row)=>Object.keys(row).sort().join(',')==='metric_ids,package_id,package_version')).toBe(true);
  expect(request).not.toHaveProperty('parent_snapshots');
  expect(calls.some(call=>call.url.endsWith('/api/report-sessions')&&call.options.method==='POST')).toBe(false);
 });
});
