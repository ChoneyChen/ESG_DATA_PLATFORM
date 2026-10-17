import {afterEach,expect,it,vi} from 'vitest';
import {ApiError,credentials,request} from './api';

afterEach(()=>{credentials.clear();vi.unstubAllGlobals();});

it('纯文本500保留真实错误状态，并指明服务及接口，不显示响应原文',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('Internal Server Error\nprivate backend traceback',{status:500,headers:{'Content-Type':'text/plain','X-Request-ID':'synthetic-500-id'}})));
 const error=await request('document','/api/pipeline/tasks').catch(value=>value);
 expect(error).toBeInstanceOf(ApiError);
 expect(error.status).toBe(500);
 expect(error.context).toMatchObject({service:'document',path:'/api/pipeline/tasks',kind:'response_format',requestId:'synthetic-500-id'});
 expect(error.message).toContain('报告服务 /api/pipeline/tasks（HTTP 500）');
 expect(error.message).toContain('问题编号 synthetic-500-id');
 expect(JSON.stringify(error)).not.toContain('traceback');
});

it('结构化内部错误显示安全中文说明及问题编号，保留code和layer诊断',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({detail:{code:'internal_service_error',message:'报告服务内部错误，请使用问题编号定位',request_id:'synthetic-request-42',layer:'document',stack:'private trace',headers:{Authorization:'Bearer private-server-key'}}}),{status:500,headers:{'Content-Type':'application/json'}})));
 const error=await request('document','/api/report-assets').catch(value=>value);
 expect(error).toBeInstanceOf(ApiError);
 expect(error.message).toContain('报告服务内部错误，请使用问题编号定位');
 expect(error.context).toMatchObject({service:'document',path:'/api/report-assets',code:'internal_service_error',layer:'document',requestId:'synthetic-request-42'});
 expect(error.detail).toEqual({code:'internal_service_error',message:'报告服务内部错误，请使用问题编号定位',request_id:'synthetic-request-42',layer:'document'});
 expect(JSON.stringify(error)).not.toContain('private');
});

it('成功状态中的HTML不是成功数据，不能被转成空目录',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('<html>wrong proxy target</html>',{status:200,headers:{'Content-Type':'text/html'}})));
 const error=await request('targeted','/api/ir-runs').catch(value=>value);
 expect(error).toBeInstanceOf(ApiError);
 expect(error.status).toBe(200);
 expect(error.context).toMatchObject({service:'targeted',path:'/api/ir-runs',kind:'response_format'});
 expect(error.message).toContain('响应格式不符合接口合同');
 expect(JSON.stringify(error)).not.toContain('wrong proxy target');
});

it('代理不可用503不会被误分类为用户输入错误',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response('<html>Upstream service unavailable</html>',{status:503})));
 const error=await request('publication','/internal/v1/me').catch(value=>value);
 expect(error).not.toBeInstanceOf(ApiError); // Missing credentials prevent any request.
 credentials.set('synthetic-operator-key','synthetic-operator');
 const connectedError=await request('publication','/internal/v1/me').catch(value=>value);
 expect(connectedError.status).toBe(503);
 expect(connectedError.context).toMatchObject({service:'publication',path:'/internal/v1/me',kind:'response_format'});
 expect(connectedError.message).toContain('数据服务 /internal/v1/me（HTTP 503）');
});

it('输入验证错误只显示msg，诊断不携带查询凭据或输入payload',async()=>{
 credentials.set('synthetic-access-key','synthetic-user');credentials.setSecret('qiniu_api_key','synthetic-model-key');
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({detail:[{loc:['body','name'],msg:'计划名称不可为空',input:{qiniu_api_key:'unknown-body-secret'}}]}),{status:422,headers:{'Content-Type':'application/json'}})));
 const error=await request('document','/api/production-automations/preview?access=synthetic-access-key',{method:'POST',body:'{}'}).catch(value=>value);
 expect(error.status).toBe(422);
 expect(error.message).toContain('计划名称不可为空');
 expect(error.context.path).toBe('/api/production-automations/preview');
 expect(JSON.stringify(error)).not.toContain('access=');
 expect(JSON.stringify(error)).not.toContain('synthetic-access-key');
 expect(JSON.stringify(error)).not.toContain('unknown-body-secret');
});

it('服务错误若回显当前临时密钥，会遮蔽密钥文本',async()=>{
 credentials.set('synthetic-access-key','synthetic-user');credentials.setSecret('ocr_token','synthetic-ocr-key');
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({detail:'校验失败：synthetic-access-key / synthetic-ocr-key'}),{status:409,headers:{'Content-Type':'application/json'}})));
 const error=await request('document','/api/production-automations/preview').catch(value=>value);
 expect(error.message).toContain('[已隐藏]');
 expect(JSON.stringify(error)).not.toContain('synthetic-access-key');
 expect(JSON.stringify(error)).not.toContain('synthetic-ocr-key');
});

it('网络连接失败具有服务上下文，不传播原始fetch异常内容',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>{throw new TypeError('private connection details');}));
 const error=await request('document','/api/report-sessions').catch(value=>value);
 expect(error.status).toBe(0);
 expect(error.context).toMatchObject({service:'document',path:'/api/report-sessions',kind:'network'});
 expect(error.message).toContain('连接失败');
 expect(JSON.stringify(error)).not.toContain('private connection details');
});

it('资源切换的主动取消保留AbortError，不伪造服务故障',async()=>{
 const cancellation=new DOMException('synthetic cancellation','AbortError');
 vi.stubGlobal('fetch',vi.fn(async()=>{throw cancellation;}));
 const error=await request('document','/api/report-assets').catch(value=>value);
 expect(error).toBe(cancellation);
});

it('有效JSON及无内容204继续按HTTP合同成功，不添加虚假错误',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>String(url).endsWith('/empty')?new Response(null,{status:204}):new Response(JSON.stringify({items:[]}),{headers:{'Content-Type':'application/json'}})));
 await expect(request('document','/api/synthetic/valid')).resolves.toEqual({items:[]});
 await expect(request('document','/api/synthetic/empty')).resolves.toBeUndefined();
});
