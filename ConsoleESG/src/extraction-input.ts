import {items,type Row} from './api';

export type ExtractionInputChoice={mode:'single'|'batch'|'history';assetId:string;assetIds:string[];evidence:'auto'|'fixed';irId:string};
export function initialExtractionInput(params:URLSearchParams):ExtractionInputChoice{
 const assetIds=(params.get('assets') || '').split(',').filter(Boolean),assetId=params.get('asset') || assetIds[0] || '',irId=params.get('ir') || '';
 return {mode:assetIds.length?'batch':irId&&!assetId?'history':'single',assetId,assetIds,evidence:irId?'fixed':'auto',irId};
}
export function readableEvidence(row:Row,limited:boolean){
 return row.can_build_full_evidence===true || (row.can_build_limited_evidence===true&&limited&&row.evidence_policy_mode==='limited');
}
export function reportEvidenceVersions(asset:Row|undefined,library:Row|undefined,catalog:Row[]):Row[]{
 if(!asset)return [];
 const parent=items(library).find(row=>row.asset_id===asset.asset_id&&row.sha256===asset.sha256);
 const available=new Map(catalog.map(row=>[row.run_id,row]));const rows:Row[]=[];
 for(const ocr of parent?.ocr_versions || [])for(const ir of ocr.ir_versions || []){
  const entry=available.get(ir.run_id);if(!entry)continue;
  if([ir.source_pdf_sha256,entry.source_pdf_sha256].some(sha=>sha&&sha!==asset.sha256))continue;
  if(entry.ocr_run_id&&entry.ocr_run_id!==ocr.run_id)continue;
  rows.push({...ir,...entry,source_ocr_run_id:ocr.run_id});
 }
 return [...new Map(rows.map(row=>[row.run_id,row])).values()];
}
export function extractionInput(choice:ExtractionInputChoice,assets:Row[],library:Row|undefined,catalog:Row[],limited:boolean,assetId=choice.assetId):Row{
 if(choice.mode==='history'){
  const version=catalog.find(row=>row.run_id===choice.irId);
  if(!version||!readableEvidence(version,limited))throw new Error('请选择可读取的历史结构证据；所选版本不可用时需重新选择。');
  return {kind:'document_ir',ir_run_id:version.run_id};
 }
 const asset=assets.find(row=>row.asset_id===assetId);
 if(!asset)throw new Error('请先选择报告。');
 if(choice.mode==='batch'||choice.evidence==='auto')return {kind:'pdf',asset_id:asset.asset_id};
 const version=reportEvidenceVersions(asset,library,catalog).find(row=>row.run_id===choice.irId);
 if(!version)throw new Error('所选结构证据不属于当前报告或已不可读取，请重新选择。');
 if(!readableEvidence(version,limited))throw new Error('所选版本未满足当前证据要求，请选择其他版本或自动准备证据。');
 return {kind:'document_ir',ir_run_id:version.run_id};
}
