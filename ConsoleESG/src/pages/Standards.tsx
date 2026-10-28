import {useState} from 'react';
import {credentials,navigate,request,type Row} from '../api';
import {useResource} from '../state';
import {Badge,Diagnostics,Field,Modal,Notice,ResourceState} from '../components/ui';
import {ClauseLifecyclePanel} from '../components/ClauseLifecyclePanel';
import {ClauseEditor} from '../components/ClauseEditor';
import {ExtensionManagement} from '../components/ExtensionManagement';

const lifecycleLabels:Record<string,string>={draft:'草稿',compiled:'未试提取',trialed:'试提取未通过',accepted:'已验收',publishable:'可发布',retired:'已退役'};
const ROLE_LABELS:Record<string,string>={anchor:'锚点',context:'方法/说明',detail:'明细',derived:'派生',presentation:'展示',extension:'延伸'};
const PERIOD_LABELS:Record<string,string>={annual:'年度',fiscal_year:'财年',cumulative:'累计期',quarter:'季度',instant:'期末',target_period:'目标期',baseline_period:'基准期',restated_period:'重述期',multi_year:'多年',unknown:'未识别'};
const ENTITY_LABELS:Record<string,string>={group:'集团合并',consolidated:'合并',parent:'母公司',subsidiary:'子公司',business_unit:'事业部',segment:'分部',site:'基地',plant:'工厂',project:'项目公司',region:'地区',office:'办公场所',unclassified_subset:'未分类子范围',unknown:'未识别'};
const AGGREGATION_LABELS:Record<string,string>={total:'总量',subtotal:'小计',member:'成员',single_line:'单行',intensity:'强度',share:'占比',capacity:'容量',change:'变化量',unknown:'未识别'};
type Resource = {data?:Row;error?:string;loading:boolean;retry:()=>void};
const relationLabels:Record<string,string>={decomposes:'总量与组成',uses_context:'使用背景',depends_on:'必要依赖',conditional_activation:'按条件激活',derived_from:'派生关系',presentation_of:'展示关系',alternative_to:'替代关系'};

/** Four ways to read the same catalogue; every view shows only the current version. */
const VIEWS:[string,string][]=[['concept','业务概念'],['level','标准层级'],['maturity','成熟度'],['dependency','依赖关系']];
const CONCEPTS:[string,string][]=[['E1','气候变化'],['E2','污染'],['E3','水资源与海洋'],['E4','生物多样性与生态系统'],['E5','资源利用与循环经济'],
 ['S1','自有劳动力'],['S2','价值链中的工人'],['S3','受影响社区'],['S4','消费者与终端用户'],['G1','商业行为'],['GOV','治理'],
 ['BP','基础包'],['IRO','影响、风险与机会'],['MDR','最低披露要求'],['SBM','战略与商业模式']];
function conceptOf(requirement:string){const code=(requirement.match(/^[ES]\d/)?.[0]) || (requirement.match(/^(G1|GOV|BP|IRO|MDR|SBM)/)?.[0]) || 'OTHER';return CONCEPTS.find(([key])=>key===code)?.[1] || '其他';}
function levelOf(requirement:string){if(/^E\d/.test(requirement))return '环境';if(/^S\d/.test(requirement))return '社会';if(/^(G1|GOV)/.test(requirement))return '治理';return '通用与交叉';}
function groupPackages(packages:Row[],view:string,lifecycle:Map<string,Row>){
 const groups=new Map<string,Row[]>();
 for(const item of packages){
  let key:string;
  if(view==='concept')key=conceptOf(item.disclosure_requirement);
  else if(view==='level')key=levelOf(item.disclosure_requirement);
  else if(view==='maturity'){const row=lifecycle.get(item.package_id);const accepted=Number(row?.accepted_count||0),publishable=Number(row?.publishable_count||0);key=publishable?'可发布':accepted?'已验收 · 待业务批准':'仅编译 · 未验收';}
  else key=Number(item.relation_count||0)>0?'有依赖关系':'无依赖关系';
  const bucket=groups.get(key) || [];bucket.push(item);groups.set(key,bucket);
 }
 const order=(view==='concept'?CONCEPTS.map(([,name])=>name):view==='level'?['环境','社会','治理','通用与交叉']:view==='maturity'?['可发布','已验收 · 待业务批准','仅编译 · 未验收']:['有依赖关系','无依赖关系']);
 return [...groups.entries()].sort((a,b)=>{const ia=order.indexOf(a[0]),ib=order.indexOf(b[0]);return (ia<0?99:ia)-(ib<0?99:ib) || a[0].localeCompare(b[0],'zh');});
}
const packagePath=(packageId:string)=>`/standards/${encodeURIComponent(packageId)}`;
const clausePath=(packageId:string,sourceId:string)=>`${packagePath(packageId)}/${encodeURIComponent(sourceId)}`;

