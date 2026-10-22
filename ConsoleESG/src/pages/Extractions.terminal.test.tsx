// @vitest-environment jsdom
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {credentials,request,type Row} from '../api';
import {Actions} from '../components/ui';
import {WorkspaceProvider} from '../state';
import {DeliveryInventory,Extractions} from './Extractions';

let calls:{url:string,options:RequestInit}[]=[];
let routes:Record<string,Row>={};
const action={action:'materialize',label:'保存可交付阶段成果',
 endpoint:'/api/report-sessions/facade-1/materialize',requires_operator:true,
 requires_reason:true,accepts_revision:false,calls_model:false,payload:{job_ids:['job-1']}};
function mount(node:React.ReactNode){return render(<WorkspaceProvider>{node}</WorkspaceProvider>);}
beforeEach(()=>{
 credentials.clear();calls=[];routes={};window.location.hash='';
 vi.stubGlobal('EventSource',class{addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit={})=>{
  // Identity discovery is not a business request.
  if(String(url).endsWith('/internal/v1/me'))return new Response(JSON.stringify({operator_ref:'local-operator',roles:['read','review','admin']}),{headers:{'Content-Type':'application/json'}});
  calls.push({url:String(url),options});
  return new Response(JSON.stringify(routes[String(url)] || {}),{headers:{'Content-Type':'application/json'}});
 }));
});
afterEach(()=>{cleanup();credentials.clear();vi.unstubAllGlobals();});

it('没有凭据时终态保存动作禁用且不写请求',()=>{
 mount(<Actions actions={[action]} revision={987}/>);
 const button=screen.getByRole('button',{name:action.label});
 expect(button.hasAttribute('disabled')).toBe(true);
 expect(button.getAttribute('title')).toBe('数据平台未连接或当前身份没有此操作权限');
 fireEvent.click(button);
 expect(calls).toHaveLength(0);
});

it('终态保存发送明确原因及原job范围，不混入修订和模型凭据',async()=>{
 credentials.set('test-only-token','local-test-operator');credentials.setSecret('qiniu_api_key','do-not-send');
 mount(<Actions actions={[action]} revision={987}/>);
 fireEvent.click(screen.getByRole('button',{name:action.label}));
 expect(screen.getByRole('button',{name:'确认执行'}).hasAttribute('disabled')).toBe(true);
 fireEvent.change(screen.getByLabelText('操作原因'),{target:{value:'保存失败任务已冻结来源，不批准或发布'}});
 fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
 await waitFor(()=>expect(calls).toHaveLength(1));
 expect(calls[0].url).toBe('/document/api/report-sessions/facade-1/materialize');
 expect(JSON.parse(String(calls[0].options.body))).toEqual({job_ids:['job-1'],reason:'保存失败任务已冻结来源，不批准或发布'});
 expect(new Headers(calls[0].options.headers).get('Authorization')).toBe('Bearer test-only-token');
});

it('交付动作导航到报告身份核对表单，不能直接提交空交付请求',()=>{
 credentials.set('test-only-token','local-test-operator');
 mount(<Actions actions={[{action:'deliver',label:'交付已保存来源进入审核',requires_operator:true,
  endpoint:'/api/console/report-sessions/facade-1/deliver',scope:{session_id:'facade-1'}}]}/>);
 fireEvent.click(screen.getByRole('button',{name:'交付已保存来源进入审核'}));
 expect(window.location.hash).toBe('#/extractions/facade-1?tab=deliver');
 expect(calls).toHaveLength(0);
});

it('只有明确保护的materialize路径附凭据，不把token发到公开文档读取',async()=>{
 credentials.set('test-only-token','local-test-operator');
 await request('document','/api/report-sessions/facade-1/materialize',{method:'POST',body:'{"reason":"测试"}'});
 await request('document','/api/report-sessions/facade-1/facts');
 expect(new Headers(calls[0].options.headers).get('Authorization')).toBe('Bearer test-only-token');
 expect(new Headers(calls[1].options.headers).has('Authorization')).toBe(false);
});

