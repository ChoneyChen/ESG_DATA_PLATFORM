import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {credentials,type Row} from '../api';
import {WorkspaceProvider} from '../state';
import {buildAutomationRequest,defaultProcessingConfig,moveReport,orderReportIds,type AutomationDraft} from '../automation';
import {Automation,AutomationItem,AutomationPreview,OrderedReports} from './Automation';

// Independent synthetic records: no real report, failed-run file, model, or database is read.
const assets:Row[]=[
 {asset_id:'synthetic-pdf-a',file_name:'synthetic-2099.pdf',sha256:'a'.repeat(64),report_metadata:{title:'明确报告 A',publisher_name:'合成企业 A',reporting_year:2024},processing_status:'unprocessed'},
 {asset_id:'synthetic-pdf-b',file_name:'synthetic-1900.pdf',sha256:'b'.repeat(64),report_metadata:{title:'明确报告 B',publisher_name:'合成企业 B',reporting_year:2023},processing_status:'ready'},
 {asset_id:'synthetic-no-year',file_name:'synthetic-2022.pdf',sha256:'c'.repeat(64),report_metadata:{title:'无登记年份'},processing_status:'unprocessed'},
];
const reports:Row[]=assets.slice(0,2).map((asset,index)=>({report_id:`synthetic-report-${index}`,revision:index+1,metadata:asset.report_metadata,asset_versions:[{asset_id:asset.asset_id}]}));
const scope={standards:[{package_id:'esrs.2023-set1.e1-5',package_version:'2.1.1',metric_ids:['stable-original-01','stable-original-05']}],originalIds:['E1-5_01','E1-5_05'],kind:'platform_major',selection_snapshot:{source_datapoint_ids:['E1-5_01','E1-5_05'],profile_id:'synthetic-major'}};
const draft:AutomationDraft={name:'合成批次',assetIds:[assets[0].asset_id,assets[1].asset_id],assets,reports,reportSelections:{},target:'processing_extraction',processingMode:'auto',processingConfig:{...defaultProcessingConfig},scope,extensionSelections:[],semanticProvider:'local_qwen',semanticModel:'',completeness:'balanced',limitedEvidence:true,cloudAuthorization:false,cloudOcr:false,failurePolicy:'continue',budget:{}};
let calls:{url:string,options:RequestInit,body?:Row}[]=[];
const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
beforeEach(()=>{
 credentials.clear();credentials.set('synthetic-token-never-saved','synthetic-operator',['read','review','admin']);calls=[];window.location.hash='';
 vi.stubGlobal('EventSource',class{addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit={})=>{
  const path=String(url),body=options.body && !(options.body instanceof FormData)?JSON.parse(String(options.body)):undefined;
  calls.push({url:path,options,body});
  if(path==='/document/api/console/reports')return response({schema_version:'console-report-v1',items:assets});
  if(path==='/document/api/reports')return response({reports});
  if(path==='/document/api/pipeline/compatibility')return response({ready:true});
  if(path.includes('/standards/catalog'))return response({packages:[]});
  if(path.includes('/extraction-profiles/foundation'))return response({profile:'universal',metric_count:2,standards:scope.standards,package_digests:{'esrs.2023-set1.e1-5':'d'.repeat(64)},metrics:[{code:'E1-5_01',metric_id:'stable-original-01'},{code:'E1-5_05',metric_id:'stable-original-05'}]});
  if(path.includes('/extraction-profiles/'))return response({overall_source_datapoint_ids:[],pending_source_datapoint_ids:[],session_standards:[],output_count:0});
  if(path.includes('/extensions'))return response({items:[]});
  if(path.endsWith('/production-automations/preview'))return response({preview_id:`automation-preview-${'d'.repeat(64)}`,can_submit:true,items:(body?.items || []).map((item:Row)=>({...item,file_name:'合成预览报告',status:'ready',stages:['报告处理'],blockers:[]}))});
  if(path==='/document/api/production-automations'&&options.method==='POST')return response({automation_id:'synthetic-created',status:'active'});
  if(path==='/document/api/production-automations'||path.startsWith('/document/api/production-automations?'))return response({plans:[]});
  return response({detail:`未声明合成接口：${path}`},404);
 }));
});
afterEach(()=>{cleanup();credentials.clear();vi.unstubAllGlobals();vi.restoreAllMocks();});

