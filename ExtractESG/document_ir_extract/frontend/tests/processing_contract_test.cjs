/* Real browser, isolated fake transport. No workflow/model service is called. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.ESG_TEST_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname, '..');
const requests = [], errors = [];
const asset = {asset_id:'pdf-fixture',file_name:'示例报告.pdf',sha256:'0'.repeat(64),ocr_runs:[],ocr_status:'unprocessed',processing_status:'unprocessed',size_bytes:100};
let ready = true, created = false;
const state = {processing_id:'processing-fixture',asset_id:asset.asset_id,source_sha256:asset.sha256,file_name:asset.file_name,report_id:'report-fixture',revision:1,status:'queued',evidence_status:'not_ready',steps:{},reuse_diagnostics:[],stages:[{stage:'ocr',label:'文档识别',status:'queued',progress_current:0,progress_total:0},{stage:'ir',label:'结构与证据构建',status:'pending'},{stage:'quality',label:'证据质量检查',status:'pending'}],available_actions:[{action:'cancel',label:'取消处理',endpoint:'/api/report-processing/runs/processing-fixture/cancel',payload:{expected_revision:1}}],report:{report_id:'report-fixture',revision:1,metadata:{title:null,publisher_name:null,reporting_year:null},asset_versions:[{version:1,asset_id:asset.asset_id,source_sha256:asset.sha256}]}};
const server = http.createServer((request,response)=>{
  const target = path.resolve(root,'.'+decodeURIComponent(request.url.split('?')[0]));
  if (!target.startsWith(root+path.sep)) {response.writeHead(403);response.end();return;}
  fs.readFile(target,(error,data)=>{if(error){response.writeHead(404);response.end();return;}response.writeHead(200,{'Content-Type':target.endsWith('.js')?'text/javascript':target.endsWith('.css')?'text/css':'text/html'});response.end(data);});
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,executablePath:process.env.ESG_TEST_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const request=route.request(), url=new URL(request.url());
    if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/health')) return route.continue();
    const body=request.postDataJSON();requests.push({path:url.pathname,method:request.method(),body});
    let payload = {};
    if(url.pathname==='/health/compatibility') payload={protocol_version:'workbench-compatibility-v1',restart_required:!ready,readiness:{ready,blockers:ready?[]:['需协调重启服务']}};
    else if(url.pathname==='/health') payload={status:'ok',capabilities:{report_sessions_v1:true,report_processing_v1:true}};
    else if(url.pathname==='/api/pipeline/compatibility') payload={ready,blockers:ready?[]:['版本不同']};
    else if(url.pathname==='/api/report-assets') payload={assets:[asset],asset_count:1};
    else if(url.pathname.endsWith('/file')) return route.fulfill({status:200,contentType:'application/pdf',body:'%PDF-1.4\n%%EOF'});
    else if(url.pathname==='/api/report-processing/runs') {if(request.method()==='POST'){created=true;payload=state;}else payload={runs:created?[state]:[]};}
    else if(url.pathname.endsWith('/pages/0')) payload={page_index:0,layout_objects:[{label:'正文',text:'示例公司披露的能源消费',layout_object_id:'block-fixture'}],tables:[{table_id:'table-fixture',cells:[{row_index:0,col_index:0,text:'能源消费'},{row_index:0,col_index:1,text:'100 MWh'}]}]};
    else if(url.pathname.endsWith('/pages')) payload=[{page_index:0,page_number:1}];
    else if(url.pathname.endsWith('/cancel')){state.status='cancelled';state.revision++;state.available_actions=[{action:'resume',label:'恢复未完成阶段',endpoint:'/api/report-processing/runs/processing-fixture/resume',payload:{expected_revision:state.revision}}];payload=state;}
    else if(url.pathname==='/api/reports/report-fixture' && request.method()==='PATCH'){state.report.metadata=body.metadata;state.report.revision++;payload=state.report;}
    else if(url.pathname==='/api/report-sessions'||url.pathname==='/api/pipeline/tasks'||url.pathname==='/api/document-ir/jobs'||url.pathname==='/api/jobs') payload=[];
    else if(url.pathname==='/api/pipeline/standards/catalog') payload={packages:[],diagnostics:{discovered_package_count:0,valid_package_count:0,invalid_package_count:0},planned:[],historical:[]};
    else if(url.pathname==='/api/pipeline/standards') payload=[];
    else if(url.pathname==='/api/health') payload={status:'ok',catalog:{standard_package_count:0}};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
  await page.locator('[data-app-view-target="report-processing"]').click();
  await page.waitForSelector('#processing-asset option[value="pdf-fixture"]',{state:'attached'});
  await page.locator('#processing-asset').selectOption('pdf-fixture');
  await page.locator('#processing-submit').click();
  await page.waitForSelector('[data-processing-id="processing-fixture"]');
  const create=requests.find(r=>r.path==='/api/report-processing/runs'&&r.method==='POST').body;
  assert.equal(create.asset_id,'pdf-fixture');assert.equal(create.source_sha256,asset.sha256);
  assert.equal(create.mode,'auto');assert.equal(create.config.ocr_provider,'local_paddleocr');
  assert.equal(create.config.allow_cloud_ocr,false);assert.equal('standards' in create,false);
  assert.equal(typeof create.submission_key,'string');
  await page.locator('[data-processing-id="processing-fixture"]').click();
  assert.match(await page.locator('#processing-detail').innerText(),/文档识别/);
  assert.match(await page.locator('#processing-detail').innerText(),/结构与证据构建/);
  state.status='ready_with_limits';state.evidence_status='limited';state.ir_run_id='ir-fixture';state.ocr_run_id='ocr-fixture';state.revision++;
  state.stages[0]={...state.stages[0],status:'completed',progress_current:2,progress_total:2};
  state.stages[1].status='completed';state.stages[2].status='completed';
  state.evidence_policy={mode:'limited',excluded_target_ids:['table-fixture']};
  state.available_actions=[{action:'inspect_evidence',label:'查看文档与证据'},{action:'start_extraction',label:'选择条款开始提取',asset_id:asset.asset_id,ir_run_id:state.ir_run_id}];
  await page.locator('#processing-refresh').click();
  await page.waitForFunction(()=>document.querySelector('#processing-detail').innerText.includes('局部证据可用'));
  await page.waitForFunction(()=>document.querySelector('#processing-evidence').innerText.includes('100 MWh'));
  assert.match(await page.locator('#processing-detail').innerText(),/已识别 2 \/ 2 页/);
  await page.evaluate(()=>{window.__selectedReport=null;window.addEventListener('esg:select-report',e=>window.__selectedReport=e.detail);});
  await page.getByRole('button',{name:'选择条款开始提取',exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>window.__selectedReport),{assetId:'pdf-fixture',irRunId:'ir-fixture'});
  // A stale service remains readable but cannot accept another processing run.
  await page.locator('[data-app-view-target="report-processing"]').click();
  ready=false;
  const before=requests.filter(r=>r.path==='/api/report-processing/runs'&&r.method==='POST').length;
  await page.locator('#processing-submit').click();
  await page.waitForFunction(()=>document.querySelector('#processing-message').innerText.includes('版本未就绪'));
  assert.equal(requests.filter(r=>r.path==='/api/report-processing/runs'&&r.method==='POST').length,before);
  assert.equal(requests.some(r=>r.method==='POST'&&r.path.includes('/tasks/ocr')),false);
  assert.equal(requests.some(r=>r.method==='POST'&&r.path.includes('/tasks/document-ir')),false);
  assert.deepEqual(errors,[]);
  await browser.close();server.close();
  console.log('Processing browser contract passed: standalone submission, real stages, limited evidence, PDF/context and compatibility guard.');
})().catch(error=>{console.error(error);server.close();process.exit(1);});
