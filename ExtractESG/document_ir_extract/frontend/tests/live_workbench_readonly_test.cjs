/* Read-only acceptance against the actual coordinated local services.
 * Run only after the owner has safely restarted 18080/18180/18081.
 * Every browser request other than GET, HEAD or OPTIONS is blocked. This test
 * never submits a report, previews via POST, calls a model, or changes history.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.ESG_TEST_PLAYWRIGHT || '/Users/choney/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const doc = process.env.ESG_LIVE_DOC || 'http://127.0.0.1:18080';
const targeted = process.env.ESG_LIVE_TARGETED || 'http://127.0.0.1:18180';
const frontend = process.env.ESG_LIVE_FRONTEND || 'http://127.0.0.1:18081';
const output = process.env.ESG_LIVE_OUTPUT || '/tmp/esg-live-workbench';
const extractRoot = path.resolve(__dirname, '../../..');
const expectedMajor = JSON.parse(fs.readFileSync(path.join(extractRoot, 'standard_packages/profiles/esrs/2023-set1/platform-major.json'), 'utf8')).source_datapoint_ids;
const expectedReady = expectedMajor.filter(id => /^E[123](?:[-.])/.test(id));
const errors = [], consoleErrors = [], forbidden = [], requests = [], badResponses = [];
const detailPattern = /^\/api\/pipeline\/standards\/esrs\.[^/]+\/[^/]+$/;

async function get(base, resource) {
  const response = await fetch(base + resource, {signal: AbortSignal.timeout(20000)});
  assert.equal(response.status, 200, `actual GET ${base}${resource}`);
  return response.json();
}

function assertRelease(payload, service) {
  assert.equal(payload.service, service);
  assert.equal(payload.protocol_version, 'workbench-compatibility-v1');
  assert.match(payload.build_fingerprint, /^[a-f0-9]{64}$/);
  assert.equal(payload.current_disk_fingerprint, payload.build_fingerprint, `${service} must run the current disk build`);
  assert.equal(payload.restart_required, false);
  assert.equal(payload.source_changed_since_start, false);
  assert.equal(payload.readiness.ready, true, `${service}: ${(payload.readiness.blockers || []).join('; ')}`);
  assert.deepEqual(payload.readiness.blockers || [], []);
  assert.ok(payload.contracts.standard_package_formats.includes('2.1'));
  assert.ok(payload.contracts.core_schema_versions.includes('1.2.0'));
  assert.equal(payload.contracts.standard_validation, 'structural_only');
}

(async () => {
  const [aggregate, docRelease, targetRelease, frontRelease, catalog, profile, assets, processing] = await Promise.all([
    get(doc, '/api/pipeline/compatibility'), get(doc, '/health/compatibility'),
    get(targeted, '/api/compatibility'), get(frontend, '/api/compatibility'),
    get(doc, '/api/pipeline/standards/catalog'),
    get(doc, '/api/pipeline/extraction-profiles/platform-major?topics=E1,E2,E3'),
    get(doc, '/api/report-assets'), get(doc, '/api/report-processing/runs'),
  ]);
  assertRelease(docRelease, 'document-backend');
  assertRelease(targetRelease, 'targeted-backend');
  assertRelease(frontRelease, 'workbench-frontend');
  assert.equal(aggregate.ready, true, `aggregate: ${aggregate.blockers.join('; ')}`);
  assert.deepEqual(aggregate.blockers, []);
  assert.equal(new Set([docRelease, targetRelease, frontRelease].map(x => x.build_fingerprint)).size, 1, 'three actual services must run the same release');
  assert.equal(aggregate.document.build_fingerprint, docRelease.build_fingerprint);
  assert.equal(aggregate.targeted.build_fingerprint, targetRelease.build_fingerprint);
  assert.equal(catalog.packages.length, 41);
  assert.equal(catalog.diagnostics.valid_package_count, 41);
  assert.equal(catalog.diagnostics.invalid_package_count, 0);
  assert.equal(catalog.diagnostics.validation_scope, 'structural_only');
  assert.equal(targetRelease.readiness.business_approval, 'not_assessed');
  assert.ok(catalog.packages.every(pkg => pkg.status === 'draft' && pkg.validation_state === 'structurally_valid'));
  assert.ok(catalog.packages.every(pkg => pkg.real_extraction_validation === 'not_assessed' && pkg.business_approval === 'not_assessed'));
  assert.equal(catalog.planned.length, 58);
  assert.equal(catalog.historical.length, 54);
  assert.equal(expectedMajor.length, 49);
  assert.deepEqual(profile.overall_source_datapoint_ids, expectedMajor, 'retain all 49 user-selected original IDs in order');
  assert.deepEqual(profile.source_datapoint_ids, expectedReady);
  assert.deepEqual(profile.ready_source_datapoint_ids, expectedReady);
  assert.equal(profile.output_count, 16);
  assert.equal(profile.pending_source_datapoint_ids.length, 33);
  assert.equal(processing.schema_version, 'report-processing-v1');
  assert.ok(Array.isArray(processing.runs));
  assert.ok(Array.isArray(assets.assets));
  const chosenPackages = new Set(profile.session_standards.map(x => x.package_id));
  const originalByMetric = new Map(catalog.packages.flatMap(pkg => pkg.metric_summaries.map(metric => [metric.metric_id, metric.source_datapoint_id])));
  assert.equal(chosenPackages.size, 5);
  for (const selection of profile.session_standards) {
    const pkg = catalog.packages.find(x => x.package_id === selection.package_id);
    assert.equal(pkg.package_version, selection.package_version);
    for (const id of selection.metric_ids) assert.ok(pkg.core_source_datapoint_ids.includes(originalByMetric.get(id)), `${id} must be both platform-major and package-core`);
  }
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.ESG_TEST_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  let page;
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 1080}});
    page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') consoleErrors.push(message.text());});
    page.on('response', response => {if (response.status() >= 400) badResponses.push({url: response.url(), status: response.status()});});
    await page.route('**/*', async route => {
      const request = route.request();
      requests.push({url: request.url(), method: request.method()});
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
        forbidden.push({url: request.url(), method: request.method()});
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    await page.goto(`${frontend}/index.html?readonly_acceptance=${Date.now()}`, {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => document.querySelector('#health')?.textContent === '服务与标准已就绪', null, {timeout: 20000});
    assert.equal(await page.locator('#health').getAttribute('class').then(x => x.includes('online')), true);
    await page.locator('[data-app-view-target="report-processing"]').first().click();
    await page.waitForFunction(() => document.body.dataset.activeView === 'report-processing');
    await page.waitForSelector('#report-processing-page:not(.view-hidden)');
    await page.waitForFunction(count => document.querySelector('#processing-asset')?.options.length === count + 1, assets.assets.length);
    assert.equal(await page.locator('#processing-form').isVisible(), true);
    assert.deepEqual(await page.locator('#processing-mode option').evaluateAll(nodes => nodes.map(node => node.value)), ['auto', 'rebuild_ir', 'reprocess']);
    assert.equal(await page.locator('#processing-list [data-processing-id]').count(), processing.runs.length);
    await page.screenshot({path: path.join(output, 'report-processing.png'), fullPage: true});
    await page.locator('[data-app-view-target="extract-tasks"]').first().click();
    await page.waitForFunction(() => document.body.dataset.activeView === 'extract-tasks');
    await page.waitForFunction(() => document.querySelectorAll('#session-standards [data-standard-package]').length === 41);
    await page.waitForFunction(() => document.querySelectorAll('#session-standards input[type="checkbox"]:checked').length === 16);
    assert.match(await page.locator('#session-profile-hint').innerText(), /全部 49 个原编号/);
    assert.match(await page.locator('#session-profile-hint').innerText(), /16 个主要条款/);
    assert.match(await page.locator('#session-profile-hint').innerText(), /其余 33 个/);
    const selectedIds = await page.locator('#session-standards input[type="checkbox"]:checked').evaluateAll(nodes => nodes.map(node => node.value));
    assert.deepEqual(selectedIds.map(id => originalByMetric.get(id)), expectedReady);
    const renderedPackages = await page.locator('#session-standards [data-standard-package]').evaluateAll(nodes => nodes.map(node => node.dataset.standardPackage));
    assert.deepEqual(renderedPackages, catalog.packages.map(pkg => pkg.package_id));
    assert.equal(await page.locator('#session-pending-profile-ids').textContent(), profile.pending_source_datapoint_ids.join('、'));
    const selectedPkg = page.locator('[data-standard-package="esrs.2023-set1.e1-6"]');
    await selectedPkg.locator('[data-standard-detail]').getByText(/条款关系/).waitFor();
    const graphSummary = selectedPkg.locator('[data-standard-detail] details > summary');
    await graphSummary.click();
    assert.match(await selectedPkg.locator('[data-standard-detail]').innerText(), /格式 2\.1 · Core 1\.2\.0/);
    assert.match(await selectedPkg.locator('[data-standard-detail]').innerText(), /E1-6_07/);
    assert.match(await selectedPkg.locator('[data-standard-detail]').innerText(), /E1-6_09/);
    assert.match(await selectedPkg.locator('[data-standard-detail]').innerText(), /E1-6_10/);
    await page.waitForFunction(() => [...document.querySelectorAll('#session-standards [data-standard-package][open] [data-standard-detail]')].every(node => node.textContent.includes('格式 2.1')));
    const initialDetails = new Set(requests.filter(x => detailPattern.test(new URL(x.url).pathname)).map(x => new URL(x.url).pathname));
    assert.equal(initialDetails.size, chosenPackages.size, 'only five selected packages may load details initially, never all 41');
    for (const resource of initialDetails) assert.ok(chosenPackages.has(decodeURIComponent(resource.split('/')[4])));
    const extra = page.locator('[data-standard-package="esrs.2023-set1.e1-3"]');
    assert.equal(await extra.evaluate(node => node.open), false);
    await extra.locator(':scope > summary').click();
    await extra.locator('[data-standard-detail]').getByText(/条款关系/).waitFor();
    const expandedDetails = new Set(requests.filter(x => detailPattern.test(new URL(x.url).pathname)).map(x => new URL(x.url).pathname));
    assert.equal(expandedDetails.size, chosenPackages.size + 1, 'expanding an unselected package loads exactly its own detail');
    assert.equal(await page.locator('#session-standards input[type="checkbox"]:checked').count(), 16, 'opening package detail must not broaden selected outputs');
    assert.ok(await page.locator('#session-standards .session-metric').getByText('核心条款', {exact: true}).count() > 16);
    assert.equal(await page.locator('#session-standards .session-metric').getByText('主要条款', {exact: true}).count(), 16);
    await page.locator('#session-profile-hint').scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(output, 'standards-overview.png')});
    await selectedPkg.scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(output, 'standards-selector.png')});
    const [queue, jobs, reportSessions] = await Promise.all([
      get(doc, '/api/pipeline/tasks?limit=300'), get(targeted, '/api/jobs?limit=500'),
      get(targeted, '/api/report-sessions'),
    ]);
    assert.ok(Array.isArray(queue) && Array.isArray(jobs) && Array.isArray(reportSessions));
    await page.locator('#task-dock-toggle').click();
    const expectedQueueRows = queue.filter(x => ['running', 'queued'].includes(x.status)).length + Math.min(20, queue.filter(x => !['running', 'queued'].includes(x.status)).length);
    await page.waitForFunction(count => document.querySelectorAll('#pipeline-task-list article[data-task-id]').length === count, expectedQueueRows);
    assert.equal(await page.locator('#dock-tab-queue').isVisible(), true);
    const historyQueue = queue.find(x => !['queued', 'running'].includes(x.status));
    if (historyQueue) {
      await page.locator(`#pipeline-task-list article[data-task-id="${historyQueue.task_id}"] strong`).first().click();
      await page.waitForFunction(() => document.querySelector('#dock-tab-events')?.classList.contains('active'));
      await page.waitForFunction(() => {
        const text = document.querySelector('#pipeline-task-events')?.textContent.trim();
        return text && text !== '选择任务后显示日志。';
      });
      assert.equal(await page.locator('#pipeline-task-events .empty-state.error').count(), 0, 'historical queue logs must remain readable');
      await page.screenshot({path: path.join(output, 'queue-history.png')});
    }
    await page.locator('#task-dock-toggle').click();
    await page.locator('[data-app-view-target="results"]').first().click();
    await page.waitForFunction(() => document.body.dataset.activeView === 'results');
    await page.waitForFunction(count => document.querySelectorAll('#tx-job-list [data-tx-job]').length === count, jobs.length);
    await page.waitForFunction(count => document.querySelectorAll('#session-result-list [data-session-open]').length === count, reportSessions.length);
    assert.equal(await page.locator('#tx-job-list .empty-state.error').count(), 0);
    const historicalJob = jobs.find(x => x.job_id === (process.env.ESG_LIVE_HISTORY_JOB || 'tx-20261002T182823Z-f5b3169b0c'));
    assert.ok(historicalJob, 'retain the original China Petroleum regression job in the visible history');
    if (historicalJob) {
      const originalDetail = await get(targeted, `/api/jobs/${historicalJob.job_id}`);
      assert.equal(originalDetail.error_detail.category, 'runtime');
      assert.equal(originalDetail.error_detail.code, 'execution_deadline');
      assert.equal(originalDetail.error_detail.message, '执行达到时限，检查点与阶段成果保留');
      await page.locator(`#tx-job-list [data-tx-job="${historicalJob.job_id}"]`).click();
      await page.waitForFunction(() => document.querySelector('#tx-job-title')?.textContent !== '选择一个任务');
      assert.equal(await page.locator('#tx-job-summary.empty-state').count(), 0, 'historical result must load an actual detail view');
      assert.equal(await page.locator('#tx-job-summary .task-error-message').innerText(), originalDetail.error_detail.message);
      const [summaryBox, errorBox] = await Promise.all([page.locator('#tx-job-summary').boundingBox(), page.locator('#tx-job-summary .task-error-summary').boundingBox()]);
      assert.ok(errorBox.width >= summaryBox.width * 0.9, 'the new error summary must span the whole summary grid, not a narrow metric cell');
      assert.equal(await page.locator('#tx-job-summary .task-error-technical').evaluate(node => node.open), false);
      assert.equal(await page.locator('#tx-job-summary .task-error-technical pre').isVisible(), false, 'English original error must not be the primary explanation');
      assert.match(await page.locator(`#tx-job-list [data-tx-job="${historicalJob.job_id}"] .business-progress`).innerText(), /历史调用记录，任务已停止/);
      assert.doesNotMatch(await page.locator(`#tx-job-list [data-tx-job="${historicalJob.job_id}"] .business-progress`).innerText(), /生成输出/);
      assert.match(await page.locator('#tx-job-summary').innerText(), /任务记录跨度/);
      assert.match(await page.locator('#tx-job-summary').innerText(), /含排队；执行与未观测时间分解未记录/);
      assert.match(await page.locator('#session-result-list .task-error-message').innerText(), /[\u4e00-\u9fff]/);
      assert.equal(await page.locator('#session-result-list .task-error-technical pre').isVisible(), false);
      await page.locator('#tx-job-title').scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(output, 'extraction-history.png')});
      await page.locator('#tx-job-summary .task-error-technical > summary').click();
      assert.ok((await page.locator('#tx-job-summary .task-error-technical pre').innerText()).includes(originalDetail.error_detail.technical_detail), 'original English error survives in its expandable technical record');
      await page.locator('#tx-agent-progress > details > summary').click();
      assert.match(await page.locator('#tx-agent-progress').innerText(), /历史调用记录，任务已停止|调用已中断/);
      assert.doesNotMatch(await page.locator('#tx-agent-progress').innerText(), /生成输出/);
      await page.locator('#tx-job-summary .task-error-technical').scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(output, 'extraction-technical-detail.png')});
    }
    await page.waitForLoadState('networkidle', {timeout: 20000});
    assert.deepEqual(forbidden, [], 'no mutation may be attempted');
    assert.deepEqual(errors, [], 'actual page must have no uncaught JavaScript errors');
    assert.deepEqual(badResponses, [], 'actual read APIs/assets must not return errors');
    assert.deepEqual(consoleErrors, [], 'actual browser must have no console errors');
    const evidence = {status: 'passed', scope: 'live_read_only_no_models_no_mutations', build_fingerprint: docRelease.build_fingerprint,
      active_packages: catalog.packages.length, planned_packages: catalog.planned.length, historical_versions: catalog.historical.length,
      major_original_ids: profile.overall_source_datapoint_ids, selected_original_ids: expectedReady, pending_major_count: 33,
      initially_loaded_detail_count: initialDetails.size, after_one_expansion_detail_count: expandedDetails.size,
      report_asset_count: assets.assets.length, processing_run_count: processing.runs.length,
      queue_total_count: queue.length, queue_rendered_count: expectedQueueRows, historical_extraction_jobs: jobs.length,
      historical_report_sessions: reportSessions.length, opened_history_job: historicalJob?.job_id || null,
      historical_error_classification: 'runtime/execution_deadline', primary_error_language: 'Chinese', original_errors_expandable: true,
      stopped_historical_call_explicit: true, legacy_duration_basis: 'creation_to_last_update_including_queue_not_gpu_or_single_call',
      browser_request_count: requests.length, attempted_mutations: forbidden.length, page_errors: errors,
      console_errors: consoleErrors, bad_http_responses: badResponses, screenshots: ['report-processing.png', 'standards-overview.png', 'standards-selector.png', ...(historyQueue ? ['queue-history.png'] : []), 'extraction-history.png', 'extraction-technical-detail.png'].map(x => path.join(output, x))};
    fs.writeFileSync(path.join(output, 'acceptance.json'), JSON.stringify(evidence, null, 2) + '\n');
    console.log(JSON.stringify(evidence, null, 2));
  } catch (error) {
    if (page) await page.screenshot({path: path.join(output, 'failure.png')}).catch(() => {});
    fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({status: 'failed', message: error.message,
      attempted_mutations: forbidden, page_errors: errors, console_errors: consoleErrors,
      bad_http_responses: badResponses, read_requests: requests}, null, 2) + '\n');
    throw error;
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
