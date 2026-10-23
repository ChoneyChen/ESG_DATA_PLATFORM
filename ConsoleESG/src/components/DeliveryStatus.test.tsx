// @vitest-environment jsdom
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {credentials} from '../api';
import {WorkspaceProvider,useWorkspace} from '../state';
import {DeliveryStatusPanel,useDeliveryStatus,type DeliveryState} from './DeliveryStatus';

const counts=(n:number|null)=>({archived_source_count:n,selected_candidate_count:n===null?null:0,
 internal_candidate_count:n===null?null:0,published_observation_count:n===null?null:0});
const saved:DeliveryState={status:'source_archived',checked_at:'2026-10-08T01:00:00+08:00',
 current_revision_counts:counts(291),session_counts:counts(294),report_counts:counts(299),
 publication_dataset_version:3,report_counts_scope:'fixed_report_version',
 jobs:[{job_id:'job-1',status:'source_archived',counts:counts(291)}],
 execution:{native_status:'failed',legacy_database_status:'not_imported',jobs:[{job_id:'job-1',package_id:'esrs.e1.e1-6',
  selected_metrics:4,attempted_metrics:2,not_executed_metrics:2}]}};
let calls:{url:string,options:RequestInit}[]=[];
let respond:(url:string,options:RequestInit)=>Promise<Response>;
const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
function ReadStatus({sessionId='session-1',reportId}:{sessionId?:string,reportId?:string}){
 const state=useDeliveryStatus(sessionId,reportId),{refresh}=useWorkspace();
 return <><button onClick={refresh}>重新核对</button><DeliveryStatusPanel {...state}/></>;
}
function mount(props:{sessionId?:string,reportId?:string}={}){
 return render(<WorkspaceProvider><ReadStatus {...props}/></WorkspaceProvider>);
}
const currentSource=()=>within(screen.getByRole('region',{name:'当前固定成果版本'})).getByText('SQL存档来源').nextElementSibling?.textContent;
beforeEach(()=>{
 credentials.clear();calls=[];respond=async()=>json(saved);
 vi.stubGlobal('EventSource',class{addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit={})=>{
  // Identity discovery is the workbench asking who it is, not a data request.
  if(String(url).endsWith('/internal/v1/me'))return json({operator_ref:'local-operator',roles:['read','review','admin']});
  calls.push({url:String(url),options});return respond(String(url),options);
 }));
});
afterEach(()=>{cleanup();credentials.clear();vi.unstubAllGlobals();});

it('无凭据不请求SQL状态，也不把本地成果数量显示为零或已入库',()=>{
 mount();expect(calls).toHaveLength(0);
 expect(screen.getByText('数据平台未连接，暂不能核对数据库存档。已保存成果数量不作为SQL入库证明。')).toBeTruthy();
 expect(screen.queryByLabelText('数据库持久交付状态')).toBeNull();
});

it('只读状态使用明确报告身份及受保护GET，不接受原生会话未入库作为SQL结果',async()=>{
 credentials.set('test-only-token','operator');mount({sessionId:'plan-1',reportId:'report-1'});
 await screen.findByText('SQL来源 291 条 · 所选候选 0 条');
 expect(calls).toHaveLength(1);
 expect(calls[0].url).toBe('/document/api/console/report-sessions/plan-1/delivery-status?report_id=report-1');
 expect(calls[0].options.method || 'GET').toBe('GET');expect(calls[0].options.body).toBeUndefined();
 expect(new Headers(calls[0].options.headers).get('Authorization')).toBe('Bearer test-only-token');
 expect(screen.queryByText('尚未存档')).toBeNull();
 expect(screen.getByText('所选 4 条 · 已尝试 2 条 · 尚未执行 2 条')).toBeTruthy();
});

it('当前成果、会话、报告版本数量独立，合法零与发布版本可解释',async()=>{
 credentials.set('test-only-token','operator');mount();
 const panel=await screen.findByLabelText('数据库持久交付状态');
 await screen.findByText('SQL来源 291 条 · 所选候选 0 条');
 const lists=within(panel).getAllByRole('definition');
 expect(lists.map(x=>x.textContent)).toEqual(['291','0','0','0','294','0','0','0','299','0','0','0']);
 expect(screen.getByText('该报告当前PDF版本全部任务')).toBeTruthy();
 expect(screen.getByText(/发布数量核对数据集版本：3/)).toBeTruthy();
 expect(screen.queryByText('已发布')).toBeNull();
});

