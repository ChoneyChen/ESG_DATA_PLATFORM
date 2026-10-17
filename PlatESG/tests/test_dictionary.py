"""Product selection and frozen-source integrity at the extraction boundary."""
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from esg_standard_packages.contracts import MeasurementContract

PLATFORM = Path(__file__).resolve().parents[1]
STANDARDS = PLATFORM.parent / 'ExtractESG' / 'standard_packages'
module_spec = importlib.util.spec_from_file_location('platform_dictionary_builder', PLATFORM / 'scripts/build-dictionary.py')
builder = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(builder)


@pytest.fixture(scope='module')
def artifacts():
    return builder.build_dictionary()


def test_generated_artifacts_reproduce_committed_catalog(artifacts):
    dictionary, profile = artifacts
    assert dictionary == json.loads((PLATFORM / 'catalog/metric-dictionary.v1.json').read_text())
    assert profile == json.loads((PLATFORM / 'catalog/extraction-profile.e1-e3.json').read_text())


def test_all_major_ids_preserved_in_original_order_with_explicit_pending(artifacts):
    dictionary, profile = artifacts
    selection = json.loads((STANDARDS / 'profiles/esrs/2023-set1/platform-major.json').read_text())
    ids = [m['source_datapoint_id'] for m in dictionary['metrics']]
    assert ids == selection['source_datapoint_ids']
    assert len(ids) == len(set(ids)) == 49
    assert len(profile['ready_source_datapoint_ids']) == 16
    assert len(profile['pending_source_datapoint_ids']) == 33
    assert profile['overall_source_datapoint_ids'] == ids
    assert profile['source_datapoint_ids'] == ids[:16]
    assert profile['output_count'] == 16
    assert set(profile['excluded_by_explicit_filter']) == set(ids[16:])


def test_main_selection_does_not_activate_all_package_core(artifacts):
    dictionary, profile = artifacts
    registry = json.loads((STANDARDS / 'catalogs/esrs/2023-set1/registry.json').read_text())
    modules = {original: module for module in registry['modules'] for original in module['source_datapoint_ids']}
    for metric in dictionary['metrics']:
        assert metric['source_datapoint_id'] in modules[metric['source_datapoint_id']]['core_source_datapoint_ids']
    e1 = next(r for r in profile['routes'] if r['package_id'].endswith('.e1-5'))
    assert e1['source_datapoint_ids'] == ['E1-5_01', 'E1-5_05', 'E1-5_09']
    assert 'esrs.2023-set1.e1-5.dp02' not in e1['output_metric_ids']
    # The existing derived-ratio fallback may read internal operands, which
    # still cannot alter the selected outputs or their coverage denominator.
    assert set(e1['output_metric_ids']).isdisjoint(e1['internal_dependency_metric_ids'])
    assert len(e1['execution_metric_ids']) > len(e1['output_metric_ids'])


def test_published_bindings_pin_actual_runtime_and_planned_has_no_fake_version(artifacts):
    dictionary, _ = artifacts
    for metric in dictionary['metrics']:
        binding = metric['standard_binding']
        if metric['readiness'] == 'ready':
            package = json.loads((STANDARDS / 'dist' / binding['package_id'] / binding['package_version'] / 'package.json').read_text())
            assert binding['digest'] == package['compilation']['source_digest']
            source = next(m for m in package['metrics'] if m['metric_id'] == binding['metric_id'])
            assert source['source_datapoint_id'] == metric['source_datapoint_id']
            # The pinned raw release stays immutable; legacy omitted optional
            # fields receive the same typed defaults used by the dictionary builder.
            assert MeasurementContract.model_validate(source['measurement_contract']).model_dump(mode='json') == MeasurementContract.model_validate(metric['source_measurement_contract']).model_dump(mode='json')
            assert source['provenance']['row_sha256'] == metric['source_reference']['row_sha256']
        else:
            assert binding['package_id']
            assert binding['package_version'] is None
            assert binding['metric_id'] is None
            assert binding['digest'] is None
            assert metric['source_measurement_contract'] is None


def test_method_and_scope_are_explicit_constraints_not_implicit_defaults(artifacts):
    dictionary, _ = artifacts
    by_id = {m['source_datapoint_id']: m for m in dictionary['metrics']}
    for source, scope, method in [('E1-6_07', 'scope_1', None), ('E1-6_09', 'scope_2', 'location_based'), ('E1-6_10', 'scope_2', 'market_based'), ('E1-6_11', 'scope_3', None)]:
        metric = by_id[source]
        assert metric['dimension_constraints']['ghg_scope']['allowed_values'] == [scope]
        assert 'measurement_method' in metric['comparison_dimensions']
        assert 'measurement_method' not in metric['required_dimensions']
        if method:
            assert metric['required_context_fields'] == ['measurement_method']
            assert metric['typed_context_constraints']['measurement_method']['allowed_values'] == [method]
    assert by_id['E3-4_01']['dimension_constraints']['water_measure']['allowed_values'] == ['consumption']
    assert by_id['E3-4_11']['dimension_constraints']['water_measure']['allowed_values'] == ['withdrawal']
    assert by_id['E2-4_02']['dimension_constraints']['pollution_medium']['allowed_values'] == ['air']
    assert by_id['E2-4_03']['dimension_constraints']['pollution_medium']['allowed_values'] == ['water']


