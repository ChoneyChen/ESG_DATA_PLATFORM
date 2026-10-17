import {useState} from 'react';
import {credentials,request,type Row} from '../api';
import {useWorkspace} from '../state';
import {Field,Notice} from './ui';

const permissions:Record<string,string>={read:'查看候选数据与证据',review:'审核提取结果',publish:'发布数据到展示平台',admin:'启动生产任务、删除资产与任务'};
export const localCredentialCommand='pbcopy < "PlatESG/.local/review-token"';
export function normalizeCredential(value:string){return value.trim().replace(/^Bearer(?:\s+|$)/i,'').trim();}

export function OperatorConnection({onClose}:{onClose:()=>void}){
 const {authChanged}=useWorkspace();
 const [value,setValue]=useState(''),[message,setMessage]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [principal,setPrincipal]=useState<Row|undefined>(credentials.operator?{operator_ref:credentials.operator,roles:credentials.roles}:undefined);
 async function connect(){
  const key=normalizeCredential(value);if(!key)return;
  setBusy(true);setMessage('');setError('');credentials.set(key,'');
  try{
   const me=await request('publication','/internal/v1/me');
   if(typeof me.operator_ref!=='string'||!Array.isArray(me.roles)||me.roles.some((role:unknown)=>typeof role!=='string'))throw new Error('服务没有返回有效操作员身份，请核对发布服务配置。');
   credentials.set(key,me.operator_ref,me.roles);setPrincipal(me);setValue('');authChanged();setMessage('操作权限已启用，可以启动任务及管理记录。');
  }catch(e){credentials.clear();setPrincipal(undefined);authChanged();setError((e as Error).message);}
  finally{setBusy(false);}
 }
 return <section className="auth-panel" aria-label="工作台操作权限">
  <div className="section-title"><h2>启用操作权限</h2><button className="ghost" onClick={onClose}>关闭</button></div>
  <p>查看报告资产、OCR 和处理状态无需 token。启动生产任务、删除记录、审核或发布数据时，需要用本机 token 启用相应操作权限。它不是模型 API key；模型凭据在模型配置中设置。</p>
  <details open={!principal}><summary>取得本机 token</summary>
   <ol><li>在 <strong>ESG_DATA_PLATFORM 项目根目录</strong>打开终端。</li><li>执行下面的命令，将本机密钥复制到剪贴板。</li><li>粘贴到下方输入框，点击“启用权限”。</li></ol>
   <div className="button-row"><code>{localCredentialCommand}</code><button type="button" className="secondary" onClick={async()=>{try{if(!navigator.clipboard)throw new Error('当前浏览器不支持复制，请手动复制下面的命令。');await navigator.clipboard.writeText(localCredentialCommand);setMessage('终端命令已复制。在项目根目录执行后，再粘贴密钥。');}catch(e){setError((e as Error).message);}}}>复制终端命令</button></div>
   <p className="muted">密钥文件：PlatESG/.local/review-token。文件不存在时先启动数据平台。若使用自定义操作员，请使用管理员分配的访问密钥。</p>
  </details>
  <form onSubmit={e=>{e.preventDefault();void connect();}}>
   <Field label="本机操作 token" hint="仅在当前页面保存。启用后各功能会根据你的权限开放操作。"><input aria-label="本机操作 token" type="password" autoComplete="off" value={value} onChange={e=>setValue(e.target.value)} required placeholder="粘贴本机或管理员分配的访问密钥"/></Field>
   <div className="button-row"><button disabled={busy||!normalizeCredential(value)}>{busy?'验证中…':'启用权限'}</button><button type="button" className="secondary" onClick={()=>{credentials.clear();setPrincipal(undefined);setValue('');setError('');authChanged();setMessage('已退出操作权限，本页临时凭据已清空。');}}>退出操作权限</button></div>
  </form>
  {principal&&<div><p>当前操作员：{principal.operator_ref==='local-reviewer'?'本机操作员':principal.operator_ref}</p><ul>{principal.roles.map((role:string)=><li key={role}>{permissions[role] || role}</li>)}</ul></div>}
  {message&&<Notice tone="success">{message}</Notice>}{error&&<Notice tone="error">{error}</Notice>}
  <p className="muted">刷新页面后需重新启用操作权限；浏览数据展示网站无需 token。</p>
 </section>;
}