it('来源保存与规范事实分项显示，质量未通过不得显示成发布成功',()=>{
 mount(<DeliveryInventory session={ {delivery_counts:{saved_source_facts:291,saved_core_facts:0,available_bundle_count:1},
  delivery_artifacts:[{job_id:'job-1',artifact_revision_id:'partial-fixture',source_fact_count:291,core_fact_count:0,status:'available',quality_counts:{needs_confirmation:291}}],
  delivery_artifact_blockers:[{reason:'固定输入摘要已变化'}]} }/>);
 expect(screen.getByText('可交付来源事实').nextElementSibling?.textContent).toBe('291');
 expect(screen.getByText('已保存规范事实').nextElementSibling?.textContent).toBe('0');
 expect(screen.getByText('来源 291 条 · 规范事实 0 条')).toBeTruthy();
 expect(screen.getByText('成果版本不可交付：固定输入摘要已变化')).toBeTruthy();
 expect(screen.queryByText('已发布')).toBeNull();
});

it('交付链接直接打开正确tab并保留来源3、候选0正式回执',async()=>{
 credentials.set('test-only-token','local-test-operator');
 const session={session_id:'facade-1',name:'失败任务阶段来源',execution_status:'failed',available_actions:[],
  delivery_counts:{saved_source_facts:3,saved_core_facts:0,available_bundle_count:1},delivery_artifacts:[]};
 routes['/document/api/report-sessions/facade-1']=session;
 routes['/document/api/report-sessions/facade-1/facts']={facts:[]};
 routes['/document/api/reports']={reports:[{report_id:'report-1',revision:7,
  metadata:{title:'固定来源报告',publisher_entity_ref:'group-1',period_start:'2025-01-01',period_end:'2025-12-31'}}]};
 routes['/document/api/console/report-sessions/facade-1/deliver']={deliveries:[
  {job_id:'job-1',status:'imported',source_fact_snapshots:3,imported:0,receipt_id:'test-source-only-receipt'}]};
 mount(<Extractions sessionId="facade-1" params={new URLSearchParams('tab=deliver')}/>);
 const tab=await screen.findByRole('tab',{name:'交付审核'});
 expect(tab.getAttribute('aria-selected')).toBe('true');
 const select=await screen.findByLabelText('已明确登记的报告身份');
 await waitFor(()=>expect(within(select).getAllByRole('option')).toHaveLength(2));
 fireEvent.change(select,{target:{value:'report-1'}});
 fireEvent.click(screen.getByRole('button',{name:'核对并交付候选'}));
 expect(await screen.findByText('已保存来源 3 条 · 标准候选 0 条。正式发布仍须独立审核。')).toBeTruthy();
 const post=calls.find(c=>c.url.endsWith('/deliver')&&c.options.method==='POST')!;
 expect(JSON.parse(String(post.options.body))).toMatchObject({report_id:'report-1',expected_revision:7,organization_ref:'group-1'});
 expect(new Headers(post.options.headers).get('Authorization')).toBe('Bearer test-only-token');
});

it('跨会话导航不会把前一个会话所选报告身份带入新的SQL查询',async()=>{
 credentials.set('test-only-token','local-test-operator');
 routes['/document/api/reports']={reports:[{report_id:'report-old',revision:1,metadata:{title:'前一份登记报告'}}]};
 for(const id of ['old-session','new-session']){
  routes[`/document/api/report-sessions/${id}`]={session_id:id,name:id,available_actions:[]};
  routes[`/document/api/report-sessions/${id}/facts`]={facts:[]};
 }
 const view=mount(<Extractions sessionId="old-session" params={new URLSearchParams('tab=deliver')}/>);
 const select=await screen.findByLabelText('已明确登记的报告身份');
 await waitFor(()=>expect(within(select).getAllByRole('option')).toHaveLength(2));
 fireEvent.change(select,{target:{value:'report-old'}});
 await waitFor(()=>expect(calls.some(c=>c.url.includes('old-session/delivery-status?report_id=report-old'))).toBe(true));
 view.rerender(<WorkspaceProvider><Extractions sessionId="new-session" params={new URLSearchParams('tab=deliver')}/></WorkspaceProvider>);
 await waitFor(()=>expect(calls.some(c=>c.url.endsWith('new-session/delivery-status'))).toBe(true));
 expect(calls.some(c=>c.url.includes('new-session/delivery-status?report_id=report-old'))).toBe(false);
 expect(calls.filter(c=>c.url.includes('delivery-status')).every(c=>!c.options.body)).toBe(true);
});
