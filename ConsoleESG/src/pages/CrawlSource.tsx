import {useEffect,useMemo,useState} from 'react';
import {credentials,items,request,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Badge,Field,Notice,ResourceState,Section} from '../components/ui';

const PROFILE_LABELS:Record<string,string>={
  foundation16:'基础 16 条',platform_major:'平台主要 55 条',quantitative:'定量 38 条',
  base16:'基础 16 条（兼容）',priority32:'重点 32 条',
};

function FactTable({facts}:{facts:Row[]}){
 if(!facts.length)return <p className="muted">没有匹配事实。</p>;
 return <div className="table-scroll crawl-fact-table"><table><thead><tr><th>概念</th><th>值</th><th>单位</th><th>期间 / 时点</th><th>维度</th><th>来源</th></tr></thead><tbody>
  {facts.map((fact,index)=><tr key={fact.fact_id || `${fact.concept}:${index}`}>
   <td data-label="概念"><strong className="mono">{fact.concept || '未命名概念'}</strong><small className="table-secondary">{fact.kind === 'text' ? '文本事实' : '数值事实'}{fact.esg_related ? ' · ESG 相关' : ''}</small></td>
   <td data-label="值" className="crawl-fact-value">{fact.value_numeric ?? fact.value_text ?? '—'}</td>
   <td data-label="单位" className="mono">{Array.isArray(fact.unit)?fact.unit.join(' / '):(fact.unit || '—')}</td>
   <td data-label="期间 / 时点"><small className="table-secondary">{fact.period_start ? `${fact.period_start} — ${fact.period_end || ''}` : (fact.instant || '未记录')}</small></td>
   <td data-label="维度">{Object.keys(fact.dimensions || {}).length ? <small className="table-secondary mono">{Object.entries(fact.dimensions).map(([key,value])=>`${key}=${value}`).join('；')}</small> : <span className="muted">无</span>}</td>
   <td data-label="来源"><small className="table-secondary mono">{fact.file}{fact.page_number ? ` · 第 ${fact.page_number} 页` : ''}</small></td>
  </tr>)}
 </tbody></table></div>;
}

