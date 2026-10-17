import {post, reportLabel, type Row} from './api';
import type {Scope} from './components/ScopeSelector';

export type ProductionTarget = 'processing' | 'processing_extraction' | 'processing_extraction_delivery';
export type ProductionOrder = 'selection' | 'year_ascending' | 'year_descending' | 'name';
export const productionTargets:Record<ProductionTarget,string> = {
  processing:'报告处理', processing_extraction:'处理 → 定向提取',
  processing_extraction_delivery:'处理 → 定向提取 → 候选交付',
};
export const automationLabels:Record<string,string> = {
  planned:'计划待启动',active:'自动执行中',pausing:'等待当前任务结束后暂停',paused:'已暂停派发',
  waiting_credentials:'等待运行密钥',archived:'已归档',completed:'执行完成',partial:'执行结束，含失败或阻塞',
  cancelling:'正在取消',cancelled:'已取消',pending:'待派发',queued:'已排队',running:'正在执行',
  ready:'可以部署',blocked:'需处理',failed:'失败',skipped:'已跳过',interrupted:'已中断',imported:'候选已导入',not_imported:'尚未交付',
  processing:'报告处理',extraction:'定向提取',extracting:'定向提取中',delivery:'候选交付',delivering:'候选交付中',done:'已完成',
};
export const automationLabel=(value:unknown)=>automationLabels[String(value)] || String(value || '未提供');

