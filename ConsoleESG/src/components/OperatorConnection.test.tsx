import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {credentials,request} from '../api';
import {OperatorConnection,localCredentialCommand,normalizeCredential} from './OperatorConnection';
vi.mock('../state',()=>({useWorkspace:()=>({authChanged:vi.fn()})}));
beforeEach(()=>{credentials.clear();});
afterEach(()=>{cleanup();credentials.clear();vi.restoreAllMocks();vi.unstubAllGlobals();});
function respond(data:unknown,status=200){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});}

describe('明确的工作台连接入口',()=>{
 it('解释本机密钥与模型密钥的区别，复制的只是终端命令',async()=>{
  const copy=vi.fn().mockResolvedValue(undefined);Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:copy}});
  credentials.set('private-memory-value','actor');render(<OperatorConnection onClose={()=>{}}/>);
  expect(screen.getByText(/它不是模型 API key/)).toBeTruthy();fireEvent.click(screen.getByRole('button',{name:'复制终端命令'}));
  await waitFor(()=>expect(copy).toHaveBeenCalledWith(localCredentialCommand));expect(copy).not.toHaveBeenCalledWith(credentials.token);
 });
 it('验证服务身份和权限后连接，兼容粘贴Bearer前缀',async()=>{
  const fetch=vi.fn().mockResolvedValue(respond({operator_ref:'local-reviewer',roles:['read','admin']}));vi.stubGlobal('fetch',fetch);
  render(<OperatorConnection onClose={()=>{}}/>);fireEvent.change(screen.getByLabelText('本机操作 token'),{target:{value:' Bearer example-key '}});fireEvent.click(screen.getByRole('button',{name:'启用权限'}));
  await screen.findByText('操作权限已启用，可以启动任务及管理记录。');expect(credentials.token).toBe('example-key');expect(credentials.roles).toEqual(['read','admin']);
  const headers=new Headers(fetch.mock.calls[0][1].headers);expect(headers.get('Authorization')).toBe('Bearer example-key');expect(screen.getByText('启动生产任务、删除资产与任务')).toBeTruthy();
 });
 it('服务拒绝时清空无效凭据与此前身份',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(respond({detail:'访问密钥未获授权'},401)));render(<OperatorConnection onClose={()=>{}}/>);
  fireEvent.change(screen.getByLabelText('本机操作 token'),{target:{value:'invalid'}});fireEvent.click(screen.getByRole('button',{name:'启用权限'}));await screen.findByText('数据服务 /internal/v1/me（HTTP 401）：访问密钥未获授权');expect(credentials.token).toBe('');expect(credentials.roles).toEqual([]);
 });
 it('断开同时清理本页模型凭据',()=>{
  credentials.set('key','actor',['admin']);credentials.setSecret('qiniu_api_key','local-only');render(<OperatorConnection onClose={()=>{}}/>);fireEvent.click(screen.getByRole('button',{name:'退出操作权限'}));expect(credentials.token).toBe('');expect(credentials.secret('qiniu_api_key')).toBeNull();
 });
 it('空值或仅Bearer不作为可提交凭据',()=>{expect(normalizeCredential('Bearer  ')).toBe('');expect(normalizeCredential(' key ')).toBe('key');});
});

describe('管理请求保持同源内存凭据边界',()=>{
 for(const [service,path] of [['document','/api/production-automations/preview'],['targeted','/api/report-sessions/native/intermediates/receipts']] as const){
  it(`${path} 转发已连接的Bearer`,async()=>{credentials.set('test-key','actor',['admin']);const fetch=vi.fn().mockResolvedValue(respond({}));vi.stubGlobal('fetch',fetch);await request(service,path);expect(new Headers(fetch.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer test-key');});
 }
 it('公开产品查询不接收工作台密钥',async()=>{credentials.set('test-key','actor');const fetch=vi.fn().mockResolvedValue(respond({}));vi.stubGlobal('fetch',fetch);await request('publication','/v1/entities');expect(new Headers(fetch.mock.calls[0][1].headers).has('Authorization')).toBe(false);});
});
