import {useCallback,useEffect,useRef,useState} from 'react';
import {ApiError,items,request,type Row} from './api';
import {useWorkspace} from './state';

/** Explicit source identity is the only relationship accepted by the compatibility view. */
export function groupReportLibrary(pdfRows:Row[],ocrRows:Row[]){
 const linked=new Set<string>();
 const rows=pdfRows.map(pdf=>{
  const children=ocrRows.filter(ocr=>{
   if(ocr.source_asset_id)return ocr.source_asset_id===pdf.asset_id;
   const fingerprint=ocr.source_pdf_sha256 || ocr.pdf_sha256 || ocr.document_fingerprint;
   return typeof fingerprint==='string'&&/^[a-f0-9]{64}$/i.test(fingerprint)&&fingerprint===pdf.sha256;
  });
  children.forEach(ocr=>linked.add(ocr.run_id));
  return {...pdf,ocr_versions:children,ocr_count:children.length};
 });
 return {items:rows,unlinked_ocr:ocrRows.filter(ocr=>!linked.has(ocr.run_id)),compatibility_view:true};
}

/** Only absent new routes use a read-only view while an older worker drains. */
export function useLibraryResource(kind:'report'|'pdf'|'ocr'|'status'){
 const {epoch,auth}=useWorkspace();
 const [state,setState]=useState<{data?:Row,error:string,loading:boolean}>({error:'',loading:true});
 const pending=useRef('');
 const load=useCallback(()=>{
  if(pending.current===kind)return;
  pending.current=kind;
  const paths={report:'/api/report-library',pdf:'/api/pdf-library',ocr:'/api/ocr-library',status:'/api/processing-status'};
  const legacyLibrary=async(type:'pdf'|'ocr')=>{
   try{return items(await request('document',paths[type],{}));}
   catch(error){if(!(error instanceof ApiError)||error.status!==404)throw error;}
   const old=await request('document',type==='pdf'?'/api/report-assets':'/api/ocr/jobs',{});
   if(type==='pdf')return items(old,'assets');
   return items(old).map(r=>({...r,document_label:r.summary?.document_label || r.document_label,available_actions:[]}));
  };
  const run=async()=>{try{return await request('document',paths[kind],{});}catch(error){
   if(!(error instanceof ApiError)||error.status!==404)throw error;
   if(kind==='report'){
    const [pdf,ocr]=await Promise.all([legacyLibrary('pdf'),legacyLibrary('ocr')]);
    return groupReportLibrary(pdf,ocr);
   }
   const old=await request('document',kind==='pdf'?'/api/report-assets':kind==='ocr'?'/api/ocr/jobs':'/api/pipeline/tasks?limit=1000',{});
   if(kind==='pdf')return {items:items(old,'assets'),compatibility_view:true};
   if(kind==='ocr')return {items:items(old).map(r=>({...r,document_label:r.summary?.document_label || r.document_label,available_actions:[]})),compatibility_view:true};
   const all=items(old),active=all.filter(r=>['running','queued'].includes(r.status));
   return {running_count:active.filter(r=>r.status==='running').length,queued_count:active.filter(r=>r.status==='queued').length,active_tasks:active,recent_tasks:all.filter(r=>!['running','queued'].includes(r.status)).slice(0,8),compatibility_view:true};
  }};
  const mine=kind;
  run().then(data=>{if(pending.current!==mine)return;pending.current='';setState({data,error:'',loading:false});})
   .catch(error=>{if(pending.current!==mine)return;pending.current='';setState(previous=>({...previous,error:error.message,loading:false}));});
 },[kind]);
 useEffect(()=>{load();},[load,auth]);
 // Polling waits for the request that is still running instead of restarting it.
 useEffect(()=>{if(epoch>0)load();},[epoch]);
 return state;
}