describe('批次身份、排序及范围合同',()=>{
 it('年份排序只采用登记年份，缺失年份排末尾，不解析文件名',()=>{
  const ids=[assets[2].asset_id,assets[0].asset_id,assets[1].asset_id];
  expect(orderReportIds(ids,assets,reports,'year_ascending')).toEqual([assets[1].asset_id,assets[0].asset_id,assets[2].asset_id]);
  expect(orderReportIds(ids,assets,reports,'year_descending')).toEqual([assets[0].asset_id,assets[1].asset_id,assets[2].asset_id]);
  expect(orderReportIds(ids,assets,reports,'selection')).toEqual(ids);
  expect(ids).toEqual([assets[2].asset_id,assets[0].asset_id,assets[1].asset_id]);
 });
 it('手动排序不丢选取或重复资产，边界操作保留原顺序',()=>{
  const ids=draft.assetIds;
  expect(moveReport(ids,1,-1)).toEqual([...ids].reverse());
  expect(moveReport(ids,0,-1)).toEqual(ids);
  expect(moveReport(ids,1,1)).toEqual(ids);
  expect(ids).toEqual(draft.assetIds);
 });
 it('冻结原编号映射、快照、显式顺序和内容指纹，不附访问密钥或云秘密',()=>{
  const result=buildAutomationRequest(draft);
  expect(result.items.map((item:Row)=>item.asset_id)).toEqual(draft.assetIds);
  expect(result.items[0].source_sha256).toBe('a'.repeat(64));
  expect(result.extraction.standards).toEqual(scope.standards);
  expect(result.extraction.selection_snapshot).toEqual(scope.selection_snapshot);
  expect(result.extraction.role_config.cloud_authorization.allowed).toBe(false);
  expect(JSON.stringify(result)).not.toContain('synthetic-token');
  expect(JSON.stringify(result)).not.toContain('qiniu_api_key');
 });
 it('指标集同步冻结为 foundation 路由与 profile，不退回标准映射',()=>{
  const profileScope={standards:scope.standards,originalIds:['E1-5_01','E1-5_05','E1-6_07'],kind:'foundation',profile:'universal',parent_snapshots:[{package_id:'esrs.2023-set1.e1-5',package_version:'2.1.1',source_digest:'d'.repeat(64)}]};
  const result=buildAutomationRequest({...draft,scope:profileScope,target:'processing_extraction'});
  expect(result.extraction.extraction_route).toBe('foundation16');
  expect(result.extraction.metric_profile).toBe('universal');
 });
 it('所选资产不再存在或被归档时阻止提交，不默默缩减批次',()=>{
  expect(()=>buildAutomationRequest({...draft,assets:[assets[0]]})).toThrow('不再位于当前报告库');
  expect(()=>buildAutomationRequest({...draft,assets:[{...assets[0],lifecycle:{state:'archived'}},assets[1]]})).toThrow('已归档或删除');
  expect(()=>buildAutomationRequest({...draft,assetIds:[assets[0].asset_id,assets[0].asset_id]})).toThrow('重复加入');
  expect(()=>buildAutomationRequest({...draft,assets:[assets[0],{...assets[1],sha256:assets[0].sha256}]})).toThrow('同一份 PDF 内容');
 });
 it('候选交付只能固定明确匹配的登记报告和当前修订',()=>{
  expect(()=>buildAutomationRequest({...draft,target:'processing_extraction_delivery'})).toThrow('明确关联业务报告身份');
  expect(()=>buildAutomationRequest({...draft,target:'processing_extraction_delivery',reportSelections:{[assets[0].asset_id]:reports[1].report_id}})).toThrow('PDF 不一致');
  const result=buildAutomationRequest({...draft,target:'processing_extraction_delivery',reportSelections:{[assets[0].asset_id]:reports[0].report_id,[assets[1].asset_id]:reports[1].report_id}});
  expect(result.items.map((item:Row)=>[item.report_id,item.report_revision])).toEqual([['synthetic-report-0',1],['synthetic-report-1',2]]);
 });
 it('自动化本地合同拒绝云配置，不误导为可以后台永久保存云凭据',()=>{
  expect(()=>buildAutomationRequest({...draft,semanticProvider:'qiniu_vlm',cloudAuthorization:true})).toThrow('支持本地模型');
  expect(()=>buildAutomationRequest({...draft,target:'processing',processingConfig:{...draft.processingConfig,review_provider:'qiniu'}})).toThrow('支持本地模型');
 });
});

