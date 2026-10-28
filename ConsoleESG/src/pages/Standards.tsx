import {ExtensionManagement} from "../components/ExtensionManagement";
import {ClauseLifecyclePanel} from "../components/ClauseLifecyclePanel";
import {useState} from 'react';
import {type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Badge,Diagnostics,Field,Notice,ResourceState,Section} from '../components/ui';
const relationLabels:Record<string,string>={decomposes:'总量与组成',uses_context:'使用背景',depends_on:'必要依赖',conditional_activation:'按条件激活',derived_from:'派生关系',presentation_of:'展示关系',alternative_to:'替代关系'};
const lifecycleLabels:Record<string,string>={draft:'草稿',compiled:'未试提取',trialed:'试提取未通过',accepted:'已验收',publishable:'可发布',retired:'已退役'};
export function Standards(){
 const catalog=useResource('document','/api/pipeline/standards/catalog');
 const profile=useResource('document','/api/pipeline/extraction-profiles/platform-major?topics=E1,E2,E3');
 const lifecycleView=useResource('document','/api/pipeline/clause-lifecycle');
 const [selected,setSelected]=useState<Row>(),[query,setQuery]=useState('');
 const detail=useResource('document',selected?`/api/pipeline/standards/${encodeURIComponent(selected.package_id)}/${encodeURIComponent(selected.package_version)}`:null);
 const packages:Row[]=catalog.data?.packages || [],major=new Set<string>(profile.data?.overall_source_datapoint_ids || []);
 const manifest=detail.data?.manifest;
 const identityMismatch=!!manifest&&!!selected&&(manifest.package_id!==selected.package_id||manifest.package_version!==selected.package_version);
 const verifiedManifest=identityMismatch?undefined:manifest;
 const historical=selected?.is_historical===true;
 const coreIds:string[]=verifiedManifest?.core_source_datapoint_ids || (historical?[]:selected?.core_source_datapoint_ids || []);
 const metrics:Row[]=identityMismatch?[]:detail.data?.metrics || (historical?[]:selected?.metric_summaries || []);
 const relations:Row[]=identityMismatch?[]:detail.data?.relations || [];
 const lifecycle:Row[]=identityMismatch?[]:detail.data?.lifecycle || [];
 const lifecycleBySource=new Map<string,Row>(lifecycle.map(row=>[String(row.clause).split(':').pop() as string,row]));
 const versions=[...new Set<string>([
  ...packages.filter(p=>p.package_id===selected?.package_id).map((p:Row)=>String(p.package_version)),
  ...((catalog.data?.historical || []) as Row[]).filter(h=>h.package_id===selected?.package_id).map(h=>String(h.package_version))])];
 const workspace=useWorkspace();
 const lifecycleSummaries:Row[]=lifecycleView.data?.packages || [];
 const publishableTotal=lifecycleSummaries.reduce((sum,row)=>sum+Number(row.publishable_count || 0),0);
 const acceptedTotal=lifecycleSummaries.reduce((sum,row)=>sum+Number(row.accepted_count || 0),0);
 const name=verifiedManifest?.standard?.disclosure_requirement || selected?.disclosure_requirement || selected?.package_id;
 return <>
  <div className="page-heading"><div><span className="eyebrow">标准体系</span><h1>标准中心</h1><p>一披露小号一包，完整保留原条款编号、语义关系、建设与验收状态。</p></div><div className="count">当前 {packages.length} 包 · 主要 {profile.data?.overall_source_datapoint_ids?.length ?? '…'} 项 · 已验收 {acceptedTotal} 条 · 可发布 {publishableTotal} 条</div></div>
  <Notice>结构校验、真实报告验收与业务批准分别记录。已编译草稿不自动等同于提取质量已验收。</Notice>
  <div className="standard-layout"><aside className="panel standard-index">
   <Field label="查找标准包"><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="E1-5 / MDR / 原编号"/></Field>
   <ResourceState error={catalog.error || profile.error} loading={catalog.loading&&!packages.length}/>
   {packages.filter(p=>`${p.disclosure_requirement} ${(p.metric_summaries || []).map((m:Row)=>m.source_datapoint_id).join(' ')}`.toLowerCase().includes(query.toLowerCase())).map(p=><button className={`standard-item ${selected?.package_id===p.package_id&&selected?.package_version===p.package_version?'selected':''}`} key={`${p.package_id}@${p.package_version}`} onClick={()=>setSelected({...p,is_historical:false})}><strong>{p.disclosure_requirement}</strong><small>{p.metric_count} 条 · v{p.package_version}</small><Badge value={p.status}/></button>)}
  </aside><div>{selected?<>
   <Section title={`${name} · v${selected.package_version}`} action={<Badge value={historical?'历史只读':verifiedManifest?.status || selected.status}/>}>
    {historical&&<Notice>正在只读查看此历史固定版本，供既有任务与来源查证。此操作不会启用、升级或改写标准包。“当前平台主要”仅表示当前平台清单，历史包的核心条款以该版本原合同为准。</Notice>}
    <dl className="facts"><dt>固定版本</dt><dd>{selected.package_id} @ {selected.package_version}</dd>
     <dt>包内核心</dt><dd>{coreIds.join('、') || (detail.loading?'正在读取该版本合同':'此版本未记录核心条款')}</dd>
     <dt>当前平台主要</dt><dd>{metrics.filter(m=>major.has(m.source_datapoint_id)).map(m=>m.source_datapoint_id).join('、') || '本包未进入当前主要清单'}</dd>
     <dt>结构</dt><dd>{historical?'历史合同按原版本读取':selected.validation_state==='structurally_valid'?'校验通过':'需检查'}</dd>
     <dt>真实提取验收</dt><dd>{historical?'历史合同不继承当前版本验收':selected.real_extraction_validation==='not_assessed'?'尚未评估':selected.real_extraction_validation || '尚未记录'}</dd>
     <dt>业务批准</dt><dd>{historical?'历史合同不继承当前版本批准':selected.business_approval==='not_assessed'?'尚未评估':selected.business_approval || '尚未记录'}</dd>
    </dl><ResourceState error={detail.error} loading={detail.loading}/>
    {identityMismatch&&<Notice tone="error">详情返回的包身份与所选固定版本不一致，已停止展示条款。请核对服务，不能用当前版本替代历史合同。</Notice>}
   </Section>
   {!identityMismatch&&detail.data?.semantic_diagnostics?.length>0&&<Notice tone="warning">此版本有 {detail.data?.semantic_diagnostics.length} 条语义诊断，按原合同保留。诊断不修改既有任务的标准身份。<Diagnostics value={detail.data?.semantic_diagnostics} title="历史语义诊断"/></Notice>}
   <Section title="条款与条件"><div className="table-scroll"><table><thead><tr><th>原编号 / 名称</th><th>核心</th><th>当前平台主要</th><th>生命周期</th><th>语义角色</th><th>披露条件</th></tr></thead><tbody>{metrics.map(m=><tr key={m.metric_id}>
    <td><strong>{m.source_datapoint_id}</strong><p>{m.labels?.zh || m.labels?.en}</p></td><td>{coreIds.includes(m.source_datapoint_id)?'是':'否'}</td><td>{major.has(m.source_datapoint_id)?'是':'否'}</td>
    <td>{lifecycleBySource.has(m.source_datapoint_id)?<Badge value={lifecycleLabels[lifecycleBySource.get(m.source_datapoint_id)!.state] || '未试提取'}/>:'—'}</td>
    <td>{m.disclosure_role==='anchor'?'锚点条款':m.disclosure_role==='context'?'方法 / 说明':m.disclosure_role==='derived'?'派生条款':m.disclosure_role==='presentation'?'展示条款':'延伸披露'}</td>
    <td>{m.obligation_class}<details><summary>身份与证据合同</summary><dl className="facts"><dt>正向定义</dt><dd>{m.measurement_contract?.positive_definition || m.description || m.labels?.zh || '详见合同'}</dd><dt>阅读说明</dt><dd>{(m.measurement_contract?.interpretation_guidance || []).join('；') || '无补充说明'}</dd><dt>易混概念</dt><dd>{(m.measurement_contract?.distinguish_from || []).join('、') || '详见合同'}</dd><dt>身份轴</dt><dd>{(m.measurement_contract?.identity_axes || []).join('、') || '详见合同'}</dd><dt>排除条件</dt><dd>{(m.measurement_contract?.exclusions || []).join('；') || '详见合同'}</dd></dl><Diagnostics value={m} title="完整合同"/></details></td>
   </tr>)}</tbody></table></div></Section>
   {!identityMismatch&&lifecycle.length>0&&<Section title="条款生命周期与真实验收" action={<Badge value={`${lifecycle.filter(row=>row.state==='publishable').length}/${lifecycle.length} 可发布`}/>}>
    <ClauseLifecyclePanel packageId={selected.package_id} version={selected.package_version} rows={lifecycle} versions={versions} refresh={()=>{workspace.refresh();detail.retry();lifecycleView.retry();}}/>
   </Section>}
   <Section title="条款关系与必要依赖"><RelationshipGraph metrics={metrics} relations={relations}/>{relations.map(r=><article className="relation" key={r.relation_id}><strong>{relationLabels[r.relation_type] || r.relation_type}</strong><p>{r.description}</p><p>{(r.source_metric_ids || []).map((id:string)=>metrics.find(m=>m.metric_id===id)?.source_datapoint_id || id).join('、')} → {(r.target_metric_ids || r.member_metric_ids || []).map((id:string)=>metrics.find(m=>m.metric_id===id)?.source_datapoint_id || id).join('、')}</p><Diagnostics value={r.activation_predicate} title="适用条件"/></article>)}{!relations.length&&<p className="muted">详细合同加载后展示关系；无关系的包不生成额外依赖。</p>}</Section>
   {!identityMismatch&&<Diagnostics value={{manifest:verifiedManifest,compilation:detail.data?.compilation}} title="格式、Core、固定来源版本与摘要"/>}
  </>:<Section title="选择一个标准包"><p className="muted">查看原编号、核心与主要、关系、依赖及建设状态。详情按需加载，单包故障不会隐藏全部目录。</p></Section>}</div></div>
  <ExtensionManagement/>
  <Section title="后续建设与历史引用"><details><summary>占位包（{catalog.data?.planned?.length || 0}）</summary>{catalog.data?.planned?.map((p:Row)=><article className="relation" key={p.package_id}><strong>{p.disclosure_requirement} · 待建设</strong><p>{p.source_datapoint_ids?.join('、')}</p><p>主要条款：{p.major_source_datapoint_ids?.join('、') || '无'}</p></article>)}</details>
   <details><summary>历史版本（{catalog.data?.historical?.length || 0}）</summary><ul>{catalog.data?.historical?.map((h:Row)=><li key={`${h.package_id}@${h.package_version}`}><span>{h.package_id} · {h.package_version} · 保留既有任务引用 </span><button className="text-button" onClick={()=>setSelected({...h,is_historical:true})} aria-label={`只读查看历史包 ${h.package_id} ${h.package_version}`}>只读查看固定版本</button></li>)}</ul></details>
   {profile.data&&<details><summary>完整 {profile.data.overall_source_datapoint_ids.length} 项主要原编号 · {profile.data.pending_source_datapoint_ids.length} 项待建设</summary><div className="chips">{profile.data.overall_source_datapoint_ids.map((id:string)=><span className="clause-chip" key={id}>{id}{profile.data?.pending_source_datapoint_ids?.includes(id)?' · 待建':''}</span>)}</div></details>}
  </Section>
 </>;
}
function RelationshipGraph({metrics,relations}:{metrics:Row[],relations:Row[]}){const links=relations.flatMap(r=>(r.source_metric_ids || []).flatMap((from:string)=>(r.target_metric_ids || r.member_metric_ids || []).map((to:string)=>({from,to,type:r.relation_type}))));if(!links.length)return null;const ids=[...new Set<string>(links.flatMap(l=>[l.from,l.to]))],height=Math.max(140,ids.length*34);const pos=(id:string)=>({x:ids.indexOf(id)%2?500:40,y:40+Math.floor(ids.indexOf(id)/2)*64});return <div className="relation-graph"><svg viewBox={`0 0 760 ${height}`} role="img" aria-label="条款依赖关系图"><defs><marker id="relation-arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="#648581"/></marker></defs>{links.map((l,i)=>{const a=pos(l.from),b=pos(l.to);return <path key={i} d={`M${a.x+130} ${a.y+15}C380 ${a.y+15},380 ${b.y+15},${b.x} ${b.y+15}`} fill="none" stroke="#98b7b0" strokeWidth="1.4" markerEnd="url(#relation-arrow)"><title>{relationLabels[l.type] || l.type}</title></path>;})}{ids.map(id=>{const p=pos(id);return <g key={id} transform={`translate(${p.x} ${p.y})`}><rect width="155" height="32" rx="7" fill="#eff6f3" stroke="#bad2ca"/><text x="12" y="21" fontSize="13" fill="#245146">{metrics.find(m=>m.metric_id===id)?.source_datapoint_id || id.split('.').pop()}</text></g>;})}</svg></div>;}
