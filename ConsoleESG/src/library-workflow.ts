import {credentials,type Row} from './api';
import {buildAutomationRequest,createAutomation,defaultProcessingConfig,previewAutomation,type ProductionTarget} from './automation';
import {scopeFromSelection} from './components/ScopeSelector';

/** Use the existing production coordinator and the exact major profile. */
export async function startLibraryProduction(assets:Row[],target:ProductionTarget,profile?:Row,catalog?:Row,processingMode:'auto'|'reprocess'='auto'){
 if(!credentials.roles.includes('admin'))throw new Error('数据平台未连接或当前身份没有管理权限，暂不能开始处理。');
 let scope={standards:[],originalIds:[],kind:'none'} as ReturnType<typeof scopeFromSelection>;
 if(target!=='processing'){
  if(!profile?.selection_snapshot||!profile.session_standards?.length)throw new Error('主要指标配置尚未就绪，请刷新后重试。');
  scope=scopeFromSelection(catalog?.packages || [],new Set<string>(profile.session_standards.flatMap((p:Row)=>p.metric_ids)),profile.selection_snapshot);
 }
 const request=buildAutomationRequest({name:target==='processing'?'PDF识别与结构处理':'PDF主要指标提取',assetIds:assets.map(a=>a.asset_id),assets,reports:[],reportSelections:{},target,
  processingMode,processingConfig:{...defaultProcessingConfig,execute_required_reviews:false},scope,extensionSelections:[],semanticProvider:'local_qwen',semanticModel:'',completeness:'balanced',limitedEvidence:true,cloudAuthorization:false,cloudOcr:false,failurePolicy:'continue',budget:{}});
 const preview=await previewAutomation(request);
 if(!preview.can_submit)throw new Error((preview.items || []).flatMap((i:Row)=>(i.blockers || []).map((b:Row)=>b.message)).join('；') || '当前报告未满足执行条件');
 const result=await createAutomation(request,preview.preview_id,crypto.randomUUID(),true);
 window.dispatchEvent(new CustomEvent('show-processing-status',{detail:{automationId:result.automation_id}}));
 return result;
}
