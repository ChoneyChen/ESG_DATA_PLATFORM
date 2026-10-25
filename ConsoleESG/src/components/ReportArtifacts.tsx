import {useMemo,useState} from 'react';
import {credentials,items,post,type Row} from '../api';
import {useResource,useWorkspace} from '../state';
import {Badge,dateLabel,MetricValue,Notice,ResourceState,Section} from './ui';

const blockerText:Record<string,string>={
  published_observation:'已产生发布观测，不能在这里删除；请走撤回或全库产物清理',
  measurement_context_present:'已保存测量口径，不能删除不可变口径；请走全库产物清理',
  project_extension_candidate:'项目延伸候选带有计算血缘，不能在这里删除',
  superseded_by_another_disclosure:'被同一报告的后续披露引用，不能单独删除',
};

/** Locate and delete only the candidate layer that has not reached review/publication. */
export function ReportArtifacts({reportRef}:{reportRef:string}){
 const {refresh}=useWorkspace();
 const resource=useResource('publication',reportRef?`/internal/v1/report-artifacts?report_ref=${encodeURIComponent(reportRef)}`:null,true);
 const [selected,setSelected]=useState<Set<string>>(new Set());
 const [confirm,setConfirm]=useState<null|{ids:string[]}>(null);
 const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const candidates=items(resource.data,'candidates');
 const deletable=useMemo(()=>candidates.filter(candidate=>candidate.deletable),[candidates]);
 const blocked=candidates.filter(candidate=>!candidate.deletable);
 const batches=items(resource.data,'extraction_batches');
 const report=resource.data?.report || {};
 const canDelete=credentials.roles.includes('admin');
 const targetIds=confirm?.ids?.length?confirm.ids:deletable.map(candidate=>candidate.mapping_id);
 async function remove(ids:string[]){
  setBusy(true);setMessage('');
  try{
   const result=await post('publication','/internal/v1/report-artifacts/delete',{report_ref:reportRef,apply:true,
     reason:'删除未发布且未保存测量口径的候选',...(ids.length?{mapping_ids:ids}:{})});
   setMessage(`已删除 ${result.deleted ?? 0} 条候选；${(result.blocked_candidates || []).length} 条因已发布或已保存口径保留。`);
   setSelected(new Set());setConfirm(null);refresh();
  }catch(error){setMessage((error as Error).message);}
  finally{setBusy(false);}
 }
 return <Section title="报告产物与删除">
  <Notice>这里只删除尚未发布、尚未保存测量口径的候选行。报告身份、PDF 来源、导入批次审计、已发布观测、不可变来源快照都保留；提取成果目录由提取服务负责删除。彻底物理清理走发布中心的全库产物清理。</Notice>
  <ResourceState error={resource.error} loading={resource.loading&&!resource.data} empty={!reportRef}/>
  {report.report_ref&&<>
   <div className="artifact-summary">
    <span><strong>{candidates.length}</strong>候选</span>
    <span><strong>{deletable.length}</strong>可删除</span>
    <span className={blocked.length?'warning':''}><strong>{blocked.length}</strong>受保护</span>
    <span><strong>{batches.length}</strong>导入批次</span>
   </div>
   <details className="artifact-locations"><summary>产物定位（报告、PDF、导入批次、IR/OCR）</summary>
    <dl className="facts">
     <dt>报告引用</dt><dd className="mono">{report.report_ref}</dd>
     <dt>报告版本</dt><dd className="mono">{report.report_version_ref || '未记录'}</dd>
     <dt>PDF 资产</dt><dd className="mono">{report.source_artifact_ref || '未记录'}</dd>
     <dt>来源 SHA-256</dt><dd className="mono">{report.source_sha256 || '未记录'}</dd>
    </dl>
    <div className="artifact-batches">{batches.map(batch=><article key={batch.import_batch_id}>
     <strong className="mono">{batch.bundle_job_id || batch.import_batch_id}</strong>
     <small className="mono" title={batch.bundle_ref}>{batch.bundle_ref}</small>
     <small>{batch.package_id}@{batch.package_version} · {batch.item_count} 条导入 · {dateLabel(batch.imported_at)}</small>
    </article>)}</div>
    <p className="muted">提取成果目录与这些批次一一对应，但归提取服务所有。删除提取任务记录时会按发布引用检查是否可删除成果目录；平台候选需要在这里单独删除。</p>
   </details>
   <div className="button-row">
    <button type="button" className="danger" disabled={busy||!canDelete||!deletable.length} title={!canDelete?'当前身份没有管理权限':undefined}
      onClick={()=>setConfirm({ids:selected.size?[...selected]:[]})}>{busy?'删除中…':selected.size?`删除所选 ${selected.size} 条`:`删除全部可删除候选（${deletable.length}）`}</button>
    <small className="muted">每次最多删除当前报告的可删除候选；受保护行会列出原因。</small>
   </div>
   {confirm&&<div className="confirm-box"><strong>确认删除 {targetIds.length} 条候选？</strong>
    <p>删除后该报告的这些候选、映射、测量证据与审核决定不再可见，无法在这里恢复。报告身份与已发布观测不受影响。</p>
    <div className="button-row"><button type="button" className="danger" disabled={busy} onClick={()=>void remove(targetIds)}>确认删除</button><button type="button" className="ghost" disabled={busy} onClick={()=>setConfirm(null)}>取消</button></div>
   </div>}
   {message&&<Notice>{message}</Notice>}
   <div className="table-scroll artifact-table-wrap"><table><thead><tr><th>选择</th><th>原编号 / 指标 / 产物位置</th><th>值</th><th>状态</th><th>删除</th></tr></thead><tbody>
    {candidates.map(candidate=><tr key={candidate.mapping_id}>
     <td>{candidate.deletable?<input type="checkbox" aria-label={`选择 ${candidate.source_datapoint_id || candidate.mapping_id}`} checked={selected.has(candidate.mapping_id)} onChange={event=>{const next=new Set(selected);event.target.checked?next.add(candidate.mapping_id):next.delete(candidate.mapping_id);setSelected(next);}}/>:<span className="muted">—</span>}</td>
     <td><strong>{candidate.source_datapoint_id || candidate.clause_kind}</strong><small className="table-secondary">{candidate.raw_indicator_name || '未命名披露'}</small><small className="table-secondary mono artifact-id" title={`${candidate.mapping_id} / ${candidate.disclosure_id}`}>mapping {candidate.mapping_id} · disclosure {candidate.disclosure_id}</small><small className="table-secondary artifact-id" title={candidate.document_id || undefined}>IR {candidate.source_ir_run_id || '未记录'}{candidate.page_number?` · 第 ${candidate.page_number} 页`:''}{candidate.document_id?` · ${candidate.document_id}`:''}</small></td>
     <td><MetricValue value={candidate.raw_value_text}/></td>
     <td><Badge value={candidate.fact_review_status}/><Badge value={candidate.mapping_review_status}/>{candidate.publication_status&&<Badge value={candidate.publication_status}/>}</td>
     <td>{candidate.deletable?<button type="button" className="secondary" disabled={busy||!canDelete} onClick={()=>setConfirm({ids:[candidate.mapping_id]})}>删除</button>:<small className="muted">{blockerText[candidate.deletion_blocker] || '受保护'}</small>}</td>
    </tr>)}
   </tbody></table></div>
  </>}
 </Section>;
}
