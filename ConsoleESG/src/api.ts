export type Row = Record<string, any>;
export type Service = 'document' | 'targeted' | 'publication';
export type EvidenceLocation = {state:'verified'|'synthetic'|'unavailable'|'unknown';asset_id?:string|null;ir_run_id?:string|null;ir_revision?:number|null;pdf_available?:boolean;ir_available?:boolean;reason?:string|null};
export function verifiedEvidence(location:EvidenceLocation|undefined){return {assetId:location?.pdf_available===true&&typeof location.asset_id==='string'?location.asset_id:undefined,irRunId:location?.ir_available===true&&typeof location.ir_run_id==='string'?location.ir_run_id:undefined};}
let token = '';
let operator = '';
let operatorRoles:string[] = [];
const secrets:Record<string,string> = {};
export const credentials = {get token(){return token;},get operator(){return operator;},get roles(){return [...operatorRoles];},set(value:string,actor:string,roles:string[]=[]){token=value.trim();operator=actor.trim();operatorRoles=[...roles];},clear(){token='';operator='';operatorRoles=[];Object.keys(secrets).forEach(k=>delete secrets[k]);},secret:(name:string)=>secrets[name] || null,setSecret:(name:string,value:string)=>{secrets[name]=value.trim();}};
export type ApiErrorContext={service:Service;path:string;kind:'http'|'network'|'response_format';requestId?:string;code?:string;layer?:string};
export class ApiError extends Error {constructor(message:string,public status:number,public detail:unknown,public context?:ApiErrorContext){super(message);this.name='ApiError';}}
export function errorText(value:unknown):string {if(typeof value==='string')return value;if(Array.isArray(value))return value.map(errorText).join('；');if(value && typeof value==='object'){const x=value as Row;return x.message || x.msg || x.reason || Object.values(x).map(errorText).join('；');}return String(value ?? '请求失败');}
const serviceLabels:Record<Service,string>={document:'报告服务',targeted:'提取服务',publication:'数据服务'};
function safeErrorText(value:string):string {
  let message=value.replace(/[\u0000-\u001f\u007f]/g,' ').trim();
  for(const secret of [token,...Object.values(secrets)].filter(Boolean))message=message.split(secret).join('[已隐藏]');
  if(/traceback|\bfile\s+"[^"]+",\s*line\s+\d+|\b(?:KeyError|TypeError|ValueError|AssertionError):|authorization\s*:|\bat\s+\S+\s+\([^)]*:\d+:\d+\)|<\/?(?:html|script|body)\b/i.test(message))return '服务未提供可显示的错误说明，请凭问题编号检查日志';
  return message.length>500?`${message.slice(0,500)}…`:message;
}
function errorIdentifier(value:unknown):string|undefined{return typeof value==='string'&&/^[A-Za-z0-9._:-]{1,128}$/.test(value)?safeErrorText(value):undefined;}
function errorContext(service:Service,path:string,kind:ApiErrorContext['kind'],detail?:unknown,response?:Response):ApiErrorContext {
 const fields=detail&&typeof detail==='object'&&!Array.isArray(detail)?detail as Row:{};
 return {service,path:safeErrorText(path.split('?')[0]),kind,
  requestId:errorIdentifier(fields.request_id) || errorIdentifier(response?.headers.get('X-Request-ID')),
  code:errorIdentifier(fields.code),layer:errorIdentifier(fields.layer)};
}
function responseMessage(detail:unknown,status:number):string {
 if(typeof detail==='string'&&(status<500||/[\u4e00-\u9fff]/.test(detail)))return safeErrorText(detail);
 if(Array.isArray(detail)&&status<500){const messages=detail.map(item=>typeof item==='string'?item:item&&typeof item==='object'?item.msg || item.message || item.reason:undefined).filter((item):item is string=>typeof item==='string');if(messages.length)return safeErrorText(messages.join('；'));}
 if(detail&&typeof detail==='object'&&!Array.isArray(detail)){const fields=detail as Row;const message=fields.message || fields.msg || fields.reason;if(typeof message==='string'&&(status<500||errorIdentifier(fields.code)||/[\u4e00-\u9fff]/.test(message)))return safeErrorText(message);}
 return status>=500?'服务发生内部错误，请凭问题编号检查日志':'请求未通过服务检查，请核对输入与操作权限';
}
function contextualMessage(message:string,status:number,context:ApiErrorContext):string {
 return `${serviceLabels[context.service]} ${context.path}（${status?`HTTP ${status}`:'连接失败'}）：${message}${context.requestId?` · 问题编号 ${context.requestId}`:''}`;
}
export async function request<T=Row>(service:Service,path:string,options:RequestInit={}):Promise<T>{
  if(!path.startsWith('/') || path.startsWith('//'))throw new Error('接口路径必须是当前服务中的绝对路径');
  const headers=new Headers(options.headers);
  if(service==='publication' && path.startsWith('/internal/')){if(!token)throw new Error('请先启用操作权限');headers.set('Authorization',`Bearer ${token}`);}
  if(service==='document' && (path.startsWith('/api/console/') || /^\/api\/report-sessions\/[^/]+\/materialize$/.test(path)) && token)headers.set('Authorization',`Bearer ${token}`);
  if(token && ((service==='document' && path.startsWith('/api/production-automations')) ||
    (service==='targeted' && /^\/api\/report-sessions\/[^/]+\/intermediates(?:\/|$)/.test(path))))headers.set('Authorization',`Bearer ${token}`);
  if(token&&['document','targeted'].includes(service)&&(options.method?.toUpperCase()==='DELETE'||path.startsWith('/api/ocr-library')||/\/deletion$/.test(path)))headers.set('Authorization',`Bearer ${token}`);
  if(options.body && !(options.body instanceof FormData))headers.set('Content-Type','application/json');
  let result:Response;
  try{result=await fetch(`/${service}${path}`,{...options,headers});}
  catch(error){if(options.signal?.aborted||(error as Error)?.name==='AbortError')throw error;const context=errorContext(service,path,'network');const message=contextualMessage('无法连接服务，请检查服务是否运行',0,context);throw new ApiError(message,0,{message:'无法连接服务'},context);}
  if(result.status===204)return undefined as T;
  let data:unknown;
  try{data=await result.json();}
  catch(error){
   if(options.signal?.aborted||(error as Error)?.name==='AbortError')throw error;
   const context=errorContext(service,path,'response_format',undefined,result);
   const message=result.ok?'响应格式不符合接口合同，未返回有效 JSON':result.status>=500?'服务返回错误，未提供有效 JSON 错误详情':'请求被拒绝，未提供有效 JSON 错误详情';
   throw new ApiError(contextualMessage(message,result.status,context),result.status,{code:'invalid_json_response',message},context);
  }
  if(!result.ok){const detail=data&&typeof data==='object'&&!Array.isArray(data)&&Object.hasOwn(data,'detail')?(data as Row).detail:data;const context=errorContext(service,path,'http',detail,result);const message=responseMessage(detail,result.status);throw new ApiError(contextualMessage(message,result.status,context),result.status,{message,...(context.code?{code:context.code}:{}),...(context.requestId?{request_id:context.requestId}:{}),...(context.layer?{layer:context.layer}:{})},context);}
  return data as T;
}
export const post=<T=Row>(service:Service,path:string,body:Row={})=>request<T>(service,path,{method:'POST',body:JSON.stringify(body)});
export const items=(value:unknown,key='items'):Row[]=>Array.isArray(value)?value:(value && typeof value==='object' && Array.isArray((value as Row)[key])?(value as Row)[key]:[]);
export const doc=(path:string)=>request('document',path);
export const published=(path:string)=>request('publication',path);
export const reportLabel=(report:Row)=>report.report_metadata?.title || report.metadata?.title || report.report?.metadata?.title || report.file_name || report.name || '报告名称待确认';
export const assetFile=(assetId:string,page=1)=>`/document/api/report-assets/${encodeURIComponent(assetId)}/file#page=${page}`;
export function artifactUrl(run:string,path:string):string | undefined {if(!path || path.includes('://') || path.startsWith('/') || path.split('/').includes('..'))return;return `/document/document_ir_output/${encodeURIComponent(run)}/${path.split('/').map(encodeURIComponent).join('/')}`;}
export const navigate=(path:string)=>{window.location.hash=path.startsWith('/')?path:`/${path}`;};