def test_canonical_units_and_non_numeric_polarity(artifacts):
    dictionary, _ = artifacts
    by_id = {m['source_datapoint_id']: m for m in dictionary['metrics']}
    for source in ['E1-5_01', 'E1-5_05']:
        assert by_id[source]['canonical_unit'] == 'MWh'
    for source in ['E1-6_07', 'E1-6_09', 'E1-6_10', 'E1-6_11']:
        assert by_id[source]['canonical_unit'] == 'tCO2e'
    for source in ['E3-4_01', 'E3-4_02', 'E3-4_03', 'E3-4_11', 'E3-4_12']:
        assert by_id[source]['canonical_unit'] == 'm3'
    assert by_id['E1-5_09']['canonical_unit'] == 'ratio'
    assert by_id['E1-5_09']['value_constraints'] == {'minimum': '0', 'maximum': '1'}
    assert by_id['E2-4_09']['value_type'] == 'statement'
    assert by_id['E2-4_09']['value_role'] == 'context'
    assert by_id['E2-4_09']['canonical_unit'] is None
    assert by_id['E2-4_09']['comparison_mode'] == 'structured_display'
    assert by_id['G1-1_03']['statement_polarity'] == 'negative_absence'
    assert by_id['S1-14_05']['canonical_unit'] == 'rate'
    assert by_id['G1-4_02']['canonical_unit'] is None


def test_raw_source_identity_and_legal_facets_preserved(artifacts):
    dictionary, _ = artifacts
    catalog = json.loads((STANDARDS / 'catalogs/esrs/2023-set1/ig3-original.json').read_text())
    rows = {r['id']: r for r in catalog['records'] if r['record_kind'] == '独立编号条目'}
    assert dictionary['source_sha256'] == catalog['source_sha256']
    for metric in dictionary['metrics']:
        row, source = rows[metric['source_datapoint_id']], metric['source_reference']
        assert metric['original_name'] == row['name']
        assert metric['original_data_type'] == row['data_type']
        assert source['source_sheet'] == row['source_sheet']
        assert source['source_row'] == row['source_row']
        assert source['may'] == row['may']
        assert source['conditional_or_alternative'] == row['conditional_or_alternative']
        assert source['row_sha256'] == hashlib.sha256(builder.canonical_json_bytes(row['raw_values'])).hexdigest()
    assert rows['E3-4_11']['may'] == 'V'
    assert rows['E3-4_12']['may'] == 'V'


def test_extraction_ui_distinguishes_major_and_package_core(tmp_path):
    """Real browser, fake HTTP only: never starts extraction or a model."""
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('ESG_TEST_NODE') or shutil.which('node') or str(bundled / 'bin/node')
    playwright = os.environ.get('ESG_TEST_PLAYWRIGHT') or str(bundled / 'node_modules/playwright')
    chrome = os.environ.get('ESG_TEST_CHROME') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    if not Path(node).is_file() or not Path(playwright).is_dir() or not Path(chrome).is_file():
        pytest.skip('A local Node/Playwright/Chromium runtime is required for the UI contract test')
    script = tmp_path / 'major_scope_ui.cjs'
    script.write_text(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.ESG_TEST_PLAYWRIGHT);
