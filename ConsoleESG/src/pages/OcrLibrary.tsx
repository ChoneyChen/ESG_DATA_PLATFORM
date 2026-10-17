import {items,navigate} from '../api';
import {useResource} from '../state';
import {Badge,dateLabel,ResourceState,Section} from '../components/ui';
import {RecordManagement} from '../components/RecordManagement';
import {Reports,StructureVersion,irVersions,ocrVersions} from './Reports';
import '../library.css';
import {useLibraryResource} from '../library-resource';

/** Existing OCR links remain valid; the list is now the unified asset center. */
export function OcrLibrary({runId,params=new URLSearchParams()}:{runId?:string,params?:URLSearchParams}){
 return runId?<OcrResult runId={runId}/>:<Reports params={params}/>;
}
function OcrResult({runId}:{runId:string}){
 const resource=useLibraryResource('report');
 const parent=items(resource.data).find(pdf=>ocrVersions(pdf).some(ocr=>ocr.run_id===runId));
 const row=parent?ocrVersions(parent).find(ocr=>ocr.run_id===runId):items(resource.data,'unlinked_ocr').find(ocr=>ocr.run_id===runId);
 const artifacts=useResource('document',`/api/ocr/jobs/${encodeURIComponent(runId)}/artifacts`);
 const files=items(artifacts.data,'files').filter(file=>/\.(md|txt|json|png|jpg|jpeg)$/i.test(file.path));
 const back=parent?`/reports?asset=${encodeURIComponent(parent.asset_id)}`:`/reports?ocr=${encodeURIComponent(runId)}`;
 return <>
  <div className="page-heading"><div><button className="text-button" onClick={()=>navigate(back)}>← 报告资产{parent?` / ${parent.file_name}`:''}</button><h1>OCR 识别结果</h1><p>{row?.document_label || row?.title || parent?.file_name || '报告名称未记录'}</p></div><button className="secondary" onClick={()=>navigate(back)}>返回版本关系</button></div>
  <ResourceState error={resource.error} loading={resource.loading&&!row}/>
  <Section title="来源与版本"><div className="ocr-result-lineage">{parent?<button className="text-button" onClick={()=>navigate(`/reports/${encodeURIComponent(parent.asset_id)}`)}>PDF · {parent.file_name}</button>:<span>原 PDF 未关联</span>}<span aria-hidden="true">→</span><strong>OCR · {runId}</strong>{row&&<Badge value={row.status}/>}</div>{row&&<><p>{row.page_count ?? '页数未记录'}{typeof row.page_count==='number'?' 页':''} · {dateLabel(row.completed_at || row.updated_at || row.created_at)}</p><RecordManagement record={row} kind="ocr" id={runId}/></>}</Section>
  <Section title="识别文件"><ResourceState error={artifacts.error} loading={artifacts.loading&&!artifacts.data} empty={!!artifacts.data&&!files.length}/><div className="ocr-files">{files.map(file=>{const path=String(file.path);const safe=!path.startsWith('/')&&!path.split('/').includes('..');const url=safe?`/document/ocr_output/${encodeURIComponent(runId)}/${path.split('/').map(encodeURIComponent).join('/')}`:undefined;return <article key={path}><span>{path}</span>{url&&<a href={url} target="_blank" rel="noreferrer">打开</a>}</article>;})}</div></Section>
  {row&&irVersions(row).length>0&&<Section title={`这个 OCR 版本的结构证据 · ${irVersions(row).length}`}>{irVersions(row).map(ir=><StructureVersion key={ir.run_id || ir.ir_run_id} row={ir} asset={parent}/>)}</Section>}
 </>;
}