it('未确定身份时保持未知数量，不自动选择报告或发起交付',async()=>{
 credentials.set('test-only-token','operator');respond=async()=>json({status:'unknown',reason_codes:['report_selection_required'],
  current_revision_counts:counts(null),session_counts:counts(null),report_counts:counts(null)});
 mount();await screen.findByText('同一来源存在多个报告身份。请在交付表单明确选择已登记报告，再核对存档。');
 expect(screen.getByText('存档状态待核对')).toBeTruthy();
 expect(screen.getAllByText('尚未核对')).toHaveLength(12);
 expect(screen.queryByText('0')).toBeNull();expect(calls.every(c=>!c.options.body)).toBe(true);
});

it('数据库已提交但回执不明时不宣布交付成功',async()=>{
 credentials.set('test-only-token','operator');respond=async()=>json({...saved,status:'committed_without_acknowledgement',
  jobs:[],reason_messages:[{code:'receipt_unconfirmed',label_zh:'数据库批次存在，交付回执尚未确认'}]});
 mount();await screen.findByText('SQL已提交，回执待确认');
 expect(screen.getByText('数据库批次存在，交付回执尚未确认')).toBeTruthy();
 expect(screen.queryByText('来源已存档')).toBeNull();
});

it('权限拒绝不降级为未入库零，并保持接口错误可见',async()=>{
 credentials.set('test-only-token','operator');respond=async()=>json({detail:'当前操作员无读取权限'},403);
 mount();await screen.findByText(/当前无法刷新存档状态/);
 expect(screen.getByText('存档状态待核对')).toBeTruthy();
 expect(screen.getAllByText('尚未核对')).toHaveLength(12);
 expect(screen.queryByText('0')).toBeNull();expect(screen.queryByText('尚未存档')).toBeNull();
});

it('断网后保留带核对时间的旧结果并明确当前未知，恢复后重新查询',async()=>{
 credentials.set('test-only-token','operator');mount();await screen.findByText('SQL来源 291 条 · 所选候选 0 条');
 respond=async()=>{throw new Error('网络连接中断');};fireEvent.click(screen.getByRole('button',{name:'重新核对'}));
 await screen.findByText(/以下保留上次核对结果，不能视为当前状态/);
 expect(screen.getByText('存档状态待核对')).toBeTruthy();expect(screen.getByText(/上次核对：/)).toBeTruthy();
 expect(screen.getByText('SQL来源 291 条 · 所选候选 0 条')).toBeTruthy();
 respond=async()=>json({...saved,current_revision_counts:counts(292)});
 fireEvent.click(screen.getByRole('button',{name:'重新核对'}));
 await waitFor(()=>expect(currentSource()).toBe('292'));
 expect(screen.queryByText(/以下保留上次核对结果/)).toBeNull();expect(calls).toHaveLength(3);
});

it('重新进入页面重新读取持久状态，不依赖前一次交付组件的内存',async()=>{
 credentials.set('test-only-token','operator');const first=mount();
 await screen.findByText('SQL来源 291 条 · 所选候选 0 条');first.unmount();
 respond=async()=>json({...saved,current_revision_counts:counts(292)});mount();
 await waitFor(()=>expect(currentSource()).toBe('292'));
 expect(calls).toHaveLength(2);
});

it('切换报告身份会立即清除旧数量，并拒绝迟到的旧请求结果',async()=>{
 credentials.set('test-only-token','operator');let finishOld:(value:Response)=>void=()=>{};
 respond=async url=>url.endsWith('report_id=old')?new Promise(resolve=>{finishOld=resolve;}):json({...saved,current_revision_counts:counts(7),jobs:[]});
 const view=mount({reportId:'old'});await waitFor(()=>expect(calls).toHaveLength(1));
 view.rerender(<WorkspaceProvider><ReadStatus reportId="new"/></WorkspaceProvider>);
 await waitFor(()=>expect(currentSource()).toBe('7'));
 finishOld(json(saved));await new Promise(resolve=>setTimeout(resolve,0));
 expect(currentSource()).toBe('7');
});
