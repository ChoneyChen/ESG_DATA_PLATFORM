import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {WorkspaceProvider} from '../state';
import {Standards} from './Standards';

vi.mock('../components/ExtensionManagement',()=>({ExtensionManagement:()=>null}));
const id='esrs.2023-set1.e1-6',oldVersion='2.1.1',newVersion='2.1.2';
const metric=(source_datapoint_id:string,label:string)=>({metric_id:`fixture.${source_datapoint_id}`,source_datapoint_id,labels:{zh:label},disclosure_role:'anchor',measurement_contract:{positive_definition:`${label}定义`}});
const details=(version:string)=>({manifest:{package_id:id,package_version:version,status:'draft',standard:{disclosure_requirement:'E1-6'},core_source_datapoint_ids:[version===oldVersion?'E1-6_07':'E1-6_09']},
 compilation:{source_digest:(version===oldVersion?'1':'2').repeat(64)},metrics:[metric('E1-6_07',version===oldVersion?'历史范围1':'新版范围1'),metric('E1-6_09',version===oldVersion?'历史范围2':'新版范围2')],relations:[],semantic_diagnostics:version===oldVersion?[{code:'historical-definition-diagnostic',message:'旧定义保留诊断'}]:[]});
let calls:{url:string,options:RequestInit}[],oldMode:'normal'|'fail'|'wrong';
beforeEach(()=>{calls=[];oldMode='normal';
 vi.stubGlobal('EventSource',class{onopen=null;onerror=null;onmessage=null;addEventListener(){}close(){}});
 vi.stubGlobal('fetch',vi.fn((input:string,options:RequestInit={})=>{const url=String(input);calls.push({url,options});
  let data:unknown,status=200;
  if(url.endsWith('/standards/catalog'))data={packages:[{package_id:id,package_version:newVersion,disclosure_requirement:'E1-6',metric_count:2,status:'draft',validation_state:'structurally_valid',core_source_datapoint_ids:['E1-6_09'],metric_summaries:details(newVersion).metrics}],historical:[{package_id:id,package_version:oldVersion,reason:'retained'}],planned:[]};
  else if(url.includes('extraction-profiles'))data={overall_source_datapoint_ids:['E1-6_07','E1-6_09'],pending_source_datapoint_ids:[]};
  else if(url.endsWith(`/standards/${id}/${oldVersion}`)){if(oldMode==='fail'){data={detail:'所选历史固定版本暂不可读取'};status=503;}else data=details(oldMode==='wrong'?newVersion:oldVersion);}
  else if(url.endsWith(`/standards/${id}/${newVersion}`))data=details(newVersion);
  else {data={detail:`未声明的匿名接口 ${url}`};status=404;}
  return Promise.resolve(new Response(JSON.stringify(data),{status}));
 }));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
const mount=()=>render(<WorkspaceProvider><Standards/></WorkspaceProvider>);
const chooseHistory=async()=>fireEvent.click(await screen.findByRole('button',{name:`只读查看历史包 ${id} ${oldVersion}`}));

it('历史入口请求精确id/version，并显示原核心与原条款合同，不借用新版核心或批准状态',async()=>{
 mount();await chooseHistory();
 expect(await screen.findByText('历史范围1')).toBeDefined();
 expect(screen.getByText('E1-6 · v2.1.1')).toBeDefined();
 expect(screen.getByText(/正在只读查看此历史固定版本/)).toBeDefined();
 const rows=screen.getAllByRole('row');
 expect(within(rows.find(row=>within(row).queryByText('历史范围1'))!).getAllByRole('cell')[1].textContent).toBe('是');
 expect(within(rows.find(row=>within(row).queryByText('历史范围2'))!).getAllByRole('cell')[1].textContent).toBe('否');
 expect(screen.getByText('历史合同不继承当前版本验收')).toBeDefined();
 fireEvent.click(screen.getByText('历史语义诊断'));expect(screen.getByText(/旧定义保留诊断/)).toBeDefined();
 expect(calls.some(call=>call.url.endsWith(`/standards/${id}/${newVersion}`))).toBe(false);
 expect(calls.every(call=>!call.options.method||call.options.method==='GET')).toBe(true);
});

it('历史详情读取失败保持原固定身份，不退回最新版，当前目录仍可使用',async()=>{
 oldMode='fail';mount();await chooseHistory();
 expect(await screen.findByText(/所选历史固定版本暂不可读取/)).toBeDefined();
 expect(screen.getByText(`${id} @ ${oldVersion}`)).toBeDefined();
 expect(screen.queryByText('新版范围1')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:/E1-6.*2.1.2/}));
 expect(await screen.findByText('新版范围1')).toBeDefined();
 expect(screen.getByText('E1-6 · v2.1.2')).toBeDefined();
 expect(screen.queryByText(/正在只读查看此历史固定版本/)).toBeNull();
});

it('详情服务错误返回新版身份时阻止混用，不能把新版条款标作历史合同',async()=>{
 oldMode='wrong';mount();await chooseHistory();
 expect(await screen.findByText(/详情返回的包身份与所选固定版本不一致/)).toBeDefined();
 expect(screen.queryByText('新版范围1')).toBeNull();
 expect(screen.getByText(`${id} @ ${oldVersion}`)).toBeDefined();
 expect(calls.filter(call=>call.url.includes('/standards/')&&!call.url.endsWith('catalog')).map(call=>call.url)).toEqual([`/document/api/pipeline/standards/${id}/${oldVersion}`]);
});
