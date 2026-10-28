import {useState} from 'react';
import {credentials,items,navigate,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Actions,Badge,Diagnostics,Field,Notice,Progress,ResourceState,Section} from '../components/ui';
import {RecordManagement} from '../components/RecordManagement';
import {BatchRecordDelete,endedRecords} from '../components/BatchRecordDelete';
import {workActions} from '../management-api';

export function Runtime(){
 const {refresh,connected}=useWorkspace();const [statusFilter,setStatusFilter]=useState('');
 const [tab,setTab]=useState(()=>new URLSearchParams(window.location.hash.split('?')[1] || '').get('tab')==='storage'?'storage':'queue');
 const compatibility=useResource('document','/api/pipeline/compatibility'),queue=useResource('document','/api/pipeline/tasks?include_archived=true'),models=useResource('document',tab==='models'?'/api/models/status':null),status=useResource('document',tab==='models'?'/api/pipeline/status':null),storage=useResource('document',tab==='storage'?'/api/storage/inventory':null);
 const tasks=items(queue.data,'tasks').filter(task=>!statusFilter||task.status===statusFilter);
 return <><div className="page-heading"><div><span className="eyebrow">执行与资源</span><h1>运行中心</h1><p>执行队列、模型资源与存储引用。页面读取与结构证据的逐对象修复从报告详情和下方入口进入。</p></div><div className="button-row"><button className="secondary" onClick={()=>navigate('/processing')}>报告处理</button><button className="secondary" onClick={refresh}>刷新</button></div></div>
  <div className="tabs" role="tablist">{[['queue','执行队列'],['models','模型与资源'],['storage','存储引用']].map(([code,title])=><button role="tab" aria-selected={tab===code} key={code} onClick={()=>setTab(code)}>{title}</button>)}</div>
  {tab==='queue'&&<Section title="执行队列"><div className="filter-bar"><select aria-label="筛选队列状态" value={statusFilter} onChange={e=>setStatusFilter(e.target.value)}><option value="">全部状态</option><option value="queued">等待执行</option><option value="running">正在执行</option><option value="failed">失败</option><option value="interrupted">已中断</option><option value="cancelled">已取消</option><option value="completed">已完成</option></select><BatchRecordDelete rows={endedRecords(tasks)} kind="queue" idKey="task_id"/></div>
   <ResourceState error={queue.error} loading={queue.loading&&!tasks.length} empty={!tasks.length}/>
   {tasks.map(task=><article className="task-card" key={task.task_id}><div className="between"><h3>{task.title}</h3><Badge value={task.status}/></div><Progress task={task}/>{task.error_detail&&<Notice tone="error">{task.error_detail.message}</Notice>}<RecordManagement record={task} kind="queue" id={task.task_id}/><Actions actions={workActions(task.available_actions)} revision={task.revision}/><Diagnostics value={{task_id:task.task_id,operation:task.operation,source_references:task.source_references,outcome:task.outcome}} title="任务诊断"/></article>)}
  </Section>}
  {tab==='models'&&<><Section title="模型与服务资源"><ResourceState error={models.error}/><Diagnostics value={models.data} title="服务与资源状态"/></Section><Section title="平台状态"><ResourceState error={status.error}/><Diagnostics value={status.data} title="执行槽与控制状态"/></Section><RuntimeSecrets/></>}
  <details className="diagnostics"><summary>高级运行检查</summary><ResourceState error={compatibility.error}/>{compatibility.data?.ready===false&&<Notice tone="error">{compatibility.data.blockers?.join('；') || '当前服务尚未就绪'}</Notice>}<p>实时事件：{connected?'已连接':'轮询更新中'}</p><button type="button" className="text-button" onClick={()=>setTab('storage')}>查看存储引用</button><Diagnostics value={compatibility.data} title="服务兼容检查"/></details>
  {tab==='storage'&&<Section title="存储引用检查"><p>文件删除请在 PDF 库或 OCR 库操作。这里查看来源关系与文件规模。</p><ResourceState error={storage.error} loading={storage.loading&&!storage.data}/><Diagnostics value={storage.data} title="存储与依赖清单"/><button className="secondary" onClick={()=>setTab('queue')}>返回执行队列</button></Section>}
 </>;
}
function RuntimeSecrets(){const [message,setMessage]=useState('');return <Section title="本次模型凭据"><Notice>凭据仅存当前页面内存，刷新或断开连接会清空。模型参数与具体云授权由报告任务分别确认。</Notice><div className="form-grid">{[['qiniu_api_key','七牛模型密钥'],['ocr_token','OCR API 凭据']].map(([key,title])=><Field key={key} label={title}><input type="password" autoComplete="off" defaultValue="" onChange={e=>credentials.setSecret(key,e.target.value)}/></Field>)}</div><p className="muted">后端环境凭据仍由后端管理；本页不会保存到 URL 或浏览器持久存储。</p><button className="secondary" onClick={()=>{credentials.setSecret('qiniu_api_key','');credentials.setSecret('ocr_token','');setMessage('本页临时模型凭据已清空。');}}>清空本次模型凭据</button>{message&&<Notice>{message}</Notice>}</Section>;}
