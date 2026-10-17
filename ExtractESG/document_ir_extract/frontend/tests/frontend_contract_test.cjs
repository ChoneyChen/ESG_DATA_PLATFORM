/* Browser contract verification uses fake HTTP responses only. It cannot start
 * a worker or call OCR, an embedding model, VLM, or an extraction provider. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.ESG_TEST_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname, '..');
const errors = [], requests = [];
// This optional fixture is emitted by ResultInspectionService over real
// temporary checkpoint/source artifacts, not an idealized UI-only partial.
const stageInspection = process.env.ESG_TEST_STAGE_INSPECTION ? JSON.parse(fs.readFileSync(process.env.ESG_TEST_STAGE_INSPECTION,'utf8')) : null;
const metric = {metric_id:'esrs.2023-set1.e1-6.dp07', source_datapoint_id:'E1-6_07', labels:{zh:'Scope 1 排放量'}, description:'Scope 1', data_class:'quantitative', facts:[], status:'not_found'};
const standard = {format_version:'2.1',compilation:{source_digest:'0'.repeat(64)},manifest:{package_id:'esrs.2023-set1.e1-6',package_version:'2.0.0',status:'draft',core_source_datapoint_ids:[metric.source_datapoint_id],standard:{disclosure_requirement:'E1-6'}},metrics:[metric],relations:[]};
const catalog = [{package_id:standard.manifest.package_id,package_version:'2.0.0',status:'draft',disclosure_requirement:'E1-6',metric_count:1,format_version:'2.1',source_digest:'0'.repeat(64),core_source_datapoint_ids:[metric.source_datapoint_id],metric_summaries:[metric]}];
const asset = {asset_id:'asset-fake',file_name:'示例报告.pdf',size_bytes:100,sha256:'0'.repeat(64),ocr_status:'unprocessed',ocr_run_count:0};
const session = {session_id:'plan-fake',name:'示例报告.pdf',execution_status:'finished',result_status:'partial',revision:'r1',job_ids:['job-fake'],available_actions:[{action:'resume',label:'恢复未完成动作',endpoint:'/api/report-sessions/plan-fake/resume',scope:{job_ids:['job-fake']},calls_model:true,incurs_cost:false}],projections:[{job_id:'job-fake',package_id:catalog[0].package_id,selected_metric_ids:[metric.metric_id]}]};
if (stageInspection) {session.counts=stageInspection.stage_summary.counts;session.gaps=stageInspection.gaps;session.errors=stageInspection.stage_summary.errors;session.error='interrupted';session.result_status=stageInspection.stage_summary.result_status;session.execution_status='interrupted';}
const technicalError = "Model call 'repair_format' exceeded its external hard deadline; checkpoints retained.";
const deadlineError = {code:'execution_deadline',category:'runtime',message:'执行达到时限，检查点与阶段成果保留',technical_detail:technicalError,recoverable:true,next_action:'resume'};
session.error_detail = deadlineError;
session.error = session.error || technicalError;
const fact = {source_fact_id:'fact-fake',source_row_label:'范围一排放',identity_raw:{reporting_period:'2024',reporting_entity:'示例公司',entity_aggregation_role:'报告主体',scope:'Scope 1',method:'报告声明方法',reporting_boundary:'境内运营',carrier:'天然气'},value_raw:'100',unit_raw:'tCO2e',physical_sources:[{pdf_page_index:2,ir_object_id:'table-fake',bbox:{x0:0,y0:0,x1:10,y1:10}}]};
const job = {job_id:'job-fake',status:'partial',stage:'complete',progress_current:1,progress_total:1,created_at:'2026-10-02T01:00:00Z',updated_at:'2026-10-02T01:01:00Z',request:{ir_run_id:'ir-fake',package_id:catalog[0].package_id,package_version:'2.0.0',metric_ids:[metric.metric_id],semantic_provider:'local_qwen',semantic_fill:true},summary:{}};
job.error = technicalError; job.error_detail = deadlineError;
job.progress = {stage_code:'interrupted',label:'执行中断',status:'partial',unit:'unknown',determinate:false,current:null,total:null,percent:null,current_call:{tool:'repair_format',phase:'generation',elapsed_seconds:238}};
job.summary.agent = {state:'running',current_call:{tool:'repair_format',phase:'generation',elapsed_seconds:238},steps:[]};
let created = false, failSessionList = false, emptySessionFacts = false;
let failAssets = false, failIr = false, incompatible = false;
let invalidSelectedPackage = false;
const server = http.createServer((request,response) => {
  const target = path.resolve(root, '.' + decodeURIComponent(request.url.split('?')[0]));
  if (!target.startsWith(root + path.sep)) {response.writeHead(403);response.end();return;}
  fs.readFile(target,(error,data) => { if(error){response.writeHead(404);response.end();return;} response.writeHead(200,{'Content-Type': target.endsWith('.js')?'text/javascript':target.endsWith('.css')?'text/css':'text/html'});response.end(data); });
});
(async () => {
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,executablePath:process.env.ESG_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context=await browser.newContext();
  const page=await context.newPage();
  page.on('pageerror', error=>errors.push(error.message));
  await page.route('**/*', async route => {
    const request=route.request(), url=new URL(request.url());
    if (![18080,18180].includes(Number(url.port))) return route.continue();
    requests.push({path:url.pathname,method:request.method(),body:request.postData() && JSON.parse(request.postData())});
    let payload=[];
    if (url.pathname === '/api/test-dedup') {await new Promise(resolve=>setTimeout(resolve,30));payload={ok:true};}
    else if(url.pathname==='/health') payload={status:'ok'};
    else if(url.pathname==='/api/report-assets') {if(failAssets) return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'报告库测试故障'})});payload={assets:[asset],asset_count:1,root:'/fake/pdf'};}
    else if(url.pathname==='/api/pipeline/compatibility') payload={ready:!incompatible,blockers:incompatible?['旧后端不支持标准包 2.1']:[]};
    else if(url.pathname==='/api/pipeline/standards/catalog') payload={packages:invalidSelectedPackage?[]:catalog,diagnostics:{valid_package_count:invalidSelectedPackage?0:1,invalid_package_count:invalidSelectedPackage?1:0,issues:invalidSelectedPackage?[{path:'fixture-package.json',error:'包校验测试故障'}]:[]},planned:[],historical:[]};
    else if(url.pathname==='/api/pipeline/standards'||url.pathname==='/api/standards') payload=catalog;
    else if(url.pathname.startsWith('/api/pipeline/standards/')||url.pathname.startsWith('/api/standards/esrs.')) payload=standard;
    else if(url.pathname==='/api/pipeline/status') payload={counts:{},services:{document_backend:{available:true},targeted_backend:{available:true,url:'http://127.0.0.1:18180'}}};
    else if(url.pathname==='/api/storage/inventory') payload={available:true,summary:{},documents:[]};
    else if(url.pathname==='/api/models/status'||url.pathname==='/api/ocr/providers/status') payload={providers:{}};
    else if(url.pathname==='/api/document-ir/review-worklist') payload={entries:[]};
    else if(url.pathname==='/api/health') payload={status:'ok',models:{}};
    else if(url.pathname==='/api/config'||url.pathname==='/api/standards/diagnostics'||url.pathname==='/api/result-catalog') payload={};
    else if(url.pathname==='/api/ir-runs') {if(failIr) return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'证据列表测试故障'})});payload=[{run_id:'ir-fake',document_label:'示例已有报告',ir_revision:1,page_count:10,can_build_evidence:true}];}
    else if(url.pathname==='/api/report-sessions/preview') payload={roles:request.postDataJSON().role_config,output_count:1,selected_outputs:[{source_datapoint_id:metric.source_datapoint_id,label:'Scope 1 排放量'}],context_requirements:[{purpose:'report_identity',clauses:[{source_datapoint_id:'BP-1_01',label:'编制基础'}],output_policy:'context_only'}],identity_requirements:[{source_datapoint_id:metric.source_datapoint_id,label:'Scope 1 排放量',identity_axes:['reporting_entity','reporting_period']}],formula_operands:[],reused_inputs:[],internal_dependencies:[{metric_ids:[]}],budget:{retrieval_object_top_n:3,shared_object_budget:3,agent_max_steps:8,total_timeout_seconds:14400,call_timeout_seconds:600,max_model_calls:16,max_format_repairs:4,max_input_tokens:50000,max_output_tokens:6000},evidence_limitations:[],document_preparation:{ocr_reused:false,ir_reused:false}};
    else if(url.pathname==='/api/report-sessions' && request.method()==='POST'){created=true;payload=session;}
    else if(url.pathname==='/api/report-sessions'){if(failSessionList){return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'测试断连'})});}payload=created?[session]:[];}
    else if(url.pathname==='/api/report-sessions/plan-fake/facts') payload={facts:emptySessionFacts?[]:[fact],gaps:[],projections:session.projections};
    else if(url.pathname.startsWith('/api/report-sessions/plan-fake')) payload=session;
    else if(url.pathname==='/api/jobs') payload=[job];
    else if(url.pathname==='/api/jobs/job-fake') payload=job;
    else if(url.pathname==='/api/jobs/job-fake/inspection') payload=stageInspection || {availability:'ready',metrics:[{...metric,facts:[{...fact,fact_id:'fact-fake',evidence:fact.physical_sources}],review_status:'pending',element_columns:[]}],overview:{metric_count:1,quantitative_fact_count:1},model_execution:{},integrity_issues:[],downloads:{}};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)});
  });
  const base=`http://127.0.0.1:${server.address().port}`;
  await page.goto(`${base}/index.html`);
  await page.getByRole('button',{name:'提取此报告'}).click();
  await page.waitForSelector('#session-standards [data-standard-package] > summary');
  assert.equal(requests.some(request=>request.path==='/api/pipeline/standards/esrs.2023-set1.e1-6/2.0.0'),false,'full package must not be downloaded with the lightweight catalog');
  await page.locator('#session-standards [data-standard-package] > summary').click();
  await page.waitForSelector('#session-standards input');
  await page.locator('#session-standards input').check();
  assert.equal(await page.locator('#session-report').inputValue(),'asset-fake');
  await page.getByRole('button',{name:'预览实际计划',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#session-create').disabled);
  assert.match(await page.locator('#session-preview').innerText(),/模型总调用上限：16/);
  assert.match(await page.locator('#session-preview').innerText(),/协议修复调用上限：4/);
  assert.match(await page.locator('#session-preview').innerText(),/总输入 token 上限：50000/);
  assert.match(await page.locator('#session-preview').innerText(),/总输出 token 上限：6000/);
  assert.match(await page.locator('#session-preview').innerText(),/所选输出（1 个/);
  assert.match(await page.locator('#session-preview').innerText(),/BP-1_01/);
  await page.getByRole('button',{name:'确认计划并加入队列',exact:true}).click();
  await page.waitForSelector('#session-list [data-session-open="plan-fake"]');
  const createRequest=requests.find(request=>request.path==='/api/report-sessions' && request.method==='POST').body;
  assert.equal(createRequest.input.kind,'pdf');
  assert.equal(createRequest.role_config.semantic_brain.provider,'local_qwen');
  assert.equal(createRequest.role_config.specialist.provider,'local_nuextract');
  assert.equal(createRequest.role_config.cloud_authorization.allowed,false);
  assert.equal(createRequest.standards.length,1);
  assert.equal('qiniu_api_key' in createRequest,false);
  await page.getByText('高级输入：使用已有文档证据版本',{exact:true}).click();
  assert.equal(requests.some(request=>request.path==='/api/jobs/job-fake/events'||request.path==='/api/jobs/job-fake/artifacts'),false,'audit files must be loaded only on demand');
  await page.locator('#session-ir').selectOption('ir-fake');
  await page.getByRole('button',{name:'预览实际计划',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#session-create').disabled);
  const irPreview=requests.filter(request=>request.path==='/api/report-sessions/preview').at(-1).body;
  assert.deepEqual(irPreview.input,{kind:'document_ir',ir_run_id:'ir-fake'});
  assert.deepEqual(irPreview.standards,createRequest.standards);
  await page.locator('#session-brain').selectOption('qiniu_vlm');
  await page.getByRole('button',{name:'预览实际计划',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#session-message').textContent.includes('需要明确允许'));
  assert.equal(await page.locator('#session-create').isDisabled(),true);
  await page.locator('[data-app-view-target="results"]').first().click();
  assert.equal(await page.locator('#session-result-list .task-error-message').innerText(),deadlineError.message);
  assert.equal(await page.locator('#session-result-list .task-error-technical').evaluate(node=>node.open),false,'technical errors start folded');
  assert.equal(await page.locator('#session-result-list .task-error-technical pre').isVisible(),false);
  await page.locator('#session-result-list .task-error-technical > summary').click();
  assert.match(await page.locator('#session-result-list .task-error-technical pre').innerText(),/repair_format.*hard deadline/,'producer original error remains inspectable');
  await page.locator('#session-result-list [data-session-open]').click();
  await page.waitForSelector('#session-result-perspective-content td');
  assert.match(await page.locator('#session-result-perspective-content').innerText(),/范围一排放/);
  assert.match(await page.locator('#session-result-perspective-content').innerText(),/2024/);
  assert.match(await page.locator('#session-result-perspective-content').innerText(),/示例公司/);
  assert.match(await page.locator('#session-result-perspective-content').innerText(),/境内运营/);
  assert.match(await page.locator('#session-result-perspective-content').innerText(),/天然气/);
  assert.match(await page.locator('#session-result-perspective-content').innerText(),/PDF 第 3 页/);
  if(stageInspection) {
    await page.locator('#session-report-results .task-error-technical > summary').click();
    assert.match(await page.locator('#session-report-results .session-errors').innerText(),/Fixture interruption/);
    assert.ok((await page.locator('#session-report-results .stage-summary').innerText()).includes(`已进入未完成 ${session.counts.unfinished_metrics} 项`));
    assert.match(await page.locator('#session-report-results .stage-gaps').innerText(),/Entity needs source confirmation/);
    emptySessionFacts=true;
    await page.locator('#session-result-list [data-session-open]').click();
    await page.waitForFunction(()=>document.querySelector('#session-result-perspective-content')?.textContent.includes('当前范围尚无可展示事实'));
    await page.locator('#session-report-results .task-error-technical > summary').click();
    assert.match(await page.locator('#session-report-results .stage-gaps').innerText(),/Entity needs source confirmation/,'empty facts payload must not hide detail gaps');
    assert.match(await page.locator('#session-report-results .session-errors').innerText(),/Fixture interruption/);
    emptySessionFacts=false;
    await page.locator('#session-result-list [data-session-open]').click();
    await page.waitForSelector('#session-result-perspective-content td');
  }
  await page.evaluate(async()=>{
    const {taskRevision}=await import('./src/status.js');
    const original={status:'running',stage:'reading',revision:1,updated_at:'old',counts:{candidate_source_facts:1},summary:{agent:{current_call:{phase:'evaluating',elapsed_seconds:1}}}};
    const heartbeat={...original,revision:999,updated_at:'new',heartbeat_at:'new',summary:{agent:{current_call:{phase:'evaluating',elapsed_seconds:999}}}};
    if(taskRevision(original)!==taskRevision(heartbeat)) throw new Error('heartbeat revision must not replace business view');
    if(taskRevision(original)===taskRevision({...heartbeat,counts:{candidate_source_facts:2}})) throw new Error('durable counts must refresh business view');
    if(taskRevision(original)===taskRevision({...heartbeat,summary:{agent:{current_call:{phase:'completed'}}}})) throw new Error('call phase must refresh business view');
    if(taskRevision({...original,document:{revision:1}})===taskRevision({...heartbeat,document:{revision:2}})) throw new Error('input revision must refresh business view');
  });
  await page.locator('[data-session-perspective="coverage"]').click();
  await page.locator('#session-report-results [data-session-job="job-fake"]').click();
  await page.waitForSelector('#tx-result-table td');
  assert.equal(await page.locator('#tx-job-summary .task-error-message').innerText(),deadlineError.message);
  assert.equal(await page.locator('#tx-job-summary .task-error-technical pre').isVisible(),false);
  await page.locator('#tx-job-summary .task-error-technical > summary').click();
  assert.match(await page.locator('#tx-job-summary .task-error-technical pre').innerText(),/repair_format.*hard deadline/);
  assert.match(await page.locator('#tx-job-summary').innerText(),/任务记录跨度/);
  assert.match(await page.locator('#tx-job-summary').innerText(),/含排队；执行与未观测时间分解未记录/);
  assert.match(await page.locator('#tx-job-list .business-progress').innerText(),/历史调用记录，任务已停止/);
  assert.doesNotMatch(await page.locator('#tx-job-list .business-progress').innerText(),/生成输出/);
  assert.equal(await page.locator('#tx-job-list [role="progressbar"]').count(),0,'unknown work total must remain indeterminate');
  await page.locator('#tx-agent-progress > details > summary').click();
  if (!stageInspection?.agent?.current_call || ['started','loading','prefill','generation','request','evaluating'].includes(stageInspection.agent.current_call.phase)) {
    assert.match(await page.locator('#tx-agent-progress').innerText(),/历史调用记录，任务已停止/);
    assert.doesNotMatch(await page.locator('#tx-agent-progress').innerText(),/生成输出/);
    await page.locator('#tx-agent-progress').getByText('原始调用记录',{exact:true}).click();
    const originalPhase=(stageInspection?.agent?.current_call || job.summary.agent.current_call).phase;
    assert.ok((await page.locator('#tx-agent-progress details details pre').innerText()).includes(`"phase": "${originalPhase}"`),'original active phase is retained as audit data');
  }
  if(stageInspection) {
    assert.match(await page.locator('#tx-inspection-summary').innerText(),/来源候选 1 条/);
    assert.match(await page.locator('#tx-inspection-summary').innerText(),/有效 Core 事实 0 条/);
    assert.match(await page.locator('#tx-integrity-banner').innerText(),/无 Core 事实记录可验证/);
    assert.match(await page.locator('#tx-result-table').innerText(),/来源候选（未批准）/);
    assert.equal(await page.locator('#tx-result-downloads').getByRole('link',{name:'下载成果 JSON'}).count(),0);
  }
  await page.locator('[data-result-perspective="coverage"]').click();
  await page.waitForSelector('[data-tx-metric]');
  if(stageInspection) {
    assert.match(await page.locator('#tx-result-table').innerText(),/未执行/);
    assert.match(await page.locator('#tx-result-table').innerText(),/已进入未完成/);
    assert.equal(await page.locator('[data-tx-metric]').count(),3);
    await page.screenshot({path:process.env.ESG_TEST_RESULTS_SCREENSHOT||'/tmp/esg-stage-frontend-results.png',fullPage:true});
  }
  const inspectionCount=requests.filter(request=>request.path.endsWith('/inspection')).length;
  await page.waitForTimeout(2800);
  assert.equal(requests.filter(request=>request.path.endsWith('/inspection')).length,inspectionCount,'unchanged revision must not reload inspection');
  await page.locator('[data-app-view-target="report-assets"]').first().click();
  await page.waitForTimeout(500);
  const hiddenCounts=requests.filter(request=>request.path==='/api/report-sessions'||request.path==='/api/jobs').length;
  await page.waitForTimeout(3500);
  assert.equal(requests.filter(request=>request.path==='/api/report-sessions'||request.path==='/api/jobs').length,hiddenCounts,'hidden pages must release subscriptions');
  await page.evaluate(async()=>{const {docRequest}=await import('./src/api.js');await Promise.all([docRequest('/api/test-dedup'),docRequest('/api/test-dedup')]);});
  assert.equal(requests.filter(request=>request.path==='/api/test-dedup').length,1);
  await page.evaluate(async()=>{
    const {renderAvailableActions}=await import('./src/actions.js');
    const root=document.createElement('div');document.body.append(root);
    await new Promise((resolve,reject)=>{
      renderAvailableActions(root,[{action:'remove-draft',label:'删除假草稿',method:'DELETE',endpoint:'/api/action-delete',payload:{revision:'r1'}}],{onComplete:resolve,onError:reject});
      root.querySelector('button').click();
    });
    root.remove();
  });
  const removeRequest=requests.find(request=>request.path==='/api/action-delete');
  assert.equal(removeRequest.method,'DELETE');
  assert.deepEqual(removeRequest.body,{revision:'r1'});
  await page.evaluate(async()=>{
    const {renderAvailableActions}=await import('./src/actions.js');
    const {setSecret}=await import('./src/roles.js');
    setSecret('qiniu_api_key','fixture-current-cloud-key');
    setSecret('ocr_token','fixture-unrelated-ocr-token');
    const root=document.createElement('div');document.body.append(root);
    for (const action of [
      {action:'adopt_evidence_transition',label:'采用云修复证据',method:'POST',endpoint:'/api/action-cloud-transition',calls_model:true,incurs_cost:true,payload:{new_ir_run_id:'ir-cloud',affected_object_ids:['cell-fake'],reason:'fixture repair',requested_by:'fixture-user'}},
      {action:'adopt_evidence_transition',label:'采用本地修复证据',method:'POST',endpoint:'/api/action-local-transition',calls_model:true,incurs_cost:false,payload:{new_ir_run_id:'ir-local'}},
      {action:'remove-draft',label:'删除假草稿',method:'DELETE',endpoint:'/api/action-delete-with-key',calls_model:false,incurs_cost:false,payload:{revision:'r2'}},
    ]) {
      await new Promise((resolve,reject)=>{
        renderAvailableActions(root,[action],{onComplete:resolve,onError:reject});
        root.querySelector('button').click();
      });
    }
    root.remove();
  });
  const cloudTransition=requests.find(request=>request.path==='/api/action-cloud-transition');
  assert.equal(cloudTransition.body.qiniu_api_key,'fixture-current-cloud-key');
  assert.equal(cloudTransition.body.new_ir_run_id,'ir-cloud');
  assert.equal('ocr_token' in cloudTransition.body,false);
  for (const target of ['/api/action-local-transition','/api/action-delete-with-key']) {
    const request=requests.find(request=>request.path===target);
    assert.equal('qiniu_api_key' in request.body,false);
    assert.equal('ocr_token' in request.body,false);
  }
  assert.equal(await page.evaluate(()=>Object.keys(localStorage).some(key=>/fixture-current-cloud-key|fixture-unrelated-ocr-token/.test(localStorage.getItem(key)))),false);
  failSessionList=true;
  await page.locator('[data-app-view-target="extract-tasks"]').first().click();
  await page.waitForFunction(()=>document.querySelector('#session-list').textContent.includes('报告任务读取失败'));
  assert.match(await page.locator('#session-list').innerText(),/报告任务读取失败/);
  failAssets=true;failIr=true;
  await page.locator('#session-reload').click();
  await page.waitForFunction(()=>document.querySelector('#session-message').textContent.includes('报告库读取失败'));
  assert.equal(await page.locator('#session-standards [data-standard-package]').count(),1,'independent read failures must preserve healthy packages');
  assert.match(await page.locator('#session-message').innerText(),/证据列表读取失败/);
  assert.match(await page.locator('#session-standards').innerText(),/结构校验通过不代表/);
  failAssets=false;failIr=false;
  invalidSelectedPackage=true;
  await page.locator('#session-reload').click();
  await page.waitForFunction(()=>document.querySelector('#session-standards').textContent.includes('保留 1 个暂不可用'));
  assert.match(await page.locator('#session-standard-count').innerText(),/已选 1 个条款/,'invalid package must not silently shrink chosen output');
  assert.equal(await page.locator('#session-preview-button').isDisabled(),true);
  invalidSelectedPackage=false;
  await page.locator('#session-reload').click();
  await page.waitForSelector('#session-standards input:checked');
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('esg:select-report',{detail:{assetId:'asset-fake',irRunId:'ir-fake'}})));
  await page.waitForFunction(()=>document.querySelector('#session-ir').value==='ir-fake' && !document.querySelector('#session-preview-button').disabled);
  await page.locator('#session-reload').click();
  await page.waitForFunction(()=>document.querySelector('#session-ir').value==='ir-fake' && !document.querySelector('#session-preview-button').disabled);
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('esg:select-report',{detail:{assetId:'asset-fake',irRunId:'ir-missing'}})));
  await page.waitForFunction(()=>document.querySelector('#session-message').textContent.includes('已固定证据版本 ir-missing'));
  assert.equal(await page.locator('#session-ir').inputValue(),'ir-missing','missing pinned evidence must never fall back to automatic preparation');
  assert.equal(await page.locator('#session-preview-button').isDisabled(),true);
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('esg:select-session',{detail:{sessionId:'plan-fake'}})));
  await page.waitForFunction(()=>document.querySelector('#session-detail').textContent.includes('示例报告.pdf'));
  incompatible=true;
  await page.locator('#session-reload').click();
  await page.waitForFunction(()=>document.querySelector('#session-message').textContent.includes('旧后端不支持标准包'));
  assert.equal(await page.locator('#session-preview-button').isDisabled(),true,'incompatible builds must not preview or create extraction');
  assert.equal(await page.locator('#session-create').isDisabled(),true);
  assert.equal(await page.locator('#session-standards [data-standard-package]').count(),1,'compatibility failure must not masquerade as an empty catalog');
  assert.equal(requests.filter(request=>request.path==='/api/pipeline/standards/esrs.2023-set1.e1-6/2.0.0').length,1,'expanded package details should be cached by digest');
  await page.evaluate(async()=>{
    const {disclosureGraph,topicBadge}=await import('./src/disclosure.js');
    const graph=disclosureGraph({format_version:'2.1',metrics:[{metric_id:'a',source_datapoint_id:'E1-5_01'}],relations:[{relation_type:'explains',source_metric_ids:['a'],target_metric_ids:[],description:'上下文'}]});
    if(!graph.includes('E1-5_01')) throw new Error('2.1 relation graph hidden');
    if(topicBadge({disclosure_topic:'test.topic.energy',disclosure_role:'anchor'}).includes('主指标')) throw new Error('anchor conflated with platform-major');
    const {progressView,taskErrorView}=await import('./src/progress.js');
    for(const status of ['completed','finished','partial','failed','interrupted','cancelled']) for(const phase of ['started','loading','prefill','generation']) {
      const record={status,progress:{label:'执行中断',determinate:false,total:null,current_call:{phase,tool:'repair_format',elapsed_seconds:238}}};
      const original=JSON.stringify(record), view=progressView(record);
      if(!view.includes('历史调用记录，任务已停止') || view.includes('生成输出')) throw new Error(`stopped ${status}/${phase} masquerades as active call`);
      if(view.includes('role="progressbar"')) throw new Error('unknown progress rendered as determinate');
      if(JSON.stringify(record)!==original) throw new Error('rendering changed producer data');
    }
    if(!progressView({status:'running',progress:{determinate:false,current_call:{phase:'generation'}}}).includes('生成输出')) throw new Error('active generation hidden');
    const unknown=taskErrorView({error:'json schema repair_format hard deadline'});
    if(!unknown.includes('执行遇到问题') || unknown.includes('输出格式未满足合同')) throw new Error('browser inferred error category from English');
    if(!unknown.includes('json schema repair_format hard deadline') || !unknown.includes('<details')) throw new Error('raw unknown error audit discarded');
    const typed=taskErrorView({error_detail:{category:'identity',message:'producer-English',technical_detail:'original'}});
    if(!typed.includes('主体、期间或口径身份不完整') || !typed.includes('producer-English')) throw new Error('typed producer category or raw message lost');
  });
  assert.deepEqual(errors,[]);
  console.log('PASS: PDF/IR creator, roles, results, lazy package loading, independent read failures, incompatible-service guard, 2.1 graph and core/major distinction.');
  await page.screenshot({path:process.env.ESG_TEST_SCREENSHOT||'/tmp/esg-frontend-contract.png',fullPage:true});
  await browser.close();server.close();
})().catch(error=>{console.error(error);server.close();process.exit(1);});
