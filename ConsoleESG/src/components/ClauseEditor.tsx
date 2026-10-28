import {useEffect,useState} from 'react';
import {credentials,request,type Row} from '../api';
import {useResource} from '../state';
import {Badge,Field,Notice,ResourceState,Section} from './ui';

const PERIODS:Record<string,string>={annual:'年度',fiscal_year:'财年',cumulative:'累计期',quarter:'季度',instant:'期末时点',target_period:'目标期',baseline_period:'基准期',restated_period:'重述期',multi_year:'多年范围',unknown:'未识别'};
const ENTITIES:Record<string,string>={group:'集团合并',consolidated:'合并口径',parent:'母公司',subsidiary:'子公司',business_unit:'事业部/板块',segment:'业务分部',site:'基地/工厂',plant:'生产厂',project:'项目公司',region:'地区',office:'办公场所',unclassified_subset:'未分类子范围',unknown:'未识别'};
const AGGREGATIONS:Record<string,string>={total:'总量',subtotal:'小计',member:'成员',single_line:'单行',intensity:'强度',share:'占比',capacity:'容量',change:'变化量',unknown:'未识别'};
const ROLES:Record<string,string>={actual:'实际值',target:'目标值',baseline:'基准值',forecast:'预测值',restated:'重述值',estimate:'估计值',context:'背景陈述',structure:'结构/目录',unknown:'未识别'};

const toLines = (text: string) => text.split('\n').map(item => item.trim()).filter(Boolean);

