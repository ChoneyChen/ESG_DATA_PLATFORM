import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {credentials,type Row} from '../api';
import {ReportSourceFacts} from './ReportSourceFacts';

vi.mock('../state',()=>({useWorkspace:()=>({auth:0})}));
vi.mock('./EvidenceExplorer',()=>({EvidenceExplorer:()=> <div>实际证据浏览器</div>}));
let respond:(url:string)=>Promise<Response>|Response,calls:string[];
const json=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
const row=(id:string):Row=>({snapshot_id:id,source_row_label:`原始表行 ${id}`,value_raw:'001.2300',mapping_state:'unmapped',identity_raw:{},physical_sources:[]});
beforeEach(()=>{credentials.set('anonymous-test-credential','test');calls=[];
 vi.stubGlobal('fetch',vi.fn((url:string)=>{calls.push(String(url));return Promise.resolve(respond(String(url)));}));
});
afterEach(()=>{cleanup();credentials.clear();vi.unstubAllGlobals();vi.restoreAllMocks();});

it('保留未映射表行和额外原始结构，不将保存来源冒充批准或发布',async()=>{
 respond=()=>json({items:[{...row('fixture-snapshot'),report_title_snapshot:'合成来源核验报告',
  source_row_label:'范围一和范围二合计',unit_raw:'万吨',provenance_status:'unbound',
  identity_raw:{business_segment:'炼化业务',extra:{site:'合成场址'}},
  evidence_location:{state:'unknown',reason:'固定来源身份待确认'}}],has_more:true,next_cursor:null});
 render(<ReportSourceFacts reportRef="fixture report/1"/>);
 expect(await screen.findByText('尚无条款投影')).toBeDefined();
 expect(calls[0]).toContain('report_ref=fixture%20report%2F1');
 expect(screen.getByText('证据尚未绑定')).toBeDefined();
 fireEvent.click(screen.getByRole('button',{name:'核对表行与属性'}));
 expect(screen.getAllByText(/001.2300/)).toHaveLength(2);
 expect(screen.getByText('炼化业务')).toBeDefined();
 expect(screen.getByText('此属性保留原始结构，请展开来源诊断核对。')).toBeDefined();
 expect(screen.getByText('固定来源身份待确认')).toBeDefined();
 expect(screen.getByText(/服务尚未提供续页凭据/)).toBeDefined();
 expect((screen.getByRole('button',{name:'加载更多'}) as HTMLButtonElement).disabled).toBe(true);
 expect(screen.queryByRole('button',{name:/批准|发布/})).toBeNull();
 expect(screen.queryByRole('link',{name:'打开原报告'})).toBeNull();
});

it('后端确认固定PDF与IR位置后才提供实际来源入口',async()=>{
 respond=()=>json({items:[{...row('located-fixture'),report_title_snapshot:'合成位置核验',source_row_label:'污染物',
  value_raw:'false',mapping_state:'has_core',provenance_status:'physical',
  physical_sources:[{pdf_page_index:89,ir_object_id:'fixture-table'}],
  evidence_location:{state:'verified',pdf_available:true,ir_available:true,asset_id:'fixture-asset',ir_run_id:'fixture-ir'}}],has_more:false,next_cursor:null});
 render(<ReportSourceFacts/>);
 fireEvent.click(await screen.findByRole('button',{name:'核对表行与属性'}));
 expect(screen.getByRole('link',{name:'打开原报告'}).getAttribute('href')).toContain('fixture-asset/file#page=90');
 expect(screen.getByText('实际证据浏览器')).toBeDefined();
 expect(screen.queryByRole('button',{name:'查看标准与延伸条款'})).toBeNull();
});

it('同报告1101行可逐页完整读取并前后浏览，累计数量不冒充报告全部表行',{timeout:20000},async()=>{
 const cursors=['opaque.first+token','opaque.second/token'];
 respond=url=>{const cursor=new URL(url,'https://fixture.invalid').searchParams.get('cursor');
  const offset=cursor===cursors[0]?500:cursor===cursors[1]?1000:0,length=offset===1000?101:500;
  return json({items:Array.from({length},(_,i)=>row(String(offset+i+1))),has_more:offset<1000,next_cursor:offset<1000?cursors[offset/500]:null});};
 render(<ReportSourceFacts reportRef="same report"/>);
 expect(await screen.findByText('原始表行 500')).toBeDefined();
 fireEvent.click(screen.getAllByRole('button',{name:'核对表行与属性'})[0]);
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}));
 expect(await screen.findByText('原始表行 1000')).toBeDefined();
 expect(screen.queryByRole('heading',{name:'原始表行 1'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}));
 expect(await screen.findByText('原始表行 1001')).toBeDefined();
 expect(screen.getByText('第 3 页 · 已读取 1101 条')).toBeDefined();
 expect(screen.getByText(/当前查询已读至末页.*不等于 PDF/)).toBeDefined();
 expect((screen.getByRole('button',{name:'加载更多'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'上一页'}));expect(screen.getByText('原始表行 501')).toBeDefined();
 fireEvent.click(screen.getByRole('button',{name:'下一页'}));expect(screen.getByText('原始表行 1001')).toBeDefined();
 expect(calls).toHaveLength(3);
 expect(calls.every(url=>new URL(url,'https://fixture.invalid').searchParams.get('report_ref')==='same report')).toBe(true);
 expect(new URL(calls[1],'https://fixture.invalid').searchParams.get('cursor')).toBe(cursors[0]);
});