export function CrawlSource(){
 const {refresh}=useWorkspace();
 const status=useResource('document','/api/console/crawl-index/status');
 const downloads=useResource('document','/api/console/crawl-index/downloads?limit=1000');
 const settingsResource=useResource('document','/api/console/crawl-intake/settings');
 const [tab,setTab]=useState('source'),[query,setQuery]=useState(''),[selected,setSelected]=useState('');
 const [factsQuery,setFactsQuery]=useState(''),[facts,setFacts]=useState<Row|null>(null),[factsBusy,setFactsBusy]=useState(false);
 const [scan,setScan]=useState<Row|null>(null),[scanBusy,setScanBusy]=useState(false);
 const [form,setForm]=useState<Row|null>(null),[settingsMessage,setSettingsMessage]=useState(''),[settingsBusy,setSettingsBusy]=useState(false);
 const runner=useResource('document','/api/console/crawl-runner/status');
 const [jobId,setJobId]=useState(''),[runMessage,setRunMessage]=useState(''),[runBusy,setRunBusy]=useState(false);
 const [runForm,setRunForm]=useState<Row>({command:'plan',years:'2024',limit:'1',workers:'4',no_esg:false,confirm:false});
 const jobDetail=useResource('document',jobId?`/api/console/crawl-runner/jobs/${encodeURIComponent(jobId)}`:null);
 const activeJob=(runner.data?.jobs || []).find((job:Row)=>job.status==='running');
 const packageSummary=useResource('document',selected?`/api/console/esef-packages/${encodeURIComponent(selected)}`:null);
 useEffect(()=>{if(settingsResource.data)setForm({...settingsResource.data});},[settingsResource.data]);
 useEffect(()=>{setFacts(null);setFactsQuery('');},[selected]);
 useEffect(()=>{if(!activeJob&&!jobId)return;const timer=setInterval(()=>{runner.retry();if(jobId)jobDetail.retry();},4000);return()=>clearInterval(timer);},[activeJob?.job_id,jobId,runner.retry,jobDetail.retry]);
 const packages=items(downloads.data);
 const visible=useMemo(()=>{const needle=query.trim().toLowerCase();return packages.filter(item=>!needle||`${item.entity_name || ''} ${item.lei || ''} ${item.source_report_id || ''} ${item.period_end || ''}`.toLowerCase().includes(needle));},[packages,query]);
 const packageCount=status.data?.downloaded_count ?? packages.length;
 const indexCount=status.data?.record_count ?? 0;

 async function saveSettings(){
  if(!form)return;
  setSettingsBusy(true);setSettingsMessage('');
  try{
   const payload:Row={};
   for(const key of ['enabled','watch_downloads','auto_read_esef','auto_create_report','auto_queue_extraction',
    'interval_minutes','country','years','esg_only','extraction_profile'])payload[key]=form[key];
   const saved=await request('document','/api/console/crawl-intake/settings',{method:'PUT',body:JSON.stringify(payload)});
   setForm({...saved});setSettingsMessage('采集设置已保存。执行 worker 尚未启用，扫描仍只生成计划。');refresh();
  }catch(error){setSettingsMessage((error as Error).message);}
  finally{setSettingsBusy(false);}
 }
 async function runScan(markSeen:boolean){
  setScanBusy(true);
  try{setScan(await request('document',`/api/console/crawl-intake/scan?mark_seen=${markSeen?'true':'false'}`,{method:'POST',body:'{}'}));}
  catch(error){setScan({error:(error as Error).message});}
  finally{setScanBusy(false);}
 }
 async function searchFacts(){
  if(!selected)return;
  setFactsBusy(true);
  try{setFacts(await request('document',`/api/console/esef-packages/${encodeURIComponent(selected)}/facts?query=${encodeURIComponent(factsQuery)}&limit=50`));}
  catch(error){setFacts({error:(error as Error).message});}
  finally{setFactsBusy(false);}
 }
 async function startRunnerJob(){
  setRunBusy(true);setRunMessage('');
  try{
   const years=String(runForm.years || '').split(/[^0-9]+/).filter(Boolean).map(Number);
   const body:Row={command:runForm.command,years,limit:runForm.limit?Number(runForm.limit):null,
    workers:Number(runForm.workers || 4),no_esg:Boolean(runForm.no_esg),confirm:Boolean(runForm.confirm)};
   const job=await request('document','/api/console/crawl-runner/jobs',{method:'POST',body:JSON.stringify(body)});
   setJobId(job.job_id);setRunMessage(`已启动 ${job.command}，任务 ${job.job_id}。`);runner.retry();
  }catch(error){setRunMessage((error as Error).message);}
  finally{setRunBusy(false);}
 }
 async function stopRunnerJob(){
  if(!jobId)return;
  setRunBusy(true);
  try{const job=await request('document',`/api/console/crawl-runner/jobs/${encodeURIComponent(jobId)}/stop`,{method:'POST',body:'{}'});setRunMessage(`任务状态：${job.status}`);jobDetail.retry();runner.retry();}
  catch(error){setRunMessage((error as Error).message);}
  finally{setRunBusy(false);}
 }
 function patch(key:string,value:unknown){setForm(current=>current?{...current,[key]:value}:current);}
 function patchRun(key:string,value:unknown){setRunForm(current=>({...current,[key]:value}));}

 return <>
  <div className="page-heading"><div><span className="eyebrow">报告来源</span><h1>CrawlESG 报告源</h1><p>只读索引、下载台账与 ESEF 包状态。CrawlESG 负责采集，本链路负责读取与后续提取，边界保持独立。</p></div><div className="button-row"><button className="secondary" onClick={()=>{status.retry();downloads.retry();runner.retry();}}>刷新来源</button></div></div>
  <div className="stat-grid crawl-kpis">
   <article><small>索引记录</small><strong>{status.error?'—':indexCount}</strong></article>
   <article><small>已下载 ESEF 包</small><strong>{downloads.error?'—':packageCount}</strong></article>
   <article><small>外部来源</small><strong>{status.data?.available?'只读':'未连接'}</strong></article>
   <article><small>产物类型</small><strong>{status.data?.artifact_kind || 'ESEF_ZIP'}</strong></article>
  </div>
  <div className="tabs" role="tablist">
   <button role="tab" aria-selected={tab==='source'} onClick={()=>setTab('source')}>索引与包</button>
   <button role="tab" aria-selected={tab==='automation'} onClick={()=>setTab('automation')}>自动采集规划</button>
   <button role="tab" aria-selected={tab==='runner'} onClick={()=>setTab('runner')}>运行控制</button>
  </div>
  <ResourceState error={status.error||downloads.error}/>
  {tab==='source'&&<>
   <details className="crawl-connection" open><summary><span>连接状态</span><small>{status.data?.ledger_path || '下载台账未配置'}</small></summary>
    <dl className="facts"><dt>索引文件</dt><dd className="mono">{status.data?.index_path || '未配置'}</dd><dt>下载台账</dt><dd className="mono">{status.data?.ledger_path || '未配置'}（{status.data?.download_ledger_available?'可读':'不可读'}）</dd><dt>包目录</dt><dd className="mono">{status.data?.package_dir || '未配置'}</dd><dt>边界</dt><dd>ESEF ZIP 不是 PDF；不自动导入、不自动建报告身份。</dd></dl>
   </details>
   <Section title="已下载包" action={<span className="count">{visible.length} 个</span>}>
    <div className="filter-bar"><input aria-label="搜索 Crawl 包" placeholder="企业、LEI、报告编号或期末" value={query} onChange={event=>setQuery(event.target.value)}/><span className="muted crawl-inline-note">只读取台账与包存在性；包内容由下方 ESEF 读取器解析。</span></div>
    <ResourceState empty={!visible.length}/>
    <div className="table-scroll crawl-package-table"><table><thead><tr><th>报告主体</th><th>报告编号</th><th>财年 / 期末</th><th>包状态</th><th>ESG</th><th>操作</th></tr></thead><tbody>
     {visible.map(item=><tr key={item.source_report_id}>
      <td data-label="报告主体"><strong>{item.entity_name || '未命名主体'}</strong><small className="table-secondary mono">{item.lei || '无 LEI'}</small></td>
      <td data-label="报告编号" className="mono">{item.source_report_id}</td>
      <td data-label="财年 / 期末">{item.fiscal_year || '—'}<small className="table-secondary">{item.period_end || '期末未记录'}</small></td>
      <td data-label="包状态"><span className={item.package_available?'chip ready':'chip'}>{item.package_available?`已下载${item.package_size_mb?` · ${item.package_size_mb} MB`:''}`:item.download_status}</span><small className="table-secondary mono crawl-filename" title={item.package_filename || ''}>{item.package_filename || '台账未声明文件'}</small></td>
      <td data-label="ESG">{item.esg_flag===true?'ESG 标记':item.esg_flag===false?'未标记':'—'}</td>
      <td data-label="操作"><button type="button" className="secondary" disabled={!item.package_available} onClick={()=>setSelected(item.source_report_id)}>读取 ESEF</button></td>
     </tr>)}
    </tbody></table></div>
   </Section>
   {selected&&<Section title={`ESEF 读取 · ${selected}`} action={<button className="text-button" onClick={()=>setSelected('')}>关闭</button>}>
    <ResourceState error={packageSummary.error} loading={packageSummary.loading&&!packageSummary.data}/>
    {packageSummary.data&&<>
     <Notice>{packageSummary.data.package?.artifact_kind==='ESEF_ZIP'?'这是 ESEF ZIP 的只读解析结果，不是 PDF 资产；不会自动创建候选或报告身份。':'只读包读取结果。'}</Notice>
     <div className="stat-grid crawl-esef-stats">
      <article><small>事实</small><strong>{packageSummary.data.stats?.facts ?? 0}</strong></article>
      <article><small>数值 / 文本</small><strong>{packageSummary.data.identity?.numeric_facts ?? 0} / {packageSummary.data.identity?.text_facts ?? 0}</strong></article>
      <article><small>ESG 相关</small><strong>{packageSummary.data.identity?.esg_related_facts ?? 0}</strong></article>
      <article><small>上下文 / 单位</small><strong>{packageSummary.data.stats?.contexts ?? 0} / {packageSummary.data.stats?.units ?? 0}</strong></article>
     </div>
     <dl className="facts"><dt>主体标识</dt><dd className="mono">{(packageSummary.data.identity?.entity_identifiers || []).join('、') || '未识别'}</dd><dt>报告期</dt><dd>{packageSummary.data.identity?.period_start || '—'} — {packageSummary.data.identity?.period_end || '—'}</dd><dt>包 SHA-256</dt><dd className="mono">{packageSummary.data.package?.sha256}</dd><dt>报告文件</dt><dd className="mono">{(packageSummary.data.package?.report_files || []).join('、')}</dd></dl>
     <form className="crawl-fact-search" onSubmit={event=>{event.preventDefault();void searchFacts();}}><input aria-label="搜索 ESEF 事实" placeholder="概念、值或上下文" value={factsQuery} onChange={event=>setFactsQuery(event.target.value)}/><button className="secondary" disabled={factsBusy}>{factsBusy?'读取中…':'搜索事实'}</button><button type="button" className="ghost" onClick={()=>{setFactsQuery('');setFacts(null);}}>清空</button></form>
     {facts?.error?<Notice tone="error">{facts.error}</Notice>:<FactTable facts={(facts?.items || packageSummary.data.sample_facts || []) as Row[]}/>}
     <small className="muted">{facts?.total!=null?`匹配 ${facts.total} 条，显示前 ${(facts.items||[]).length} 条`:`显示样例 ${(packageSummary.data.sample_facts||[]).length} 条`} · 结果按包摘要缓存，重复读取不重新解析。</small>
    </>}
   </Section>}
  </>}
  {tab==='automation'&&<>
   <Section title="自动采集设置（预留执行接口）">
    <Notice>设置与扫描计划独立于 CrawlESG 和工作台提取链。当前不会自动下载、读取、调用模型或写候选；扫描只读取下载台账并生成计划，需接入执行 worker 后才会派发。</Notice>
    {!form?<ResourceState loading={settingsResource.loading} error={settingsResource.error}/>:<form className="crawl-settings" onSubmit={event=>{event.preventDefault();void saveSettings();}}>
     <div className="crawl-toggle-grid">
      {[['enabled','总开关'],['watch_downloads','监视下载台账'],['auto_read_esef','自动读取 ESEF'],['auto_create_report','自动建报告身份草稿'],['auto_queue_extraction','自动送提取计划'],['esg_only','只处理 ESG 标记包']].map(([key,label])=><label key={key} className="crawl-toggle"><input type="checkbox" checked={Boolean(form[key])} onChange={event=>patch(key,event.target.checked)}/><span>{label}</span></label>)}
     </div>
     <div className="form-grid">
      <Field label="扫描间隔（分钟）"><input type="number" min={5} max={10080} value={form.interval_minutes ?? 60} onChange={event=>patch('interval_minutes',Number(event.target.value))}/></Field>
      <Field label="国家代码"><input value={form.country || ''} onChange={event=>patch('country',event.target.value.toUpperCase())} placeholder="GB"/></Field>
      <Field label="财年（逗号分隔）"><input value={(form.years || []).join(',')} onChange={event=>patch('years',event.target.value.split(/[^0-9]+/).filter(Boolean).map(Number))} placeholder="2024,2025"/></Field>
      <Field label="提取配置"><select value={form.extraction_profile || 'foundation16'} onChange={event=>patch('extraction_profile',event.target.value)}>{Object.entries(PROFILE_LABELS).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></Field>
     </div>
     <div className="button-row"><button disabled={settingsBusy||!credentials.ready}>{settingsBusy?'保存中…':'保存设置'}</button><button type="button" className="secondary" disabled={scanBusy} onClick={()=>void runScan(false)}>{scanBusy?'扫描中…':'扫描新增（只读）'}</button><button type="button" className="ghost" disabled={scanBusy} onClick={()=>void runScan(true)}>扫描并记录已见</button></div>
     {settingsMessage&&<Notice>{settingsMessage}</Notice>}
    </form>}
   </Section>
   {scan&&<Section title="扫描计划">
    {scan.error?<Notice tone="error">{scan.error}</Notice>:<>
     <div className="stat-grid"><article><small>新增包</small><strong>{(scan.new_packages||[]).length}</strong></article><article><small>符合筛选</small><strong>{(scan.candidates||[]).length}</strong></article><article><small>被筛选排除</small><strong>{(scan.filtered_out||[]).length}</strong></article><article><small>计划动作</small><strong>{(scan.actions||[]).length}</strong></article></div>
     <Notice>{scan.note}</Notice>
     {(scan.actions||[]).length>0&&<div className="table-scroll"><table><thead><tr><th>动作</th><th>报告编号</th><th>配置 / 包</th><th>说明</th></tr></thead><tbody>{scan.actions.map((action:Row,index:number)=><tr key={`${action.action}:${action.source_report_id}:${index}`}><td><Badge value={action.action}/></td><td className="mono">{action.source_report_id}</td><td>{action.profile || action.package_filename || '—'}</td><td>{action.reason}</td></tr>)}</tbody></table></div>}
     {(scan.new_packages||[]).length>0&&<details><summary>新增包明细</summary><div className="table-scroll"><table><thead><tr><th>编号</th><th>主体</th><th>财年</th><th>包</th></tr></thead><tbody>{scan.new_packages.map((item:Row)=><tr key={item.source_report_id}><td className="mono">{item.source_report_id}</td><td>{item.entity_name}</td><td>{item.fiscal_year}</td><td className="mono">{item.package_filename}</td></tr>)}</tbody></table></div></details>}
    </>}
   </Section>}
  </>}
  {tab==='runner'&&<>
   <Section title="CrawlESG 运行控制（外部进程）">
    <Notice>Console 只启动 CrawlESG 官方 CLI，进程在 <span className="mono">{runner.data?.capabilities?.work_dir || '.local/crawl-work'}</span> 运行；不导入 Crawl 代码、不写索引、不共享数据库。plan/verify 只读，download 会写 packages 与下载台账，必须显式勾选确认。</Notice>
    <div className="stat-grid crawl-kpis">
     <article><small>Python 环境</small><strong>{runner.data?.capabilities?.python_available?'可用':'未安装'}</strong></article>
     <article><small>CLI</small><strong>{runner.data?.capabilities?.cli_available?'可执行':'缺失'}</strong></article>
     <article><small>索引</small><strong>{runner.data?.capabilities?.index_available?'可读':'缺失'}</strong></article>
     <article><small>当前任务</small><strong>{activeJob?activeJob.command:'无'}</strong></article>
    </div>
    <form className="crawl-settings" onSubmit={event=>{event.preventDefault();void startRunnerJob();}}>
     <div className="form-grid">
      <Field label="命令"><select value={runForm.command} onChange={event=>patchRun('command',event.target.value)}><option value="plan">plan · 干跑统计</option><option value="download">download · 下载包</option><option value="verify">verify · 校验已下文件</option></select></Field>
      <Field label="财年（逗号分隔）"><input value={runForm.years} onChange={event=>patchRun('years',event.target.value)} placeholder="2024"/></Field>
      <Field label="数量上限"><input type="number" min={1} max={2000} value={runForm.limit} onChange={event=>patchRun('limit',event.target.value)}/></Field>
      {runForm.command==='download'&&<Field label="并行下载"><input type="number" min={1} max={8} value={runForm.workers} onChange={event=>patchRun('workers',event.target.value)}/></Field>}
     </div>
     {runForm.command==='download'&&<div className="crawl-toggle-grid">
      <label className="crawl-toggle"><input type="checkbox" checked={Boolean(runForm.no_esg)} onChange={event=>patchRun('no_esg',event.target.checked)}/><span>只下载未命中 ESG 的包（no-esg）</span></label>
      <label className="crawl-toggle crawl-danger"><input type="checkbox" checked={Boolean(runForm.confirm)} onChange={event=>patchRun('confirm',event.target.checked)}/><span>确认下载会写入 packages 与台账</span></label>
     </div>}
     <div className="button-row"><button disabled={runBusy||!runner.data?.capabilities?.python_available||(runForm.command==='download'&&!runForm.confirm)}>{runBusy?'启动中…':`启动 ${runForm.command}`}</button><button type="button" className="secondary" onClick={()=>{runner.retry();if(jobId)jobDetail.retry();}}>刷新状态</button></div>
     {runMessage&&<Notice>{runMessage}</Notice>}
    </form>
   </Section>
   {jobId&&<Section title={`任务 ${jobId}`} action={jobDetail.data?.status==='running'?<button type="button" className="danger" disabled={runBusy} onClick={()=>void stopRunnerJob()}>停止任务</button>:undefined}>
    <ResourceState error={jobDetail.error} loading={jobDetail.loading&&!jobDetail.data}/>
    {jobDetail.data&&<>
     <div className="stat-grid"><article><small>命令</small><strong>{jobDetail.data.command}</strong></article><article><small>状态</small><strong>{jobDetail.data.status}</strong></article><article><small>退出码</small><strong>{jobDetail.data.returncode ?? '—'}</strong></article><article><small>开始</small><strong>{jobDetail.data.started_at?.slice(0,19).replace('T',' ') || '—'}</strong></article></div>
     <pre className="crawl-log">{jobDetail.data.log_tail || '暂无日志输出'}</pre>
    </>}
   </Section>}
   <Section title="最近 CrawlESG 任务" action={<button className="text-button" onClick={()=>runner.retry()}>刷新</button>}>
    <ResourceState error={runner.error} loading={runner.loading&&!runner.data} empty={!runner.data?.jobs?.length}/>
    {!!runner.data?.jobs?.length&&<div className="table-scroll crawl-job-table"><table><thead><tr><th>任务</th><th>命令</th><th>状态</th><th>开始</th><th>操作</th></tr></thead><tbody>{runner.data.jobs.map((job:Row)=><tr key={job.job_id}><td className="mono">{job.job_id}</td><td>{job.command}</td><td><Badge value={job.status}/></td><td>{job.started_at?.slice(0,19).replace('T',' ')}</td><td><button type="button" className="secondary" onClick={()=>{setJobId(job.job_id);jobDetail.retry();}}>日志</button></td></tr>)}</tbody></table></div>}
   </Section>
  </>}
 </>;
}