describe('批量生产交互',()=>{
 it('使用真实console-report-v1.items目录显示已登记资产，可直接加入批次',async()=>{
  render(<WorkspaceProvider><Automation newTask/></WorkspaceProvider>);
  const first=await screen.findByRole('checkbox',{name:/明确报告 A/});
  expect(screen.getByRole('checkbox',{name:/明确报告 B/})).toBeTruthy();
  expect(screen.getByText('选取筛选结果（3）')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'全选（3）'}));
  expect(screen.getByText('已选 3 份')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'清空所选'}));
  expect(screen.getByText('已选 0 份')).toBeTruthy();
  fireEvent.click(first);
  expect(screen.getByText('已选 1 份')).toBeTruthy();
 });
 it('未补报告身份的勾选项直接标出缺失字段，并可进入报告详情补齐',async()=>{
  render(<WorkspaceProvider><Automation newTask params={new URLSearchParams({asset:assets[2].asset_id})}/></WorkspaceProvider>);
  await screen.findByText('全选（3）');
  expect(await screen.findByText('补身份：报告身份')).toBeTruthy();
  expect(screen.getByText('待补身份 1 份')).toBeTruthy();
 });
 it('交付身份阻塞把字段键翻译为中文，不再显示英文轴名',()=>{
  render(<AutomationPreview value={{can_submit:false,items:[],blockers:[{message:'候选交付缺少已确认报告身份：title、publisher_entity_ref、period_start、period_end'}]}} originalIds={[]}/>);
  expect(screen.getByText(/报告名称、发布者主体、报告期起始、报告期结束/)).toBeTruthy();
 });
 it('一键部署只提交一个父计划，后台编排子阶段；预览和提交顺序一致',async()=>{
  render(<WorkspaceProvider><Automation newTask params={new URLSearchParams({assets:`${assets[0].asset_id},${assets[1].asset_id}`})}/></WorkspaceProvider>);
  const previewButton=await screen.findByRole('button',{name:'预览整个批次的实际计划'});
  await waitFor(()=>expect((previewButton as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(previewButton);
  fireEvent.click(await screen.findByRole('button',{name:'一键部署并自动开始'}));
  await waitFor(()=>expect(window.location.hash).toBe('#/automation/synthetic-created'));
  const mutations=calls.filter(call=>call.options.method==='POST');
  expect(mutations.map(call=>call.url)).toEqual(['/document/api/production-automations/preview','/document/api/production-automations']);
  expect(mutations[1].body?.request).toEqual(mutations[0].body);
  expect(mutations[1].body?.start).toBe(true);
  expect(mutations[1].body?.preview_id).toBe(`automation-preview-${'d'.repeat(64)}`);
  expect(mutations[1].body?.submission_key).toBeTruthy();
 });
 it('报告按登记年份自动排序提交，页面不再出现排序控件或已选面板',async()=>{
  render(<WorkspaceProvider><Automation newTask params={new URLSearchParams({assets:`${assets[0].asset_id},${assets[1].asset_id}`})}/></WorkspaceProvider>);
  await screen.findByText('全选（3）');
  expect(screen.queryByText('自动排序规则')).toBeNull();
  expect(document.querySelector('.automation-order')).toBeNull();
  const previewButton=await screen.findByRole('button',{name:'预览整个批次的实际计划'});
  await waitFor(()=>expect((previewButton as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(previewButton);
  await waitFor(()=>expect(calls.some(call=>call.url.endsWith('/production-automations/preview'))).toBe(true));
  const request=calls.find(call=>call.url.endsWith('/production-automations/preview'))!.body!;
  expect(request.items.map((item:Row)=>item.asset_id)).toEqual([assets[1].asset_id,assets[0].asset_id]);
 });
 it('配置改变使已有预览失效，必须重新核对后才能部署',async()=>{
  render(<WorkspaceProvider><Automation newTask params={new URLSearchParams({assets:`${assets[0].asset_id},${assets[1].asset_id}`})}/></WorkspaceProvider>);
  const previewButton=screen.getByRole('button',{name:'预览整个批次的实际计划'});
  await waitFor(()=>expect((previewButton as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(previewButton);
  await screen.findByRole('button',{name:'一键部署并自动开始'});
  fireEvent.change(screen.getByLabelText('遇到失败或阻塞'),{target:{value:'pause'}});
  await waitFor(()=>expect(screen.queryByRole('button',{name:'一键部署并自动开始'})).toBeNull());
  expect(calls.filter(call=>call.url==='/document/api/production-automations'&&call.options.method==='POST')).toHaveLength(0);
 });
 it('只读操作员可预览，管理按钮按服务器已验证角色禁用',async()=>{
  credentials.set('synthetic-read-token','synthetic-reader',['read']);
  render(<WorkspaceProvider><Automation newTask params={new URLSearchParams({asset:assets[0].asset_id})}/></WorkspaceProvider>);
  const previewButton=screen.getByRole('button',{name:'预览整个批次的实际计划'});
  await waitFor(()=>expect((previewButton as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(previewButton);
  expect((await screen.findByRole('button',{name:'一键部署并自动开始'}) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button',{name:'保存计划，稍后启动'}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/当前身份没有生产管理权限/)).toBeTruthy();
  const previewCall=calls.find(call=>call.url.endsWith('/production-automations/preview'));
  expect(new Headers(previewCall?.options.headers).get('Authorization')).toBe('Bearer synthetic-read-token');
  expect(JSON.stringify(previewCall?.body)).not.toContain('synthetic-read-token');
 });
 it('批次预览逐项保留阻塞，即使其余报告可运行也不开放整个批次',()=>{
  render(<AutomationPreview value={{can_submit:false,items:[{asset_id:'synthetic-good',file_name:'合成可用',status:'ready',stages:['报告处理'],blockers:[]},{asset_id:'synthetic-blocked',file_name:'合成受阻',status:'blocked',stages:['报告处理'],blockers:[{message:'明确报告期尚未确认'}]}]}} originalIds={['E1-6_09','E1-6_10']}/>);
  expect(screen.getByText('明确报告期尚未确认')).toBeTruthy();
  expect(screen.getByText('E1-6_09')).toBeTruthy();
  expect(screen.getByText('E1-6_10')).toBeTruthy();
  expect(screen.queryByRole('button',{name:'一键部署并自动开始'})).toBeNull();
 });
 it('运行页显示来自提取阶段的真实分母，不把处理结束当成提取完成',()=>{
  const item={asset_id:'synthetic-source',file_name:'合成报告',status:'extracting',stage:'extraction',processing_id:'synthetic-processing',extraction_plan_id:'synthetic-extraction',progress:{stages:[{stage:'ocr',label:'文档识别',status:'completed'}],extraction_stages:[{job_id:'synthetic-job',status:'running',progress:{determinate:true,current:2,total:16,unit:'metrics',label:'所选条款处理'}}]}};
  render(<AutomationItem index={0} current item={item}/>);
  expect(screen.getByText('定向提取中')).toBeTruthy();
  expect(screen.getByText('2 / 16 条款 · 已记录处理结论不代表有可用事实或发布')).toBeTruthy();
  expect(screen.getByText('文档识别')).toBeTruthy();
  expect(screen.getByRole('button',{name:'提取成果与缺口'})).toBeTruthy();
 });
 it('服务依赖暂时不可用时显示真实暂停原因，不把在途报告伪装为失败',async()=>{
  const originalFetch=globalThis.fetch;
  vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit={})=>{
   if(String(url)==='/document/api/production-automations/synthetic-paused')return response({automation_id:'synthetic-paused',name:'合成暂态依赖',revision:2,status:'paused',status_label:'已暂停',pause_reason:'提取服务暂不可用，等待恢复',blockers:[{message:'依赖服务返回 503'}],request:{target:'processing_extraction'},items:[{item_id:'synthetic-item',asset_id:assets[0].asset_id,file_name:'合成进行报告',position:1,status:'extracting',stage:'extraction',message:'等待服务恢复',blockers:[{message:'固定提取状态保留'}]}],available_actions:[{action:'resume',label:'恢复自动推进'}]});
   if(String(url).endsWith('/synthetic-paused/events'))return response({events:[]});
   return originalFetch(url,options);
  }));
  render(<WorkspaceProvider><Automation automationId="synthetic-paused"/></WorkspaceProvider>);
  expect(await screen.findByText('提取服务暂不可用，等待恢复')).toBeTruthy();
  expect(screen.getByText('依赖服务返回 503')).toBeTruthy();
  expect(screen.getByText('固定提取状态保留')).toBeTruthy();
  expect(screen.getByText('失败或阻塞').nextElementSibling?.textContent).toBe('0');
  expect(screen.getByText('定向提取中')).toBeTruthy();
 });
 it('简化计划列表不展示归档开关',async()=>{render(<WorkspaceProvider><Automation/></WorkspaceProvider>);await waitFor(()=>expect(calls.some(call=>call.url==='/document/api/production-automations?include_archived=false')).toBe(true));expect(screen.queryByRole('checkbox',{name:'显示已归档'})).toBeNull();});
 it('旧归档动作不能冒充计划删除操作',async()=>{vi.stubGlobal('fetch',vi.fn(async(url:string)=>String(url).endsWith('/events')?response({events:[]}):response({automation_id:'synthetic-terminal',name:'合成终态',revision:4,status:'completed',request:{target:'processing'},items:[],available_actions:[{action:'archive',label:'旧归档操作'},{action:'restore',label:'旧恢复操作'}]})));render(<WorkspaceProvider><Automation automationId="synthetic-terminal"/></WorkspaceProvider>);await screen.findByText('合成终态');expect(screen.queryByRole('button',{name:'旧归档操作'})).toBeNull();expect(screen.queryByRole('button',{name:'删除计划记录（可恢复）'})).toBeNull();});
 it('在运行计划没有后端归档动作时不自行显示删除按钮',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>String(url).endsWith('/events')?response({events:[]}):response({automation_id:'synthetic-active',name:'合成在途',revision:2,status:'active',request:{target:'processing'},items:[],available_actions:[{action:'cancel',label:'取消未完成生产'}]})));
  render(<WorkspaceProvider><Automation automationId="synthetic-active"/></WorkspaceProvider>);
  await screen.findByText('合成在途');
  expect(screen.getByRole('button',{name:'取消未完成生产'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'删除计划记录（可恢复）'})).toBeNull();
 });
 it('重排待执行项不提供移除按钮，不能改变后台冻结的完整待执行名单',()=>{
  const changed=vi.fn();render(<OrderedReports ids={draft.assetIds} assets={assets} disabled={false} onChange={changed} allowRemove={false}/>);
  expect(screen.queryByRole('button',{name:'移除第 1 份报告'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'上移第 2 份报告'}));
  expect(changed).toHaveBeenCalledWith([...draft.assetIds].reverse());
 });
 it('数据平台未连接时不读取后台计划，并说明原因',async()=>{
  credentials.clear();render(<WorkspaceProvider><Automation/></WorkspaceProvider>);
  expect(screen.getByText('数据平台未连接，暂不能管理或部署批量生产计划。')).toBeTruthy();
  await waitFor(()=>expect(calls.filter(call=>call.url.includes('production-automations'))).toHaveLength(0));
 });
});