export function ClauseEditor({packageId,version,profiles,onClose,initialDraftId}:{packageId:string;version:string;profiles:Row[];onClose:()=>void;initialDraftId?:string}){
 const draftsResource=useResource('document','/api/pipeline/clause-drafts');
 const [draftId,setDraftId]=useState(''),[parts,setParts]=useState<Row|null>(null),[meta,setMeta]=useState<Row>({}),
  [author,setAuthor]=useState(credentials.operator || 'local-operator'),[note,setNote]=useState(''),[source,setSource]=useState(''),
  [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[validation,setValidation]=useState<Row|null>(null),
  [promotion,setPromotion]=useState<Row|null>(null),[promoted,setPromoted]=useState<Row|null>(null);
 const drafts=((draftsResource.data?.drafts as Row[]) || []).filter(row=>row.base?.package_id===packageId&&row.base?.package_version===version);

 async function run(action:()=>Promise<unknown>,success:string){setBusy(true);setMessage('');try{const result=await action() as Row;if(result?.validation)setValidation(result.validation);setMessage(success);draftsResource.retry();return result;}catch(error){setMessage((error as Error).message);return null;}finally{setBusy(false);}}

 async function create(){
  if(!source)return setMessage('请选择要编辑的条款。');
  if(!note.trim())return setMessage('请填写修改说明。');
  const created=await run(()=>request('document','/api/pipeline/clause-drafts',{method:'POST',body:JSON.stringify({package_id:packageId,package_version:version,source_datapoint_id:source,author,note})}),'草稿已创建，尚未影响任何任务。');
  if(created?.draft_id)await open(String(created.draft_id));
 }

 async function open(id:string){
  setDraftId(id);setParts(null);
  await run(async()=>{const body=await request<Row>('document',`/api/pipeline/clause-drafts/${encodeURIComponent(id)}`);setParts(body);setMeta(body.manifest||{});setValidation((body.manifest?.validation as Row)||null);return body;},`已打开 ${id}。`);
 }

 useEffect(()=>{if(initialDraftId)void open(initialDraftId);},[initialDraftId]);

 function edit(field:string,value:unknown){setParts(prev=>prev?{...prev,[field]:value}:prev);}
 function editPart(part:string,key:string,value:unknown){setParts(prev=>prev?{...prev,[part]:{...(prev[part] as Row),[key]:value}}:prev);}
 function editScope(index:number,key:string,value:unknown){
  const scopes=[...(parts?.clause?.output_scopes as Row[])||[]];
  scopes[index]={...scopes[index],[key]:value};
  editPart('clause','output_scopes',scopes);
 }

 async function save(){
  if(!parts)return;
  const edits={positive_definition:parts.clause.positive_definition,distinguish_from:parts.clause.distinguish_from,
   exclusions:parts.clause.exclusions,output_scopes:parts.clause.output_scopes,publication:parts.clause.publication,
   retrieval:{positive_terms:parts.retrieval.positive_terms,negative_terms:parts.retrieval.negative_terms,structural_anchors:parts.retrieval.structural_anchors},
   evidence:parts.evidence.requirements,acceptance:{minimum_reports:parts.acceptance.minimum_reports,samples:parts.acceptance.samples}};
  await run(()=>request('document',`/api/pipeline/clause-drafts/${encodeURIComponent(draftId)}`,{method:'PUT',
   body:JSON.stringify({edits,author,note,expected_digest:meta.digest})}),'草稿已保存并重新计算合同摘要。');
 }

 async function validateNow(){await run(()=>request('document',`/api/pipeline/clause-drafts/${encodeURIComponent(draftId)}/validate`,{method:'POST'}),'已重新校验。');}
 async function loadPlan(){await run(async()=>{const body=await request<Row>('document',`/api/pipeline/clause-drafts/${encodeURIComponent(draftId)}/promotion-plan`);setPromotion(body);return body;},'已生成提升预览。');}
 async function promote(){await run(async()=>{const body=await request<Row>('document',`/api/pipeline/clause-drafts/${encodeURIComponent(draftId)}/promote`,{method:'POST',
   body:JSON.stringify({version:null,register_release:true,activate:false,reason:note||'由条款编辑器提升为试点版本'})});setPromoted(body);return body;},'已编译并登记新包版本，可用于试点提取；平台当前标准未切换。');}
 async function discard(){if(!draftId)return;await run(()=>request('document',`/api/pipeline/clause-drafts/${encodeURIComponent(draftId)}`,{method:'DELETE'}),'草稿已丢弃。');setParts(null);setDraftId('');}

 const scopes=(parts?.clause?.output_scopes as Row[]) || [];
 const evidence=(parts?.evidence?.requirements as Row[]) || [];
 const samples=(parts?.acceptance?.samples as Row[]) || [];
 return <Section title="条款编辑与草稿" action={<button className="secondary" onClick={onClose}>收起</button>}>
  <Notice>编辑只写草稿，不改动任何不可变包与已发布含义。保存会重新计算合同摘要；草稿不会自动进入提取或发布。</Notice>
  <div className="form-grid">
   <Field label="选择条款"><select value={source} onChange={event=>setSource(event.target.value)}><option value="">请选择</option>{profiles.map(profile=><option key={profile.source_datapoint_id} value={profile.source_datapoint_id}>{profile.source_datapoint_id} · {profile.labels?.zh}</option>)}</select></Field>
   <Field label="编辑人"><input value={author} onChange={event=>setAuthor(event.target.value)}/></Field>
   <Field label="修改说明"><input value={note} onChange={event=>setNote(event.target.value)} placeholder="为什么改，改了什么"/></Field>
   <div className="button-row"><button disabled={busy||!source||!note.trim()} onClick={()=>void create()}>新建草稿</button></div>
  </div>
  {message&&<Notice tone={/失败|未|不能|需要|请/.test(message)&&!/已/.test(message)?'warning':'info'}>{message}</Notice>}
  <ResourceState error={draftsResource.error} loading={draftsResource.loading&&!draftsResource.data}/>
  {drafts.length>0&&<details><summary>本包的草稿 · {drafts.length}</summary><ul>{drafts.map(draft=><li key={draft.draft_id}>
   <button className="text-button" onClick={()=>void open(draft.draft_id)}>{draft.draft_id}</button>
   {' '}<Badge value={draft.status}/>{' '}
   <small>{draft.base?.source_datapoint_id} · {draft.note} · {String(draft.updated_at || draft.created_at).slice(0,19)}</small>
  </li>)}</ul></details>}
  {parts&&<>
   <h3>{parts.clause.clause_id}</h3>
   {validation&&<Notice tone={validation.ok?'info':'warning'}>校验：{validation.ok?'通过':'未通过'}{((validation.errors as Row[])||[]).map((error:Row)=>`；${error.field}：${error.message}`).join('')}{((validation.warnings as Row[])||[]).map((warning:Row)=>`；提示 ${warning.field}：${warning.message}`).join('')}</Notice>}
   <Field label="正向定义"><textarea value={String(parts.clause.positive_definition || '')} onChange={event=>editPart('clause','positive_definition',event.target.value)}/></Field>
   <div className="form-grid">
    <Field label="易混概念（每行一个）"><textarea value={(parts.clause.distinguish_from as string[] || []).join('\n')} onChange={event=>editPart('clause','distinguish_from',toLines(event.target.value))}/></Field>
    <Field label="排除条件（每行一个）"><textarea value={(parts.clause.exclusions as string[] || []).join('\n')} onChange={event=>editPart('clause','exclusions',toLines(event.target.value))}/></Field>
   </div>
   <h4>输出口径</h4>
   <div className="table-scroll"><table><thead><tr><th>期间</th><th>主体</th><th>值角色</th><th>聚合</th><th>方法</th><th>必需</th><th/></tr></thead><tbody>{scopes.map((scope,index)=><tr key={scope.scope_id || index}>
    <td><select value={scope.period} onChange={event=>editScope(index,'period',event.target.value)}>{Object.entries(PERIODS).map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></td>
    <td><select value={scope.entity} onChange={event=>editScope(index,'entity',event.target.value)}>{Object.entries(ENTITIES).map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></td>
    <td><select value={scope.value_role} onChange={event=>editScope(index,'value_role',event.target.value)}>{Object.entries(ROLES).map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></td>
    <td><select value={scope.aggregation} onChange={event=>editScope(index,'aggregation',event.target.value)}>{Object.entries(AGGREGATIONS).map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></td>
    <td><input value={scope.method || ''} onChange={event=>editScope(index,'method',event.target.value || null)} placeholder="如 location_based"/></td>
    <td><input type="checkbox" checked={scope.required === true} onChange={event=>editScope(index,'required',event.target.checked)}/></td>
    <td><button className="text-button" onClick={()=>editPart('clause','output_scopes',scopes.filter((_,position)=>position!==index))}>删除</button></td>
   </tr>)}</tbody></table></div>
   <button className="secondary" onClick={()=>editPart('clause','output_scopes',[...scopes,{scope_id:`scope-${Date.now().toString(36)}`,label_zh:'新口径',period:'annual',entity:'group',value_role:'actual',aggregation:'total',axes:{},required:false}])}>添加口径</button>
   <div className="form-grid">
    <Field label="检索正向词（每行一个）"><textarea value={(parts.retrieval.positive_terms as string[] || []).join('\n')} onChange={event=>editPart('retrieval','positive_terms',toLines(event.target.value))}/></Field>
    <Field label="检索排除词（每行一个）"><textarea value={(parts.retrieval.negative_terms as string[] || []).join('\n')} onChange={event=>editPart('retrieval','negative_terms',toLines(event.target.value))}/></Field>
   </div>
   <h4>证据要求</h4>
   <ul>{evidence.map((item,index)=><li key={`${item.field}-${index}`}>{item.field} · {item.level}{item.required?' · 必需':''}<button className="text-button" onClick={()=>editPart('evidence','requirements',evidence.filter((_,position)=>position!==index))}>删除</button></li>)}</ul>
   <button className="secondary" onClick={()=>editPart('evidence','requirements',[...evidence,{field:'reporting_boundary',level:'row',required:false}])}>添加证据要求</button>
   <h4>验收样例</h4>
   <ul>{samples.map((sample,index)=><li key={index}>{sample.run_ref}{sample.expected_absent?' · 期望未披露':` · 期望 ${sample.expected_value_raw}`}<button className="text-button" onClick={()=>editPart('acceptance','samples',samples.filter((_,position)=>position!==index))}>删除</button></li>)}{!samples.length&&<li>尚无样例；没有样例的条款无法通过真实验收。</li>}</ul>
   <button className="secondary" onClick={()=>editPart('acceptance','samples',[...samples,{run_ref:'',report_label:'',expected_value_raw:'',expected_absent:false}])}>添加样例</button>
   <div className="button-row"><button disabled={busy} onClick={()=>void save()}>保存草稿</button><button className="secondary" disabled={busy} onClick={()=>void validateNow()}>重新校验</button><button className="secondary" disabled={busy} onClick={()=>void loadPlan()}>提升预览</button>{promotion&&meta.status==='validated'&&<button disabled={busy} onClick={()=>void promote()}>编译为新包版本</button>}<button className="secondary" disabled={busy} onClick={()=>void discard()}>丢弃草稿</button><span className="muted mono">摘要 {String(meta.digest||'').slice(0,16)}…</span></div>
   {promotion&&<Notice>提升预览：{promotion.package_id} {promotion.current_version} → <strong>{promotion.new_version}</strong>；可进入包的改动：{Object.keys(promotion.changes||{}).join('、') || '无'}。检索词与口径属编译产物，不在包内改写。</Notice>}
   {promoted&&<Notice tone="info">已生成 {promoted.package_id}@{promoted.new_version}（摘要 {String(promoted.source_digest||'').slice(0,12)}…），
    释放路径 {promoted.release_path}；登记：{promoted.registered?'是':'否'}，切换平台当前标准：{promoted.activated?'是':'否'}。
    下一步用该版本跑试点提取，验收通过后再决定是否切换。</Notice>}
  </>}
 </Section>;
}
