import {items,reportLabel,type Row} from '../api';
import {reportEvidenceVersions,readableEvidence,type ExtractionInputChoice} from '../extraction-input';
import {pipelineSteps} from '../pipeline';
import {Field,Notice,ResourceState,Section} from './ui';

/**
 * Step 1 of a new extraction task.
 *
 * Picking a report is the only required decision: the pipeline reads the PDF
 * itself (text layer first, visual OCR only where it cannot) and reuses a
 * compatible evidence version when one exists. Choosing a specific version is
 * an override for exceptions, kept in the advanced area rather than asked on
 * every task.
 */
export function ExtractionReportInput({choice,onChange,assets,library,catalog,limited,error,loading}:{choice:ExtractionInputChoice;onChange:(value:ExtractionInputChoice)=>void;assets:Row[];library?:Row;catalog:Row[];limited:boolean;error?:string;loading?:boolean}){
 const asset=assets.find(row=>row.asset_id===choice.assetId),versions=reportEvidenceVersions(asset,library,catalog);
 const parent=items(library).find(row=>row.asset_id===choice.assetId);
 const selected=versions.find(row=>row.run_id===choice.irId);
 const ocrCount=parent?.ocr_count ?? parent?.ocr_versions?.length ?? asset?.ocr_run_count ?? 0;
 function mode(value:ExtractionInputChoice['mode']){onChange({...choice,mode:value,assetId:value==='history'?'':choice.assetId,evidence:value==='history'?'fixed':'auto',irId:value==='history'?choice.irId:'',assetIds:value==='batch'?choice.assetIds:[]});}
 function useVersion(runId:string){onChange({...choice,evidence:runId?'fixed':'auto',irId:runId});}
 const option=(row:Row)=>`修订 ${row.ir_revision ?? '未编号'} · ${row.can_build_full_evidence?'完整证据':'有限证据'} · ${row.run_id}`;
 const automatic=choice.mode!=='history'&&choice.evidence==='auto';
 return <Section title="1 · 选择报告">
  <div className="input-mode-tabs" role="group" aria-label="报告选择方式"><button type="button" className={choice.mode==='single'?'secondary selected':'ghost'} aria-pressed={choice.mode==='single'} onClick={()=>mode('single')}>单份报告</button><button type="button" className={choice.mode==='batch'?'secondary selected':'ghost'} aria-pressed={choice.mode==='batch'} onClick={()=>mode('batch')}>多份报告</button><button type="button" className={choice.mode==='history'?'secondary selected':'ghost'} aria-pressed={choice.mode==='history'} onClick={()=>mode('history')}>已有结构证据</button></div>
  {choice.mode==='single'&&<><Field label="报告" hint="选择报告即可，读取与结构构建由系统自动完成。"><select aria-label="报告" value={choice.assetId} onChange={event=>onChange({...choice,assetId:event.target.value,evidence:'auto',irId:''})}><option value="">请选择报告</option>{choice.assetId&&!asset&&<option value={choice.assetId}>指定报告不可用</option>}{assets.map(row=><option key={row.asset_id} value={row.asset_id}>{reportLabel(row)}</option>)}</select></Field>
   {choice.assetId&&!asset&&!loading&&!error&&<Notice tone="error">指定报告不在当前报告库中，请重新选择报告。</Notice>}
   {asset&&<div className="extraction-report-summary"><strong>{reportLabel(asset)}</strong><p>PDF 原文件 · 已有 {ocrCount} 次页面读取 · {versions.length} 个可复用结构证据</p></div>}
   {asset&&automatic&&<div className="reading-plan"><h3>这份报告会被怎样读取</h3><p>{versions.length?`已有 ${versions.length} 个兼容结构证据，系统将直接复用，不重新读取页面。`:'尚无兼容结构证据，将按下述顺序读取页面并构建结构证据。'}</p><div className="extraction-flow" aria-label="提取执行顺序">{pipelineSteps(true).map((step,index)=><span key={step}>{index?'→ ':''}{step}</span>)}</div><details><summary>例外：改用指定的结构证据版本</summary><p>只有在需要对照旧版本或跳过自动判断时才选择；历史提取仍固定原版本。</p><Field label="这份报告的结构证据版本"><select aria-label="这份报告的结构证据版本" value={choice.irId} onChange={event=>useVersion(event.target.value)}><option value="">不指定，由系统自动判断</option>{versions.map(row=><option key={row.run_id} value={row.run_id} disabled={!readableEvidence(row,limited)}>{option(row)}</option>)}</select></Field>{choice.irId&&!selected&&<Notice tone="error">指定证据不属于当前报告或已经不可读取，请重新选择。</Notice>}</details></div>}
   {asset&&choice.evidence==='fixed'&&<div className="reading-plan"><h3>使用指定结构证据</h3><p>本次跳过页面读取与结构构建，直接从指标提取开始。</p><div className="extraction-flow" aria-label="提取执行顺序">{pipelineSteps(false).map((step,index)=><span key={step}>{index?'→ ':''}{step}</span>)}</div><Field label="这份报告的结构证据版本"><select aria-label="这份报告的结构证据版本" value={choice.irId} onChange={event=>useVersion(event.target.value)}><option value="">请选择结构证据版本</option>{choice.irId&&!selected&&<option value={choice.irId}>指定版本不可用 · {choice.irId}</option>}{versions.map(row=><option key={row.run_id} value={row.run_id} disabled={!readableEvidence(row,limited)}>{option(row)}</option>)}</select></Field>{choice.irId&&!selected&&<Notice tone="error">指定证据不属于当前报告或已经不可读取，请重新选择。</Notice>}<button type="button" className="text-button" onClick={()=>useVersion('')}>改回自动读取</button></div>}
  </>}
  {choice.mode==='batch'&&<><p>选择多份报告，统一自动读取；每份报告独立核查来源、独立预览和创建。</p><div className="batch-select">{assets.map(row=><label key={row.asset_id} className="check"><input type="checkbox" checked={choice.assetIds.includes(row.asset_id)} onChange={event=>onChange({...choice,assetIds:event.target.checked?[...choice.assetIds,row.asset_id]:choice.assetIds.filter(id=>id!==row.asset_id)})}/>{reportLabel(row)}</label>)}</div><p className="muted">已选 {choice.assetIds.length} 份报告</p><div className="extraction-flow" aria-label="提取执行顺序">{pipelineSteps(true).map((step,index)=><span key={step}>{index?'→ ':''}{step}</span>)}</div></>}
  {choice.mode==='history'&&<><Notice>原 PDF 已移除时，可直接使用仍可读取的结构证据；此模式不再选择 PDF。</Notice><Field label="历史结构证据"><select aria-label="历史结构证据" value={choice.irId} onChange={event=>onChange({...choice,irId:event.target.value})}><option value="">请选择报告的历史结构证据</option>{choice.irId&&!catalog.some(row=>row.run_id===choice.irId)&&<option value={choice.irId}>指定版本不可用 · {choice.irId}</option>}{catalog.map(row=><option key={row.run_id} value={row.run_id} disabled={!readableEvidence(row,limited)}>{row.document_label || '报告名称未记录'} · {option(row)}</option>)}</select></Field><div className="extraction-flow" aria-label="提取执行顺序">{pipelineSteps(false).map((step,index)=><span key={step}>{index?'→ ':''}{step}</span>)}</div></>}
  <ResourceState error={error} loading={loading}/>
 </Section>;
}