const root = process.env.ESG_FRONTEND_ROOT;
const standards = process.env.ESG_STANDARD_ROOT;
const profile = JSON.parse(fs.readFileSync(process.env.ESG_PROFILE_PATH, 'utf8'));
profile.selection_snapshot = {profile_id: profile.profile_id, profile_version: profile.profile_version, fixture: 'version-lock-test'};
const packages = new Map(profile.session_standards.map(s => [s.package_id, JSON.parse(fs.readFileSync(path.join(standards, 'dist', s.package_id, s.package_version, 'package.json'), 'utf8'))]));
const catalog = [...packages.values()].map(p => ({package_id: p.manifest.package_id, package_version: p.manifest.package_version, status: p.manifest.status, disclosure_requirement: p.manifest.standard.disclosure_requirement, format_version: p.format_version, source_digest: p.compilation.source_digest, core_source_datapoint_ids: p.manifest.core_source_datapoint_ids, metric_summaries: p.metrics}));
const requests = [], errors = [];
const server = http.createServer((req, res) => {
  const file = path.resolve(root, '.' + new URL(req.url, 'http://local').pathname);
  if (!file.startsWith(root + path.sep)) {res.writeHead(403); res.end(); return;}
  fs.readFile(file, (error, data) => {
    if (error) {res.writeHead(404);res.end();return;}
    res.writeHead(200, {'Content-Type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});res.end(data);
  });
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, executablePath: process.env.ESG_TEST_CHROME});
  try {
    const page = await browser.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (![18080,18180].includes(Number(url.port))) return route.continue();
      let payload = [];
      if (url.pathname === '/api/report-assets') payload = {assets: [{asset_id: 'scope-fixture', file_name: '重点配置测试.pdf', ocr_status: 'unprocessed'}]};
      else if (url.pathname === '/api/pipeline/standards/catalog') payload = {packages: catalog, diagnostics: {ready: true, errors: []}, planned: [], historical: []};
      else if (url.pathname === '/api/pipeline/compatibility') payload = {ready: true, blockers: []};
      else if (url.pathname === '/api/pipeline/standards' || url.pathname === '/api/standards') payload = catalog;
      else if (url.pathname.startsWith('/api/pipeline/standards/') || url.pathname.startsWith('/api/standards/esrs.')) payload = packages.get(decodeURIComponent(url.pathname.split('/').at(-2)));
      else if (url.pathname === '/api/pipeline/extraction-profiles/platform-major') {assert.equal(url.searchParams.get('topics'), 'E1,E2,E3');payload = profile;}
      else if (url.pathname === '/api/report-sessions/preview') {requests.push(req.postDataJSON());payload = {roles: {}, internal_dependencies: [], budget: {}, evidence_limitations: []};}
      else if (['/api/pipeline/status','/api/storage/inventory','/api/config','/api/models/status','/api/ocr/providers/status'].includes(url.pathname)) payload = {};
      await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(payload)});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/index.html`);
    await page.getByRole('button', {name: '提取此报告'}).click();
    await page.waitForFunction(() => document.querySelectorAll('#session-standards input:checked').length === 16);
    assert.match(await page.locator('#session-profile-hint').innerText(), /49.*16.*33/);
    await page.locator('#session-pending-profile summary').click();
    assert.equal(await page.locator('#session-pending-profile-ids').innerText(), profile.pending_source_datapoint_ids.join('、'));
    const energyCore = page.locator('label.session-metric').filter({hasText: 'E1-5_02'});
    assert.match(await energyCore.innerText(), /核心条款/);
    assert.doesNotMatch(await energyCore.innerText(), /主要条款/);
    assert.equal(await energyCore.locator('input').isChecked(), false);
    await page.getByRole('button', {name: '预览实际计划', exact: true}).click();
    await page.waitForFunction(() => !document.querySelector('#session-create').disabled);
    assert.deepEqual(requests.at(-1).selection_snapshot, profile.selection_snapshot);
    assert.deepEqual(requests.at(-1).standards, profile.session_standards);
    await page.locator('[data-standard-id="esrs.2023-set1.e1-5"][data-standard-choice="core"]').click();
    assert.equal(await page.locator('#session-standards input:checked').count(), 18);
    await page.getByRole('button', {name: '预览实际计划', exact: true}).click();
    await page.waitForFunction(() => !document.querySelector('#session-create').disabled);
    assert.equal('selection_snapshot' in requests.at(-1), false, 'manual package-core selection clears the product scope lock');
    assert.equal(requests.at(-1).standards[0].metric_ids.length, 5);
    await page.locator('#session-select-major').click();
    assert.equal(await page.locator('#session-standards input:checked').count(), 16);
    await page.locator('input[value="esrs.2023-set1.e1-5.dp05"]').uncheck();
    await page.getByRole('button', {name: '预览实际计划', exact: true}).click();
    await page.waitForFunction(() => !document.querySelector('#session-create').disabled);
    assert.equal('selection_snapshot' in requests.at(-1), false);
    assert.equal(requests.at(-1).standards.flatMap(s => s.metric_ids).length, 15);
    assert.deepEqual(errors, []);
    console.log('PASS: 49-ID major catalog, 16-output explicit scope, 33 pending, package-core separation, locked preview and manual invalidation.');
  } finally {await browser.close();server.close();}
})().catch(error => {console.error(error);server.close();process.exitCode = 1;});
''')
    env = {**os.environ, 'ESG_TEST_PLAYWRIGHT': playwright, 'ESG_TEST_CHROME': chrome,
           'ESG_FRONTEND_ROOT': str(PLATFORM.parent / 'ExtractESG/document_ir_extract/frontend'),
           'ESG_STANDARD_ROOT': str(STANDARDS),
           'ESG_PROFILE_PATH': str(PLATFORM / 'catalog/extraction-profile.e1-e3.json')}
    result = subprocess.run([node, str(script)], env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
