// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {WorkspaceProvider} from '../state';
import {Extractions,sessionOutputCounts} from './Extractions';

const standards=Array.from({length:5},(_,index)=>({package_id:`package-${index}`,
 metric_ids:Array.from({length:index===0?4:3},(_,n)=>`metric-${index}-${n}`)}));
const session={session_id:'synthetic-count-session',name:'合成范围计数测试',request:{standards,
 extension_selections:[{extension_id:'synthetic-sum',definition_version:'1.0.0',value_origins:['reported']}],
 internal_dependencies:[{metric_ids:['not-selected-a','not-selected-b']}],internal_operand_count:2},execution_status:'cancelled'};

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('原16项与独立延伸计为17项，内部依赖不扩大所选输出',()=>{
 expect(sessionOutputCounts(session)).toEqual({originals:16,extensions:1,total:17});
});
it('重复条款与同一延伸双来源不重复计数，延伸单独选择不需要标准数组',()=>{
 expect(sessionOutputCounts({request:{standards:[standards[0],standards[0]],extension_selections:[
  {extension_id:'synthetic-sum',definition_version:'1.0.0',value_origins:['reported','derived']},
  {extension_id:'synthetic-sum',definition_version:'1.0.0',value_origins:['reported']}]}}))
  .toEqual({originals:4,extensions:1,total:5});
 expect(sessionOutputCounts({request:{extension_selections:session.request.extension_selections}}))
  .toEqual({originals:0,extensions:1,total:1});
 expect(sessionOutputCounts({})).toEqual({originals:0,extensions:0,total:0});
});
it('实际会话列表显示17个输出及原条款/延伸分项',async()=>{
 vi.stubGlobal('EventSource',class{addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(
  String(url).includes('/report-sessions?')?{sessions:[session]}:
  String(url).includes('/standalone-jobs?')?{items:[],status:'available'}:{reports:[]}
 ),{headers:{'Content-Type':'application/json'}})));
 render(<WorkspaceProvider><Extractions params={new URLSearchParams()}/></WorkspaceProvider>);
 expect(await screen.findByText('5 个标准包 · 17 个输出条款（原条款 16 项；延伸 1 项）')).toBeTruthy();
 expect(screen.queryByText(/18 个输出条款/)).toBeNull();
});
it('checkpoint处理结论为3而事实为0时，实际阶段不暗示已提取或已发布',async()=>{
 const detail={...session,stages:[{job_id:'synthetic-empty-result',disclosure_requirement:'E1-5',status:'partial',
  counts:{selected_metrics:3,processed_metrics:3,candidate_source_facts:0,core_facts:0},
  progress:{determinate:true,current:3,total:3,unit:'metrics',label:'读取与映射'}}]};
 vi.stubGlobal('EventSource',class{addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(
  String(url).endsWith('/facts')?{facts:[]}:
  String(url).endsWith('/synthetic-count-session')?detail:{sessions:[]}
 ),{headers:{'Content-Type':'application/json'}})));
 render(<WorkspaceProvider><Extractions sessionId={session.session_id} params={new URLSearchParams()}/></WorkspaceProvider>);
 fireEvent.click(await screen.findByRole('tab',{name:'阶段与运行'}));
 expect(screen.getByText('已有处理记录')).toBeTruthy();
 expect(screen.getByText('候选来源事实').nextElementSibling?.textContent).toBe('0');
 expect(screen.getByText('已保存规范事实').nextElementSibling?.textContent).toBe('0');
 expect(screen.getByText('3 / 3 条款 · 已记录处理结论不代表有可用事实或发布')).toBeTruthy();
 expect(screen.queryByText('已处理条款')).toBeNull();
});
