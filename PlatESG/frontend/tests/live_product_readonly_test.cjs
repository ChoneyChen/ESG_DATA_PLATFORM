const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require('playwright');

// This acceptance test performs public reads only. POST /v1/comparisons is a read query.
const base = process.env.PLATESG_TEST_URL || 'http://127.0.0.1:8765';
const output = process.env.PLATESG_TEST_OUTPUT || '/tmp/esg-p5-product-qa';
let activeBrowser;
const forbidden = /候选\s*(?:ID|编号)|任务\s*(?:ID|编号)|审核人|规则哈希|规则摘要|当前审核状态|编译草稿|source_artifact_ref|source_node_id|reviewer_ref|rule_digest/i;

(async()=>{
  await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.ESG_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  activeBrowser=browser;
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  context.setDefaultTimeout(15000);
  const errors=[],failed=[],writes=[],responses=[];
  await context.route('**/*',route=>{
    const request=route.request(),url=new URL(request.url()),method=request.method();
    if(!['GET','HEAD','OPTIONS'].includes(method) && !(method==='POST' && url.pathname==='/api/v1/comparisons')){writes.push({method,path:url.pathname});return route.abort();}
    return route.continue();
  });
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
  page.on('requestfailed',request=>{if(request.failure()?.errorText!=='net::ERR_ABORTED')failed.push(request.url());});
  page.on('response',response=>{if(new URL(response.url()).pathname.startsWith('/api/'))responses.push({path:new URL(response.url()).pathname,status:response.status()});});
  const checkPublic=async()=>{assert.equal(forbidden.test(await page.locator('body').innerText()),false,'Internal operations leaked into a public product page');};
  const screenshot=async(name,fullPage=true)=>{await page.waitForLoadState('networkidle');return page.screenshot({path:path.join(output,name+'.png'),fullPage});};
  await page.goto(base,{waitUntil:'networkidle'});
  await page.getByRole('heading',{name:/看清数据。\s*理解企业。/}).waitFor();
  const dataset=new URL(page.url()).searchParams.get('dataset_version');
  assert.ok(dataset,'Resolved release must be fixed in the URL');
  assert.equal(await page.locator('.sidebar').count(),0);
  assert.equal(await page.getByRole('navigation',{name:'主要导航'}).getByRole('link').count(),6);
  await checkPublic();await screenshot('home-desktop');

  await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:'企业',exact:true}).click();
  await page.getByRole('heading',{name:'企业目录',exact:true}).waitFor();
  const cards=page.locator('main .entity-card');assert.ok(await cards.count()>0,'Live fixture needs at least one published enterprise');
  await cards.first().click();await page.getByRole('heading',{name:'已发布数据',exact:true}).waitFor();
  await page.locator('main .data-table tbody tr').first().waitFor();
  await page.waitForLoadState('networkidle');
  const canvasCount=await page.locator('canvas').count();
  await screenshot('company-desktop');
  await page.getByRole('button',{name:/查看.*原文证据/}).first().click();
  await page.getByRole('dialog').waitFor();await page.getByRole('dialog').getByRole('heading',{name:'报告与原文',exact:true}).waitFor();
  await checkPublic();assert.equal(new URL(page.url()).searchParams.get('dataset_version'),dataset);
  await screenshot('source-detail',false);await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({state:'detached'});
  assert.equal(await page.getByRole('dialog').count(),0);

  await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:'指标',exact:true}).click();
  await page.getByRole('heading',{name:'指标浏览',exact:true}).waitFor();
  const originalIds=await page.locator('.metric-card .metric-id').allTextContents();
  assert.equal(originalIds.length,49);assert.equal(new Set(originalIds).size,49);
  assert.ok(originalIds.includes('E1-5_01')&&originalIds.includes('G1-6_01'));
  await page.getByLabel('搜索主要指标').fill('E1-5_01');
  await page.waitForURL(url=>url.searchParams.get('q')==='E1-5_01');
  assert.equal(new URL(page.url()).searchParams.get('q'),'E1-5_01');
  await page.reload({waitUntil:'networkidle'});assert.equal(await page.locator('.metric-card').count(),1);
  await page.locator('.metric-card').first().click();
  await page.getByRole('heading',{name:'年度变化与已发布数据'}).waitFor();await page.locator('main .trend-card canvas').first().waitFor();await checkPublic();await screenshot('metric-desktop');
  await page.getByRole('button',{name:'数据表',exact:true}).click();
  await page.waitForURL(url=>url.searchParams.get('display')==='table');
  assert.equal(new URL(page.url()).searchParams.get('display'),'table');
  await page.locator('main .data-table tbody tr').first().waitFor();
  const exportHref=await page.getByRole('link',{name:'下载该指标数据'}).getAttribute('href');assert.equal(new URL(exportHref,base).searchParams.get('dataset_version'),dataset);

  await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:'同行比较',exact:true}).click();
  await page.getByRole('heading',{name:'在一致口径下比较',exact:true}).waitFor();
  await page.getByRole('checkbox').nth(0).check();
  if(await page.getByRole('checkbox').count()>1)await page.getByRole('checkbox').nth(1).check();
  await page.getByRole('button',{name:'生成比较表',exact:true}).click();
  await page.getByRole('heading',{name:'比较结果',exact:true}).waitFor();
  if(await page.locator('.cohort-option').count()>0){await page.locator('.cohort-option').first().click();await page.getByRole('button',{name:'生成比较表',exact:true}).click();await page.getByRole('heading',{name:'比较结果',exact:true}).waitFor();}
  assert.ok(JSON.parse(new URL(page.url()).searchParams.get('companies')).length>0);
  if(await page.locator('.comparison-result .chart-unit').count()>0)await page.locator('.comparison-result canvas').first().waitFor();
  await checkPublic();await screenshot('comparison-desktop');
  await page.reload({waitUntil:'networkidle'});await page.getByRole('heading',{name:'比较结果',exact:true}).waitFor();

  await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:'来源',exact:true}).click();
  await page.getByRole('heading',{name:'报告来源',exact:true}).waitFor();
  await page.locator('.source-card').first().waitFor();await checkPublic();await screenshot('sources-desktop');
  await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:'数据与方法',exact:true}).click();
  await page.getByRole('heading',{name:'数据与方法',exact:true}).waitFor();
  assert.ok((await page.getByRole('link',{name:'下载当前版本 CSV'}).getAttribute('href')).includes('dataset_version='+encodeURIComponent(dataset)));
  await checkPublic();await screenshot('methods-desktop');

  await page.setViewportSize({width:390,height:844});await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:'首页',exact:true}).click();
  await page.getByRole('heading',{name:/看清数据。\s*理解企业。/}).waitFor();
  await page.locator('.featured-chart canvas').first().waitFor();
  await page.waitForLoadState('networkidle');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'Public mobile page overflows viewport');
  await screenshot('home-mobile');
  assert.equal(writes.length,0);assert.equal(errors.length,0,JSON.stringify(errors));assert.equal(failed.length,0,JSON.stringify(failed));
  assert.equal(responses.filter(response=>response.status>=400).length,0,JSON.stringify(responses.filter(response=>response.status>=400)));
  const result={dataset_version:dataset,original_clause_count:originalIds.length,desktop_trend_canvas_count:canvasCount,api_response_count:responses.length,errors,writes,failed,screenshots:output};
  await fs.writeFile(path.join(output,'acceptance.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));await browser.close();
})().catch(async error=>{console.error(error);await activeBrowser?.close();process.exitCode=1;});