/** 标准中心：目录 → 包 → 条款，逐级下钻；每一级只做一件事。 */
export function Standards({packageId,clauseId}:{packageId?:string;clauseId?:string}){
 const catalog=useResource('document','/api/pipeline/standards/catalog');
 const lifecycleView=useResource('document','/api/pipeline/clause-lifecycle');
 if(!packageId) return <StandardCenter catalog={catalog} lifecycle={lifecycleView}/>;
 return <PackagePage catalog={catalog} packageId={packageId} clauseId={clauseId}/>;
}

function StandardCenter({catalog,lifecycle}:{catalog:Resource;lifecycle:Resource}){
 const packages:Row[]=catalog.data?.packages || [];
 const summaries:Row[]=lifecycle.data?.packages || [];
 const byPackage=new Map<string,Row>(summaries.map(row=>[String(row.package_id),row]));
 const [view,setView]=useState('concept'),[query,setQuery]=useState('');
 const filtered=packages.filter(p=>`${p.disclosure_requirement} ${p.title || ''} ${(p.metric_summaries || []).map((m:Row)=>m.source_datapoint_id).join(' ')}`.toLowerCase().includes(query.toLowerCase()));
 const groups=groupPackages(filtered,view,byPackage);
 return <>
  <div className="page-heading"><div><span className="eyebrow">标准体系</span><h1>标准中心</h1></div><div className="count">{packages.length} 包</div></div>
  <div className="standard-toolbar">
   <div className="chips" role="group" aria-label="标准目录视图">{VIEWS.map(([key,title])=><button type="button" key={key} className={view===key?'secondary selected':'ghost'} aria-pressed={view===key} onClick={()=>setView(key)}>{title}</button>)}</div>
   <input className="standard-search" aria-label="查找标准包" placeholder="编号、名称或原编号" value={query} onChange={event=>setQuery(event.target.value)}/>
  </div>
  <ResourceState error={catalog.error} loading={catalog.loading&&!packages.length} empty={!packages.length&&!catalog.error}/>
  {groups.map(([group,rows])=><section className="standard-group" key={group}>
   <h2 className="standard-group-title">{group}<small>{rows.length}</small></h2>
   <div className="standard-rows">{rows.map(p=><button className="standard-row" key={p.package_id} onClick={()=>navigate(packagePath(p.package_id))}>
    <span className="standard-code">{p.disclosure_requirement}</span>
    <span className="standard-name">{p.title || '名称待补'}</span>
    <span className="standard-meta">{p.metric_count} 条 · v{p.package_version}</span>
    <span className="standard-arrow" aria-hidden="true">›</span>
   </button>)}</div>
  </section>)}
  <details className="standard-extensions"><summary>项目延伸条款</summary><ExtensionManagement/></details>
 </>;
}

function PackagePage({catalog,packageId,clauseId}:{catalog:Resource;packageId:string;clauseId?:string}){
 const packages:Row[]=catalog.data?.packages || [];
 const pkg=packages.find(item=>item.package_id===packageId);
 const detail=useResource('document',pkg?`/api/pipeline/standards/${encodeURIComponent(pkg.package_id)}/${encodeURIComponent(pkg.package_version)}`:null);
 const metrics:Row[]=detail.data?.metrics || [];
 const lifecycle:Row[]=detail.data?.lifecycle || [];
 const bySource=new Map<string,Row>(lifecycle.map(row=>[String(row.clause).split(':').pop() as string,row]));
 const accepted=lifecycle.filter(row=>row.state==='accepted'||row.state==='publishable').length;
 const [dialog,setDialog]=useState<{source?:string}|undefined>(undefined),[draft,setDraft]=useState<string|undefined>(undefined);
 if(!pkg) return <><BackLink/><ResourceState loading={catalog.loading} error={catalog.error} empty={!catalog.loading&&!catalog.error}/></>;
 if(clauseId) return <ClausePage pkg={pkg} detail={detail} clauseId={clauseId} onDraft={source=>setDialog({source})} draft={draft} setDraft={setDraft} dialog={dialog} setDialog={setDialog}/>;
 return <>
  <div className="page-heading compact">
   <div>
    <button className="text-button back" onClick={()=>navigate('/standards')}>← 标准中心</button>
    <h1>{pkg.title || '名称待补'}<span className="heading-code">{pkg.disclosure_requirement}</span></h1>
    <p className="muted">v{pkg.package_version} · {metrics.length} 条条款 · 已验收 {accepted}</p>
   </div>
   <button disabled={!metrics.length} onClick={()=>setDialog({})}>新增条款</button>
  </div>
  <ResourceState error={detail.error} loading={detail.loading&&!metrics.length}/>
  <div className="clause-list">{metrics.map(m=>{
   const state=bySource.get(m.source_datapoint_id);
   return <button className="clause-row" key={m.metric_id} onClick={()=>navigate(clausePath(pkg.package_id,m.source_datapoint_id))}>
    <span className="clause-code">{m.source_datapoint_id}</span>
    <span className="clause-name">{(m.labels as Row)?.zh || (m.labels as Row)?.en || m.metric_id}</span>
    <span className="clause-role">{ROLE_LABELS[m.disclosure_role] || m.disclosure_role || '—'}</span>
    <Badge value={lifecycleLabels[state?.state] || '未试提取'}/>
    <span className="standard-arrow" aria-hidden="true">›</span>
   </button>;})}</div>
  {!detail.loading&&!metrics.length&&<Notice>该包没有可执行条款。</Notice>}
  {dialog&&<NewClauseDialog pkg={pkg} metrics={metrics} initialSource={dialog.source} onClose={()=>setDialog(undefined)} onCreated={id=>{setDialog(undefined);setDraft(id);}}/>}
  {draft&&<Modal title="编辑条款" onClose={()=>setDraft(undefined)}><ClauseEditor packageId={pkg.package_id} version={pkg.package_version} profiles={[]} initialDraftId={draft} onClose={()=>setDraft(undefined)}/></Modal>}
 </>;
}

