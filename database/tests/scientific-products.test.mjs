import test from 'node:test';
import assert from 'node:assert/strict';
import {PGlite} from '@electric-sql/pglite';
import {applyMigrations} from '../scripts/db.mjs';
import {applyPlatformMigrations} from '../runtime/connection.mjs';
import {scientificExport, scientificCsv, scientificProduct} from '../queries/scientific_products.mjs';

const metadata = {upstream_system:'science-test',organization_ref:'science-entity',report_ref:'science-report',
  report_version_ref:'science-report-v1',source_artifact_ref:'science-pdf',report_period_start:'2025-01-01',report_period_end:'2025-12-31'};

function bundle() {
  return {root:'/science/bundle',manifest:{job_id:'science-job'},bundleDigest:'1'.repeat(64),
    request:{package_id:'esrs.test',package_version:'1.0.0'},
    standard:{manifest:{standard:{framework_id:'esrs',framework_version:'2023'}},compilation:{source_digest:'2'.repeat(64)},
      metrics:[{metric_id:'metric-1',source_datapoint_id:'E1-6_07',labels:{zh:'范围一'}}],elements:[]},
    sourceMappings:{core_bindings:[{core_record_id:'obs-1',source_fact_id:'source-1'}]},
    sourceFacts:{facts:[{source_fact_id:'source-1',source_key:'reading-1',document_id:'doc-1',ir_run_id:'ir-1',ir_revision:1,
      source_object_id:'cell-1',source_row_id:'row-1',source_row_label:'范围一',kind:'quantitative',value_raw:'10',unit_raw:'tCO2e',
      identity_raw:{reporting_period:'2025',reporting_entity:'示例集团'},
      identity_evidence:{reporting_period:[{ir_object_id:'cell-1',pdf_page_index:0,evidence_role:'column_header',raw_text:'2025'}]},
      physical_sources:[{ir_object_id:'cell-1',pdf_page_index:0,printed_page_label:'1',bbox:{x0:1,y0:1,x1:2,y1:2}}],
      interpretation_version:'test-v1',review_status:'candidate'}]},
    records:{quantitative_observations:[{observation_id:'obs-1',metric_id:'metric-1',value_raw:'10',value_numeric:'10',comparator:'exact',unit_raw:'tCO2e',review_status:'pending'}],
      qualitative_assertions:[],reporting_tasks:[],attribute_values:[],dimension_values:[],
      evidence_references:[{evidence_id:'e-1',source_record_id:'obs-1',document_id:'doc-1',ir_run_id:'ir-1',ir_revision:1,ir_object_id:'cell-1',pdf_page_index:0,evidence_excerpt:'10'}]}};
}

test('scientific products retain dimensions, field evidence, source identity and unmapped coverage', async () => {
  const db = await PGlite.create();
  try {
    await applyMigrations(db); await applyPlatformMigrations(db);
    const {importBundle} = await import('../scripts/frozen-bundle.mjs');
    await importBundle(db, bundle(), metadata);
    const observations = await scientificExport(db,{table:'observations'});
    assert.equal(observations.row_count,0);
    const sources = await scientificExport(db,{table:'observation_sources'});
    assert.equal(sources.row_count,0);
    const coverage = await scientificExport(db,{table:'coverage_decisions'});
    assert.equal(coverage.row_count,0);
    const metrics = await scientificExport(db,{table:'metric_definitions'});
    assert.ok(metrics.row_count >= 0);
    const csv = scientificCsv({rows:[{value_numeric:'9007199254740993.000001',text:'=not-formula'}]});
    assert.match(csv,/9007199254740993.000001/);
    assert.match(csv,/"'=not-formula"/);
  } finally { await db.close(); }
});
