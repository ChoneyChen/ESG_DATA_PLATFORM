import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {WorkspaceProvider} from '../state';
import {Extractions} from './Extractions';
import type {Row} from '../api';
let routes:Record<string,Row>;
beforeEach(()=>{
 routes={'/document/api/pipeline/extraction-profiles/platform-major?topics=E1,E2,E3':{
  overall_source_datapoint_ids:[],pending_source_datapoint_ids:[],output_count:0,session_standards:[]}};
 vi.stubGlobal('EventSource',class{addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(routes[String(url)] || {}),{headers:{'Content-Type':'application/json'}})));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

it('创建任务明确完整的每包资源限额及独立的报告执行时限',async()=>{
 render(<WorkspaceProvider><Extractions newTask params={new URLSearchParams()}/></WorkspaceProvider>);
 for(const name of ['每包模型调用上限','每包协议修复上限','每包输入 token 上限','每包输出 token 上限','整份报告总执行时限（秒）','单调用时限（秒）'])expect(await screen.findByLabelText(name)).toBeTruthy();
 expect(screen.getByText(/不按包数拆分/)).toBeTruthy();
});

it('阶段页使用独立model_budget，报告时间用量不会冒充模型用量',async()=>{
 routes['/document/api/report-sessions/synthetic-session']={session_id:'synthetic-session',name:'合成包预算任务',execution_status:'partial',available_actions:[],
  execution_budget:{used_seconds:777,max_seconds:900},
  model_budget:{scope:'report_summary',policy:'independent-standard-package-v1',enforces_report_limit:false,
   usage:{model_calls:2,format_repairs:0,input_tokens:95,output_tokens:8,unfinished_calls:0},
   packages:[{job_id:'job-b',package_id:'E2-4',limits:{max_model_calls:10,max_format_repairs:0,max_input_tokens:100,max_output_tokens:50},usage:{model_calls:2,format_repairs:0,input_tokens:95,output_tokens:8}}]}};
 render(<WorkspaceProvider><Extractions sessionId="synthetic-session" params={new URLSearchParams()}/></WorkspaceProvider>);
 fireEvent.click(await screen.findByRole('tab',{name:'阶段与运行'}));
 expect(screen.getByRole('heading',{name:'每包独立模型预算'})).toBeTruthy();
 expect(screen.getByText('输入 token累计 95')).toBeTruthy();
 expect(screen.getByText('未启用修复')).toBeTruthy();
 expect(screen.queryByText('模型调用累计 777')).toBeNull();
});