function ClausePage({pkg,detail,clauseId,onDraft,draft,setDraft,dialog,setDialog}:{
 pkg:Row;detail:Resource;clauseId:string;onDraft:(source:string)=>void;
 draft?:string;setDraft:(value:string|undefined)=>void;dialog?:{source?:string};setDialog:(value:{source?:string}|undefined)=>void}){
 const metrics:Row[]=detail.data?.metrics || [];
 const metric=metrics.find(item=>item.source_datapoint_id===clauseId);
 const profiles:Row[]=detail.data?.profiles || [];
 const profile=profiles.find(item=>item.source_datapoint_id===clauseId);
 const lifecycle:Row[]=detail.data?.lifecycle || [];
 const state=lifecycle.find(row=>String(row.clause).endsWith(`:${clauseId}`));
 const relations:Row[]=detail.data?.relations || [];
 const name=(metric?.labels as Row)?.zh || (metric?.labels as Row)?.en || clauseId;
 if(!metric) return <><BackLink packageId={pkg.package_id} title={pkg.title}/><ResourceState error={detail.error} loading={detail.loading} empty={!detail.loading&&!detail.error}/>{!detail.loading&&<Notice>该包没有这条条款。</Notice>}</>;
 const contract=(metric.measurement_contract as Row) || {};
 const scopes=(profile?.output_scopes as Row[]) || [];
 const terms=(profile?.retrieval?.positive_terms as string[]) || [];
 const evidence=(profile?.evidence as Row[]) || [];
 const related=relations.filter(r=>[...(r.source_metric_ids||[]),...(r.target_metric_ids||r.member_metric_ids||[])].includes(metric.metric_id));
 return <>
  <div className="page-heading compact">
   <div>
    <button className="text-button back" onClick={()=>navigate(packagePath(pkg.package_id))}>← {pkg.title || pkg.disclosure_requirement}</button>
    <h1>{name}<span className="heading-code">{clauseId}</span></h1>
    <p className="muted">{ROLE_LABELS[metric.disclosure_role] || metric.disclosure_role} · {metric.obligation_class || '—'} · v{pkg.package_version}</p>
   </div>
   <div className="button-row"><Badge value={lifecycleLabels[state?.state] || '未试提取'}/><button onClick={()=>onDraft(clauseId)}>编辑条款</button></div>
  </div>
  <ResourceState error={detail.error} loading={detail.loading&&!metric}/>
  <section className="clause-summary">
   <p className="clause-definition">{contract.positive_definition || metric.description || name}</p>
   {!!(contract.interpretation_guidance as string[])?.length&&<div className="clause-block"><h2>阅读说明</h2><ul>{(contract.interpretation_guidance as string[]).map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
   {!!(contract.exclusions as string[])?.length&&<div className="clause-block"><h2>边界</h2><ul>{(contract.exclusions as string[]).map((item,index)=><li key={index}>{item}</li>)}</ul></div>}
   {!!(contract.distinguish_from as string[])?.length&&<div className="clause-block"><h2>易混概念</h2><div className="chips">{(contract.distinguish_from as string[]).map(item=><span className="clause-chip" key={item}>{item}</span>)}</div></div>}
  </section>
  <section className="clause-section"><h2>输出口径<small>{scopes.length}</small></h2>
   {scopes.length?<div className="table-scroll"><table><thead><tr><th>口径</th><th>期间</th><th>主体</th><th>聚合</th><th>固定轴</th></tr></thead><tbody>{scopes.map(scope=><tr key={String(scope.scope_id)}>
    <td>{scope.label_zh}{scope.required?' · 必需':''}</td><td>{PERIOD_LABELS[String(scope.period)] || scope.period}</td><td>{ENTITY_LABELS[String(scope.entity)] || scope.entity}</td><td>{AGGREGATION_LABELS[String(scope.aggregation)] || scope.aggregation}</td>
    <td>{Object.entries((scope.axes as Row) || {}).map(([key,value])=>`${key}=${String(value)}`).join('、') || '—'}</td>
   </tr>)}</tbody></table></div>:<p className="muted">该条款未生成输出口径。</p>}
  </section>
  <section className="clause-section"><h2>检索与证据<small>{evidence.length}</small></h2>
   {!!terms.length&&<div className="chips">{terms.map(term=><span className="clause-chip" key={term}>{term}</span>)}</div>}
   {!!evidence.length&&<ul className="evidence-list">{evidence.map((row,index)=><li key={index}>{row.field} · {row.level}{row.required?' · 必需':''}</li>)}</ul>}
  </section>
  <section className="clause-section"><h2>验收与状态</h2>
   {state?<ClauseLifecyclePanel packageId={pkg.package_id} version={pkg.package_version} rows={[state]} refresh={()=>detail.retry()}/>:<p className="muted">尚无真实验收记录。</p>}
  </section>
  {!!related.length&&<section className="clause-section"><h2>关系<small>{related.length}</small></h2>{related.map(r=><article className="clause-relation" key={r.relation_id}><strong>{relationLabels[r.relation_type] || r.relation_type}</strong><p>{r.description}</p></article>)}</section>}
  <Diagnostics value={{metric,profile,lifecycle:state}} title="完整合同"/>
  {dialog&&<NewClauseDialog pkg={pkg} metrics={metrics} initialSource={dialog.source} onClose={()=>setDialog(undefined)} onCreated={id=>{setDialog(undefined);setDraft(id);}}/>}
  {draft&&<Modal title="编辑条款" onClose={()=>setDraft(undefined)}><ClauseEditor packageId={pkg.package_id} version={pkg.package_version} profiles={profiles} initialDraftId={draft} onClose={()=>setDraft(undefined)}/></Modal>}
 </>;
}

function BackLink({packageId,title}:{packageId?:string;title?:string}){
 if(!packageId) return <div className="page-heading compact"><div><button className="text-button back" onClick={()=>navigate('/standards')}>← 标准中心</button><h1>标准包</h1></div></div>;
 return <div className="page-heading compact"><div><button className="text-button back" onClick={()=>navigate(packagePath(packageId))}>← {title || '标准包'}</button><h1>条款</h1></div></div>;
}

function NewClauseDialog({pkg,metrics,initialSource,onClose,onCreated}:{
 pkg:Row;metrics:Row[];initialSource?:string;onClose:()=>void;onCreated:(draftId:string)=>void}){
 const [source,setSource]=useState(initialSource || String(metrics[0]?.source_datapoint_id || ''));
 const [note,setNote]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 async function create(){
  if(!source)return setMessage('请选择条款。');
  if(!note.trim())return setMessage('请填写修改说明。');
  setBusy(true);setMessage('');
  try{
   const created=await request<Row>('document','/api/pipeline/clause-drafts',{method:'POST',body:JSON.stringify({
    package_id:pkg.package_id,package_version:pkg.package_version,source_datapoint_id:source,
    author:credentials.operator || 'local-operator',note})});
   if(created?.draft_id)onCreated(String(created.draft_id));else setMessage('未返回草稿编号。');
  }catch(error){setMessage((error as Error).message);}finally{setBusy(false);}
 }
 return <Modal title="新增条款草稿" onClose={onClose}>
  <p className="muted">草稿基于包内条款；保存并校验后可提升为新包版本，不影响当前标准。</p>
  <Field label="条款"><select value={source} onChange={event=>setSource(event.target.value)}>{metrics.map(m=><option key={m.metric_id} value={m.source_datapoint_id}>{m.source_datapoint_id} · {(m.labels as Row)?.zh || m.metric_id}</option>)}</select></Field>
  <Field label="修改说明"><textarea rows={3} value={note} onChange={event=>setNote(event.target.value)} placeholder="例如：补充排除条件"/></Field>
  {message&&<Notice>{message}</Notice>}
  <div className="button-row"><button disabled={busy} onClick={()=>void create()}>{busy?'创建中…':'创建草稿'}</button><button className="secondary" onClick={onClose}>取消</button></div>
 </Modal>;
}
