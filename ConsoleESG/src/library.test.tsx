import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {WorkspaceProvider} from './state';
import {credentials,type Row} from './api';
import {Reports} from './pages/Reports';
import {OcrLibrary} from './pages/OcrLibrary';
import {SidebarMonitor} from './components/SidebarMonitor';
import {startLibraryProduction} from './library-workflow';
import {groupReportLibrary} from './library-resource';

const asset={asset_id:'pdf-synthetic',file_name:'合成报告.pdf',sha256:'a'.repeat(64),size_bytes:1000,lifecycle:{revision:0}};
const ocr={run_id:'ocr-synthetic',document_label:'合成OCR',page_count:4,status:'done',source_pdf_sha256:asset.sha256,ir_versions:[{run_id:'ir-synthetic',status:'ready'}]};
const metric={metric_id:'original-metric',source_datapoint_id:'E1-6_07'};
const snapshot={source_datapoint_ids:['E1-6_07'],package_digests:{'esrs.e1-6':'b'.repeat(64)}};
const profile={output_count:1,selection_snapshot:snapshot,session_standards:[{package_id:'esrs.e1-6',metric_ids:['original-metric']}]};
const catalog={packages:[{package_id:'esrs.e1-6',package_version:'2.1.2',source_digest:'b'.repeat(64),metric_summaries:[metric]}]};
let calls:{url:string,method:string,body?:Row}[];
const json=(value:unknown)=>Promise.resolve(new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}}));
beforeEach(()=>{calls=[];credentials.clear();window.location.hash='';vi.stubGlobal('EventSource',class {addEventListener(){} close(){}});vi.stubGlobal('fetch',vi.fn((url:string,options:RequestInit={})=>{const body=options.body?JSON.parse(String(options.body)):undefined;calls.push({url,method:options.method || 'GET',body});
 if(url.includes('/production-automations/preview'))return json({can_submit:true,preview_id:`automation-preview-${'c'.repeat(64)}`,items:[]});
 if(url==='/document/api/production-automations')return json({automation_id:'synthetic-parent',status:'active'});
 if(url.includes('extraction-profiles'))return json(profile);
 if(url.includes('standards/catalog'))return json(catalog);
 if(url==='/document/api/report-library')return json({items:[{...asset,ocr_versions:[ocr,{...ocr,run_id:'ocr-second',ir_versions:[]}],ocr_count:2}],unlinked_ocr:[{...ocr,run_id:'ocr-orphan',source_pdf_sha256:'f'.repeat(64),ir_versions:[]}]});
 if(url==='/document/api/pdf-library')return json({schema_version:'pdf-library-v1',items:[asset]});
 if(url==='/document/api/ocr-library')return json({schema_version:'ocr-library-v1',items:[{run_id:'ocr-synthetic',document_label:'合成OCR',page_count:4,status:'done'}]});
 if(url==='/document/api/processing-status')return json({queued_count:1,running_count:0,active_tasks:[{task_id:'synthetic-task',title:'正在识别合成PDF',status:'queued',stage:'ocr'}],recent_tasks:[]});
 return json({});}));});