export function registeredReportsForAsset(reports:Row[],assetId:string):Row[] {
  return reports.filter(report => (report.asset_versions || []).some((version:Row)=>version.asset_id===assetId));
}
function registeredYear(asset:Row,reports:Row[]):number|undefined {
  const matches=registeredReportsForAsset(reports,asset.asset_id);
  const metadata=matches.length===1?matches[0].metadata:asset.report_metadata;
  const year=metadata?.reporting_year;
  return typeof year==='number' && Number.isInteger(year)?year:undefined;
}
export function orderReportIds(ids:string[],assets:Row[],reports:Row[],order:ProductionOrder):string[] {
  if(order==='selection')return [...ids];
  const byId=new Map(assets.map(asset=>[asset.asset_id,asset]));
  return ids.map((id,index)=>({id,index,asset:byId.get(id)})).sort((a,b)=>{
    if(order==='name')return reportLabel(a.asset || {}).localeCompare(reportLabel(b.asset || {}),'zh-CN') || a.index-b.index;
    const aYear=a.asset?registeredYear(a.asset,reports):undefined,bYear=b.asset?registeredYear(b.asset,reports):undefined;
    if(aYear===undefined)return bYear===undefined?a.index-b.index:1;
    if(bYear===undefined)return -1;
    return (order==='year_ascending'?aYear-bYear:bYear-aYear) || a.index-b.index;
  }).map(item=>item.id);
}
export function moveReport(ids:string[],index:number,direction:-1|1):string[] {
  const next=[...ids],other=index+direction;
  if(index<0||index>=ids.length||other<0||other>=ids.length)return next;
  [next[index],next[other]]=[next[other],next[index]];
  return next;
}
export const defaultProcessingConfig:Row={
  ocr_provider:'local_paddleocr',allow_cloud_ocr:false,execute_optional_reviews:false,
  execute_required_reviews:true,review_provider:'local_qwen',render_dpi:144,
  max_auto_review_rounds:3,allow_limited_evidence:true,
};
export type AutomationDraft={name:string;assetIds:string[];assets:Row[];reports:Row[];
  reportSelections:Record<string,string>;target:ProductionTarget;processingMode:string;processingConfig:Row;
  scope:Scope;extensionSelections:Row[];semanticProvider:string;semanticModel:string;
  completeness:string;limitedEvidence:boolean;cloudAuthorization:boolean;cloudOcr:boolean;
  failurePolicy:'continue'|'pause';budget:Row;
};
export function buildAutomationRequest(draft:AutomationDraft):Row {
  if(!draft.name.trim())throw new Error('请填写计划名称');
  if(!draft.assetIds.length)throw new Error('请选择至少一份报告');
  if(draft.assetIds.length>500)throw new Error('单个生产计划最多包含 500 份报告，请拆分批次');
  if(new Set(draft.assetIds).size!==draft.assetIds.length)throw new Error('同一 PDF 不能在同一批次重复加入');
  const items=draft.assetIds.map(assetId=>{
    const asset=draft.assets.find(value=>value.asset_id===assetId);
    if(!asset)throw new Error(`报告 ${assetId} 不再位于当前报告库，请核对原选择`);
    if(asset.lifecycle?.state==='archived'||asset.lifecycle?.state==='discarded')throw new Error(`报告 ${reportLabel(asset)} 已归档或删除，请恢复后再加入计划`);
    if(typeof asset.sha256!=='string'||! /^[a-f0-9]{64}$/.test(asset.sha256))throw new Error(`报告 ${reportLabel(asset)} 缺少已核对的内容指纹`);
    const reportId=draft.reportSelections[assetId];
    const report=draft.reports.find(value=>value.report_id===reportId);
    if(reportId&&!registeredReportsForAsset(draft.reports,assetId).some(value=>value.report_id===reportId))throw new Error(`报告 ${reportLabel(asset)} 的业务身份与所选 PDF 不一致`);
    if(draft.target==='processing_extraction_delivery'&&!report)throw new Error(`报告 ${reportLabel(asset)} 需要明确关联业务报告身份，才能交付候选`);
    if(report&&(!Number.isInteger(report.revision)||report.revision<1))throw new Error(`报告 ${reportLabel(asset)} 的身份修订不可读取`);
    return {asset_id:assetId,source_sha256:asset.sha256,...(report?{report_id:report.report_id,report_revision:report.revision}:{})};
  });
  if(new Set(items.map(item=>item.source_sha256)).size!==items.length)throw new Error('所选不同资产含有同一份 PDF 内容，请核对后移除重复来源');
  const request:Row={name:draft.name.trim(),items,target:draft.target,processing:{mode:draft.processingMode,config:{...draft.processingConfig}},failure_policy:draft.failurePolicy};
  if(draft.cloudAuthorization||draft.cloudOcr||draft.processingConfig.allow_cloud_ocr||draft.processingConfig.ocr_provider==='paddle_api'||draft.processingConfig.review_provider==='qiniu'||draft.semanticProvider==='qiniu_vlm')throw new Error('批量自动化目前支持本地模型。云服务请使用单报告任务，不将云密钥保存到计划中');
  if(draft.target==='processing')return request;
  if(!draft.scope.standards.length&&!draft.extensionSelections.length)throw new Error('请明确选择主要条款、包内核心或自定义输出范围');
  // The platform foundation datapoints use the bounded one-call-per-table route.
  const foundation=draft.scope.kind==='platform_major'&&!draft.extensionSelections.length;
  request.extraction={input:{kind:'pdf',asset_id:items[0].asset_id},name:draft.name.trim().slice(0,120),
    standards:draft.scope.standards,extension_selections:draft.extensionSelections,
    ...(draft.scope.selection_snapshot?{selection_snapshot:draft.scope.selection_snapshot}:{}),
    role_config:{semantic_brain:{provider:draft.semanticProvider,model:draft.semanticProvider==='qiniu_vlm'?draft.semanticModel.trim()||null:null},
      specialist:{provider:foundation?'local_qwen':'local_nuextract',model:null},cloud_authorization:{allowed:draft.cloudAuthorization,allow_ocr_api:draft.cloudOcr}},
    completeness:draft.completeness,allow_limited_evidence:draft.limitedEvidence,semantic_search:true,
    ...(foundation?{extraction_route:'foundation16'}:{}),
    ...(Object.keys(draft.budget).length?{budget:{...draft.budget}}:{}),
  };
  return request;
}
export const previewAutomation=(request:Row)=>post('document','/api/production-automations/preview',request);
export const createAutomation=(request:Row,previewId:string,submissionKey:string,start=true)=>post('document','/api/production-automations',{request,preview_id:previewId,submission_key:submissionKey,start});
export const automationAction=(id:string,action:string,revision:number,reason:string)=>post('document',`/api/production-automations/${encodeURIComponent(id)}/${action}`,{expected_revision:revision,reason});