it('续页错误保留当前页与所选原文，可重试同一游标，不能伪称完整',async()=>{
 let fail=true;respond=url=>url.includes('cursor=')?(fail?json({detail:{code:'invalid_cursor',message:'续页凭据属于其它报告范围'}},422):json({items:[row('tail')],has_more:false,next_cursor:null})):json({items:[{...row('first'),evidence_location:{reason:'已读取页的证据待确认'}}],has_more:true,next_cursor:'bound-cursor'});
 render(<ReportSourceFacts reportRef="report-a"/>);
 fireEvent.click(await screen.findByRole('button',{name:'核对表行与属性'}));
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}));
 expect(await screen.findByText(/续页凭据属于其它报告范围/)).toBeDefined();
 expect(screen.getByText('已读取页的证据待确认')).toBeDefined();
 expect(screen.getByText('第 1 页 · 已读取 1 条')).toBeDefined();
 expect(screen.queryByText(/已读至末页/)).toBeNull();
 fail=false;fireEvent.click(screen.getByRole('button',{name:'重试读取'}));
 expect(await screen.findByText('原始表行 tail')).toBeDefined();expect(calls[1]).toBe(calls[2]);
});

it('切换报告立即清选择和游标，迟到的旧续页不能污染新报告',async()=>{
 let finishOld:(value:Response)=>void=()=>{};
 respond=url=>url.includes('report_ref=report-b')?json({items:[{...row('new'),identity_raw:{reporting_period:'2025'}}],has_more:false,next_cursor:null}):url.includes('cursor=')?new Promise(resolve=>{finishOld=resolve;}):json({items:[{...row('first-report'),identity_raw:{reporting_period:'截至2024年12月31日',pollutant:'NOx'},evidence_location:{reason:'首份报告证据待确认'}}],has_more:true,next_cursor:'report-a-cursor'});
 const view=render(<ReportSourceFacts reportRef="report-a"/>);
 expect(await screen.findByText('截至2024年12月31日')).toBeDefined();
 fireEvent.click(screen.getByRole('button',{name:'核对表行与属性'}));
 fireEvent.click(screen.getByRole('button',{name:'加载更多'}));
 view.rerender(<ReportSourceFacts reportRef="report-b"/>);
 expect(screen.queryByText('首份报告证据待确认')).toBeNull();
 expect(await screen.findByText('2025')).toBeDefined();
 finishOld(json({items:[row('late-old')],has_more:false,next_cursor:null}));
 await waitFor(()=>expect(screen.queryByText('原始表行 late-old')).toBeNull());
 expect(calls.at(-1)).not.toContain('cursor=');expect(screen.getByText('第 1 页 · 已读取 1 条')).toBeDefined();
});

it('数据平台未连接时不请求内部来源，也不显示完整读取状态',()=>{
 credentials.clear();render(<ReportSourceFacts reportRef="report-a"/>);
 expect(screen.getByText(/数据平台未连接，暂不能浏览报告来源事实/)).toBeDefined();expect(calls).toHaveLength(0);
 expect(screen.queryByText(/已读至末页/)).toBeNull();
});

it('重新读取失败保留旧末页，重试仍请求首页，成功后清缓存和选择',async()=>{
 let refreshing=false,fail=true;
 respond=url=>url.includes('cursor=')?json({items:[row('tail')],has_more:false,next_cursor:null}):refreshing?(fail?json({detail:'刷新服务暂不可用'},503):json({items:[row('fresh')],has_more:false,next_cursor:null})):json({items:[row('first')],has_more:true,next_cursor:'next'});
 render(<ReportSourceFacts reportRef="fixed-report"/>);
 await screen.findByText('原始表行 first');fireEvent.click(screen.getByRole('button',{name:'加载更多'}));
 await screen.findByText('原始表行 tail');fireEvent.click(screen.getByRole('button',{name:'核对表行与属性'}));
 refreshing=true;fireEvent.click(screen.getByRole('button',{name:'重新读取来源事实'}));
 expect(await screen.findByText(/刷新服务暂不可用/)).toBeDefined();
 expect(screen.getByRole('heading',{name:'原始表行 tail'})).toBeDefined();
 expect(screen.getByText('第 2 页 · 已读取 2 条')).toBeDefined();
 fail=false;fireEvent.click(screen.getByRole('button',{name:'重试读取'}));
 expect(await screen.findByText('原始表行 fresh')).toBeDefined();
 expect(screen.queryByRole('heading',{name:'原始表行 tail'})).toBeNull();
 expect(screen.getByText('第 1 页 · 已读取 1 条')).toBeDefined();
 expect(calls.at(-1)).not.toContain('cursor=');
});