afterEach(()=>{cleanup();vi.unstubAllGlobals();credentials.clear();});
const mount=(node:React.ReactNode)=>render(<WorkspaceProvider>{node}</WorkspaceProvider>);
it('统一资产中心按PDF显示多个OCR及其结构版本，不加载聚合审核成果',async()=>{
 mount(<Reports/>);await screen.findByText('ocr-second');
 expect(screen.getByRole('heading',{name:'报告资产'})).toBeTruthy();
 const parent=screen.getByLabelText('合成报告.pdf 的读取版本');
 expect(within(parent).getByText('ocr-synthetic')).toBeTruthy();expect(within(parent).getByText('ocr-second')).toBeTruthy();expect(within(parent).getByText('ir-synthetic')).toBeTruthy();
 expect(screen.queryByText('显示已归档')).toBeNull();expect(calls.some(c=>c.url.includes('/console/reports')||c.url.includes('/report-sessions'))).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'按此版本选择指标'}));expect(window.location.hash).toBe('#/extractions/new?asset=pdf-synthetic&ir=ir-synthetic');
});
it('缺少关联的OCR单独展示，不冒充任意PDF的子版本',async()=>{
 mount(<Reports/>);await screen.findByText('ocr-second');
 expect(screen.getByRole('heading',{name:'未关联原 PDF · 1 个页面读取版本'})).toBeTruthy();
 expect(within(screen.getByLabelText('合成报告.pdf 的读取版本')).queryByText('ocr-orphan')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'收起 合成报告.pdf 的读取版本'}));
 expect(screen.queryByText('ocr-second')).toBeNull();expect(screen.getByText('ocr-orphan')).toBeTruthy();
});
it('旧OCR列表入口打开同一个资产中心',async()=>{mount(<OcrLibrary/>);await screen.findByText('ocr-synthetic');expect(screen.getByRole('heading',{name:'报告资产'})).toBeTruthy();expect(calls.some(c=>c.url==='/document/api/report-library')).toBe(true);expect(calls.some(c=>c.url==='/document/api/ocr-library')).toBe(false);});
it('兼容分组只接受明确来源身份，即使文件名相同也不误归属',()=>{
 const result=groupReportLibrary([asset],[{...ocr},{...ocr,run_id:'wrong-source',document_label:asset.file_name,source_pdf_sha256:'f'.repeat(64)},{run_id:'no-identity',document_label:asset.file_name},{...ocr,run_id:'explicit-other-asset',source_asset_id:'different'}]);
 expect(result.items[0].ocr_versions.map(r=>r.run_id)).toEqual(['ocr-synthetic']);expect(result.unlinked_ocr.map(r=>r.run_id)).toEqual(['wrong-source','no-identity','explicit-other-asset']);
});
it('新资产接口返回500时显示错误，不偷偷换旧接口隐藏故障',async()=>{
 const normal=globalThis.fetch;vi.stubGlobal('fetch',vi.fn((url:string,options:RequestInit={})=>url==='/document/api/report-library'?Promise.resolve(new Response(JSON.stringify({detail:{code:'internal_service_error',message:'服务内部问题'}}),{status:500,headers:{'Content-Type':'application/json'}})):normal(url,options)));
 mount(<Reports/>);await screen.findByRole('alert');expect(screen.queryByText('合成报告.pdf')).toBeNull();expect(calls.some(c=>c.url==='/document/api/pdf-library'||c.url==='/document/api/ocr-library'||c.url==='/document/api/report-assets')).toBe(false);
});
it('侧栏运行监视显示队列数量，展开真实任务，不派发任务',async()=>{mount(<SidebarMonitor/>);await waitFor(()=>expect(screen.getByRole('button',{name:'展开处理状态'}).textContent).toContain('1'));expect(screen.queryByRole('dialog')).toBeNull();fireEvent.click(screen.getByRole('button',{name:'展开处理状态'}));expect(screen.getByRole('dialog',{name:'处理状态'})).toBeTruthy();expect(screen.getAllByText('正在识别合成PDF').length).toBeGreaterThan(0);expect(calls.every(c=>c.method==='GET')).toBe(true);});
it('直接主要指标提取复用生产计划，保留原号及快照，仅提交一个父计划',async()=>{credentials.set('synthetic-token','operator',['admin']);await startLibraryProduction([asset],'processing_extraction',profile,catalog);const preview=calls.find(c=>c.url.includes('/preview'))!.body!;expect(preview.target).toBe('processing_extraction');expect(preview.processing.mode).toBe('auto');expect(preview.extraction.selection_snapshot).toEqual(snapshot);expect(preview.extraction.standards[0].metric_ids).toEqual(['original-metric']);expect(preview.extraction.completeness).toBe('balanced');expect(preview.processing.config.execute_optional_reviews).toBe(false);expect(preview.processing.config.execute_required_reviews).toBe(false);expect(calls.filter(c=>c.url==='/document/api/production-automations')).toHaveLength(1);expect(calls.some(c=>c.url.includes('archive'))).toBe(false);});
it('已有读取版本时直接提取，读取在提取内部自动复用',async()=>{
 credentials.set('synthetic-token','operator',['admin']);mount(<Reports/>);await screen.findByText('ocr-second');
 fireEvent.click(screen.getByRole('button',{name:'直接提取主要指标'}));
 await waitFor(()=>expect(calls.some(c=>c.url==='/document/api/production-automations')).toBe(true));
 const preview=calls.find(c=>c.url.includes('/preview'))!.body!;expect(preview.target).toBe('processing_extraction');expect(calls.filter(c=>c.url==='/document/api/production-automations')).toHaveLength(1);
});
it('显示后端真实的待删除状态，不把202当作已经删除',async()=>{
 const normal=globalThis.fetch;vi.stubGlobal('fetch',vi.fn((url:string,options:RequestInit={})=>url==='/document/api/report-library'?json({items:[{...asset,ocr_versions:[],ocr_count:0}],pending_deletions:[{kind:'pdf',identity:asset.asset_id,status:'deletion_pending'}],unlinked_ocr:[]}):normal(url,options)));
 mount(<Reports/>);await screen.findByText(/正在停止关联任务，退出后自动删除/);expect(screen.getByRole('button',{name:'提取指标'})).toBeTruthy();expect(screen.queryByText('已删除')).toBeNull();expect(calls.every(c=>c.method==='GET')).toBe(true);
});
it('未连接权限时不发任务',async()=>{await expect(startLibraryProduction([asset],'processing')).rejects.toThrow(/权限|连接/);expect(calls).toHaveLength(0);});
