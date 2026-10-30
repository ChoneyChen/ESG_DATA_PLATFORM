/** Frozen v1 bundle reader and isolated consumer-fixture loader.
 * New production ingress is retired. No extraction module imports this file.
 */
import { createHash, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import {extensionContractDigest} from './extension-derivation.mjs';
import {archiveReportSourceFacts} from './report-source-facts.mjs';
import {normalizeBoundary} from './review-evidence.mjs';

export const IMPORTER_VERSION = 'result-candidate-import-v1';
const digest = value => createHash('sha256').update(value).digest('hex');
const stableJSON = value => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v)
  ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b))) : v);
const uuid = value => {
  const h = digest(value);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-5${h.slice(13, 16)}-a${h.slice(17, 20)}-${h.slice(20, 32)}`;
};
const layouts = {
  reporting_tasks: ['reporting-tasks.jsonl', 'task_id'],
  quantitative_observations: ['quantitative-observations.jsonl', 'observation_id'],
  qualitative_assertions: ['qualitative-assertions.jsonl', 'assertion_id'],
  attribute_values: ['attribute-values.jsonl', 'attribute_id'],
  dimension_values: ['dimension-values.jsonl', 'dimension_value_id'],
  evidence_references: ['evidence-references.jsonl', 'evidence_id'],
};
async function json(file) { return JSON.parse(await readFile(file, 'utf8')); }
async function optionalJSON(file) {
  try { return await json(file); } catch (e) { if (e.code === 'ENOENT') return null; throw e; }
}
export async function loadBundle(bundlePath) {
  const root = path.resolve(bundlePath);
  const manifest = await json(path.join(root, 'manifest.json'));
  const revision = await optionalJSON(path.join(root, 'artifact-revision.json'));
  if(revision) {
    if(revision.schema_version!=='terminal-result-revision-v1' || revision.job_id!==manifest.job_id
      || revision.revision_id!==manifest.artifact_revision?.revision_id)
      throw new Error('Terminal artifact revision identity mismatch');
    const basis=await readFile(path.join(root,'input/materialization-basis.json'));
    if(digest(basis)!==revision.materialization_digest
      ||revision.revision_id!=='partial-'+revision.materialization_digest.slice(0,32)
      ||manifest.artifact_revision.materialization_digest!==revision.materialization_digest)
      throw new Error('Terminal artifact content-addressed identity mismatch');
    for(const [relative,expected] of Object.entries(revision.artifact_sha256 || {})) {
      const target=path.resolve(root,relative);
      if(!target.startsWith(root+path.sep)||! /^[0-9a-f]{64}$/.test(expected)
        || digest(await readFile(target))!==expected)throw new Error('Terminal artifact revision content changed');
    }
    for(const [relative,expected] of Object.entries(revision.frozen_input_sha256 || {})) {
      if(!['input/request.json','input/standard-package.json','input/ir-manifest.json',
        'input/runtime-standard-snapshot.json','input/evidence-transition.json'].includes(relative)
        ||digest(await readFile(path.join(root,relative)))!==expected)
        throw new Error('Terminal artifact revision frozen input changed');
    }
  }
  if (manifest.bundle_layout_version !== 'targeted-result-bundle-v1' || manifest.schema_version !== 'targeted-extraction-result-v1')
    throw new Error('Unsupported Result Bundle contract');
  if (manifest.contract_validation?.status !== 'passed') throw new Error('Result Bundle has no passed contract validation');
  const request = await json(path.join(root, 'input/request.json'));
  const standard = await json(path.join(root, 'input/standard-package.json'));
  if (standard.manifest.package_id !== request.package_id || standard.manifest.package_version !== request.package_version)
    throw new Error('Bundle package snapshot does not match its request');
  const records = {}, bytes = [stableJSON(request), stableJSON(standard)];
  for (const [key, [file, primary]] of Object.entries(layouts)) {
    const raw = await readFile(path.join(root, 'results', file), 'utf8');
    bytes.push(raw);
    records[key] = raw.split(/\r?\n/).filter(x => x.trim()).map(x => JSON.parse(x));
    const ids = new Set();
    for (const row of records[key]) {
      if (!row[primary] || ids.has(row[primary])) throw new Error(`Invalid or duplicate ${primary}`);
      ids.add(row[primary]);
    }
  }
  const sourceMappings = await optionalJSON(path.join(root, 'results/metric-source-mappings.json'));
  const sourceFacts = await optionalJSON(path.join(root, 'results/source-facts.json'));
  const organization = await optionalJSON(path.join(root, 'results/fact-organization.json'));
  const evidenceTransition = await optionalJSON(path.join(root, 'input/evidence-transition.json'));
  const derivations = await optionalJSON(path.join(root, 'derivations.json'));
  const internalDependencies = await optionalJSON(path.join(root, 'results/internal-dependencies.json'));
  const runtimeSnapshot=await optionalJSON(path.join(root,'input/runtime-standard-snapshot.json'));
  const selectedRecords=structuredClone(records);
  let executionMetrics=null,extensionComputations=null;
  if(runtimeSnapshot) {
    const output=await json(path.join(root,'exports/extraction-result.json'));
    executionMetrics=output.execution_metric_catalog;
    extensionComputations=output.extension_computations;
    if(runtimeSnapshot.schema_version!=='runtime-standard-snapshot-1.0'
        || runtimeSnapshot.parent_package_id!==standard.manifest.package_id
        || runtimeSnapshot.parent_package_version!==standard.manifest.package_version
        || runtimeSnapshot.parent_source_digest!==standard.compilation.source_digest
        || runtimeSnapshot.runtime_digest!==extensionContractDigest(Object.fromEntries(Object.entries(runtimeSnapshot).filter(([key])=>key!=='runtime_digest')))
        || stableJSON(runtimeSnapshot)!==stableJSON(output.runtime_standard_snapshot)
        || stableJSON(runtimeSnapshot.selections)!==stableJSON(request.extension_selections)
        || !Array.isArray(executionMetrics)||!Array.isArray(extensionComputations))
      throw new Error('Result Bundle runtime extension snapshot is invalid');
    const baseMetrics=new Map(standard.metrics.map(metric=>[metric.metric_id,metric]));
    const extensionReleases=new Map(runtimeSnapshot.extension_releases.map(release=>[release.definition.extension_id,release]));
    if(executionMetrics.length!==baseMetrics.size+extensionReleases.size || new Set(executionMetrics.map(metric=>metric.metric_id)).size!==executionMetrics.length)
      throw new Error('Runtime execution catalog contains missing or duplicate clauses');
    for(const metric of executionMetrics) {
      const base=baseMetrics.get(metric.metric_id);
      if(base) {if(stableJSON(base)!==stableJSON(metric))throw new Error('Original metric changed inside execution catalog');continue;}
      const release=extensionReleases.get(metric.metric_id);
      if(!release || metric.clause_kind!=='project_extension' || metric.source_datapoint_id!==null
          || metric.extension_id!==release.definition.extension_id || metric.extension_release_digest!==release.release_digest
          || metric.extension_definition_version!==release.definition.definition_version
          || stableJSON(metric.labels)!==stableJSON(release.definition.labels))
        throw new Error('Runtime execution extension identity differs from its approved definition');
    }
    bytes.push(stableJSON(runtimeSnapshot),stableJSON(executionMetrics),stableJSON(extensionComputations));
  } else if(request.extension_selections?.length)throw new Error('Selected extensions require a frozen runtime snapshot');
  const internalScope=internalDependencies?.schema_version==='internal-dependency-facts-v2'?internalDependencies:null;
  if(internalScope) {
    const exported=await json(path.join(root,'exports/extraction-result.json'));
    if(stableJSON(exported.internal_dependency_scope)!==stableJSON(internalScope)
      || internalScope.scope_role!=='internal_dependency' || internalScope.output_policy!=='excluded_from_selected_output_and_coverage'
      || internalScope.parent_package_id!==request.package_id || internalScope.parent_package_version!==request.package_version
      || internalScope.parent_source_digest!==standard.compilation.source_digest
      || internalScope.runtime_standard_digest!==(runtimeSnapshot?.runtime_digest || standard.compilation.source_digest)
      || internalScope.contract_validation?.status!=='passed' || !Array.isArray(internalScope.metric_ids)
      || new Set(internalScope.metric_ids).size!==internalScope.metric_ids.length)
      throw new Error('Internal operand scope is not a validated frozen artifact');
    const metrics=new Map(standard.metrics.map(metric=>[metric.metric_id,metric]));
    if(stableJSON([...internalScope.selected_output_metric_ids].sort())!==stableJSON([...request.metric_ids].sort()))
      throw new Error('Internal operand scope names different selected output clauses');
    const extensionOperands=new Set();
    const requiredBy=new Map();
    for(const selection of runtimeSnapshot?.selections || []) {
      const release=runtimeSnapshot.extension_releases.find(release=>release.definition.extension_id===selection.extension_id);
      for(const ruleId of selection.derivation_rule_ids) {
        const rule=release?.definition.derivation_rules.find(rule=>rule.rule_id===ruleId);
        if(!rule)throw new Error('Internal operand selection names an unregistered frozen calculation rule');
        for(const operand of rule.operands) {
          extensionOperands.add(operand.source_metric_id);
          const extensions=requiredBy.get(operand.source_metric_id) || new Set();extensions.add(selection.extension_id);requiredBy.set(operand.source_metric_id,extensions);
        }
      }
    }
    const expected=new Set([...(request.internal_metric_ids || []),...extensionOperands]);
    const pending=[...request.metric_ids,...extensionOperands],visited=new Set();
    while(pending.length) {
      const id=pending.pop();if(visited.has(id))continue;visited.add(id);
      const metric=metrics.get(id);if(metric?.derivation_policy!=='reported_then_derived_when_dependencies_complete')continue;
      const dependencies=[...(metric.depends_on || []),...(standard.derivations || []).filter(rule=>rule.target_metric_id===id&&rule.execution_stage==='data_processing').flatMap(rule=>rule.operands.map(operand=>operand.source_metric_id))];
      for(const dependency of dependencies){if(!metrics.has(dependency))throw new Error('Internal dependency graph names an unknown original metric');expected.add(dependency);pending.push(dependency);}
    }
    request.metric_ids.forEach(id=>expected.delete(id));
    if(stableJSON([...expected].sort())!==stableJSON([...internalScope.metric_ids].sort()))
      throw new Error('Internal operand scope expands beyond its explicit calculation dependencies');
    if(stableJSON(internalScope.metric_ids)!==stableJSON(internalScope.metrics.map(metric=>metric.metric_id))
      || stableJSON(internalScope.source_datapoint_ids)!==stableJSON(internalScope.metrics.map(metric=>metric.source_datapoint_id)))
      throw new Error('Internal operand identity lists disagree');
    for(const metric of internalScope.metrics)if(!metrics.has(metric.metric_id) || metric.clause_kind!=='original_datapoint'
      || metrics.get(metric.metric_id).source_datapoint_id!==metric.source_datapoint_id || request.metric_ids.includes(metric.metric_id))
      throw new Error('Internal operands require independent original identities outside selected output');
    for(const metric of internalScope.metrics)if(stableJSON([...requiredBy.get(metric.metric_id) || []].sort())!==stableJSON([...metric.required_by_extension_ids].sort()))
      throw new Error('Internal operand relationship differs from frozen extension rules');
    const allowed=new Set(internalScope.metric_ids);
    if(stableJSON(internalScope.evidence_references)!==stableJSON(internalScope.records.evidence_references))
      throw new Error('Internal operand evidence differs from its Core records');
    for(const [kind,[,primary]] of Object.entries(layouts)) {
      const extra=internalScope.records[kind];
      if(!Array.isArray(extra))throw new Error('Internal operand Core collection is absent');
      const ids=new Set(records[kind].map(row=>row[primary]));
      for(const row of extra) {
        if(!row[primary] || ids.has(row[primary]) || (kind!=='evidence_references'&&!allowed.has(row.metric_id)))
          throw new Error('Internal operand Core has a duplicate or out-of-scope identity');
        ids.add(row[primary]);records[kind].push(row);
      }
    }
  }
  const parents = new Set([...records.reporting_tasks.map(r => r.task_id), ...records.quantitative_observations.map(r => r.observation_id), ...records.qualitative_assertions.map(r => r.assertion_id)]);
  for (const row of [...records.attribute_values, ...records.dimension_values, ...records.evidence_references]) {
    if (!parents.has(row.parent_record_id || row.source_record_id)) throw new Error('Bundle contains an orphan parent reference');
  }
  for (const source of records.evidence_references) {
    if (source.document_id !== manifest.document?.document_id) throw new Error('Bundle provenance does not match its report');
    if (source.quote_sha256 && source.quote_sha256 !== digest((source.evidence_excerpt || '').replace(/\s+/gu, ' ').trim()))
      throw new Error('Bundle evidence quote checksum is invalid');
  }
  bytes.push(stableJSON(sourceMappings), stableJSON(sourceFacts), stableJSON(organization), stableJSON(evidenceTransition), stableJSON(derivations), stableJSON(internalDependencies));
  return { root, manifest, request, standard, records,selectedRecords,internalScope,sourceMappings, sourceFacts, organization, evidenceTransition, derivations, internalDependencies,
    runtimeSnapshot,executionMetrics,extensionComputations,
    bundleDigest: digest(bytes.join('\n')) };
}

function decimal(value) {
  if (value == null || value === '') return null;
  const text = String(value);
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) throw new Error(`Invalid numeric representation: ${text}`);
  return text;
}

// Only an explicit, materialized SourceFact can identify one report fact across
// standard projections. Historical bundles retain their job/record identity.
function sourceContracts(bundle) {
  const records = new Map([
    ...bundle.records.quantitative_observations.map(row => [row.observation_id, { row, kind: 'quantitative' }]),
    ...bundle.records.qualitative_assertions.map(row => [row.assertion_id, { row, kind: 'assertion' }]),
  ]);
  const facts = new Map(), bindings = new Map();
  const hasSourceFacts = bundle.sourceFacts != null || !!bundle.internalScope?.source_facts?.length;
  if (bundle.sourceFacts!=null && !Array.isArray(bundle.sourceFacts.facts)) throw new Error('Invalid SourceFact collection');
  const neutralFacts = new Map();
  for (const fact of bundle.sourceFacts?.reported_facts || []) {
    if (!fact.source_fact_id) throw new Error('Neutral source fact has no source_fact_id');
    const { review_status, ...immutable } = fact;
    const previous = neutralFacts.get(fact.source_fact_id);
    if (previous && stableJSON(previous) !== stableJSON(immutable)) throw new Error(`Neutral source fact collision: ${fact.source_fact_id}`);
    neutralFacts.set(fact.source_fact_id, immutable);
  }
  for (const fact of [...(bundle.sourceFacts?.facts || []),...(bundle.internalScope?.source_facts || [])]) {
    if (!fact.source_fact_id) throw new Error('SourceFact has no source_fact_id');
    // Review state is a separate mutable axis; it is not report identity.
    const { review_status, ...immutable } = fact;
    const prior = facts.get(fact.source_fact_id);
    if (prior && stableJSON(prior) !== stableJSON(immutable)) throw new Error(`Source fact collision: ${fact.source_fact_id}`);
    facts.set(fact.source_fact_id, immutable);
  }
  for (const binding of [...(bundle.sourceMappings?.core_bindings || []),...(bundle.internalScope?.core_bindings || [])]) {
    if (!binding.core_record_id || !binding.source_fact_id || !records.has(binding.core_record_id))
      throw new Error('Source binding references an unknown Core record');
    if (bindings.has(binding.core_record_id)) throw new Error(`Duplicate source binding: ${binding.core_record_id}`);
    const { row, kind } = records.get(binding.core_record_id);
    if (binding.metric_id && binding.metric_id !== row.metric_id)
      throw new Error(`Source binding metric disagrees with ${binding.core_record_id}`);
    for (const key of ['package_id', 'package_version']) {
      if (binding[key] && binding[key] !== bundle.request[key]) throw new Error(`Source binding ${key} disagrees with the bundle`);
    }
    if (binding.package_digest && binding.package_digest !== bundle.standard.compilation.source_digest)
      throw new Error('Source binding package digest disagrees with its snapshot');
    if(binding.runtime_standard_digest && binding.runtime_standard_digest!==bundle.runtimeSnapshot?.runtime_digest)
      throw new Error('Source binding runtime standard digest disagrees with its frozen overlay');
    if (hasSourceFacts) {
      const fact = facts.get(binding.source_fact_id);
      if (!fact) throw new Error(`Source binding references unknown SourceFact: ${binding.source_fact_id}`);
      if (fact.kind !== kind || !fact.document_id || !fact.ir_run_id || !Number.isInteger(fact.ir_revision))
        throw new Error(`Invalid source identity: ${binding.source_fact_id}`);
      if (!Array.isArray(fact.physical_sources) || !fact.physical_sources.length || fact.physical_sources.some(source =>
        !source.ir_object_id || !Number.isInteger(source.pdf_page_index) || source.pdf_page_index < 0))
        throw new Error(`SourceFact ${binding.source_fact_id} lacks valid physical provenance`);
      if ((kind === 'quantitative' && fact.value_raw == null) || (kind === 'assertion' && !fact.statement_raw?.trim()))
        throw new Error(`SourceFact ${binding.source_fact_id} lacks its reported value or statement`);
    }
    bindings.set(binding.core_record_id, binding);
  }
  return { facts, bindings, hasSourceFacts, neutralFacts };
}

export async function importBundle(db, bundle, metadata, { inTransaction = false } = {}) {
  for (const key of ['upstream_system', 'organization_ref', 'report_ref', 'report_version_ref', 'source_artifact_ref']) {
    if (typeof metadata[key] !== 'string' || !metadata[key].trim()) throw new Error(`Explicit report metadata required: ${key}`);
  }
  if (metadata.source_sha256 != null && !/^[0-9a-f]{64}$/i.test(metadata.source_sha256))
    throw new Error('Invalid report source_sha256');
  const sourceSha256 = metadata.source_sha256?.toLowerCase() || null;
  const reportId = uuid(`${metadata.upstream_system}:${metadata.report_version_ref}`);
  const jobId = bundle.manifest.job_id || path.basename(bundle.root);
  const batchId = uuid(`${reportId}:${jobId}:${bundle.bundleDigest}`);
  const metrics = new Map((bundle.executionMetrics || bundle.standard.metrics).map(m => [m.metric_id, m]));
  const elements = new Map(bundle.standard.elements.map(e => [e.element_id, e]));
  const evidence = bundle.records.evidence_references;
  const dimensions = bundle.records.dimension_values;
  const attributes = bundle.records.attribute_values;
  const { facts: sourceFacts, bindings, hasSourceFacts, neutralFacts } = sourceContracts(bundle);
  // Platform adapters may freeze their selection snapshot in the same outer
  // transaction. The default CLI behavior still owns one complete transaction.
  const execute = inTransaction ? callback => callback(db) : callback => db.transaction(callback);
  return execute(async tx => {
    for(const release of bundle.runtimeSnapshot?.extension_releases || []) {
      const approved=(await tx.query('SELECT release FROM esg.extension_releases WHERE extension_id=$1 AND definition_version=$2',
        [release.definition.extension_id,release.definition.definition_version])).rows[0];
      if(!approved || stableJSON(approved.release)!==stableJSON(release))throw new Error('Bundle extension release is not authoritatively registered');
    }
    // CLI and platform imports share source protection. A standalone fact DB
    // without the platform lifecycle migration retains its existing contract.
    const lifecycle=(await tx.query("SELECT to_regclass('plat.artifact_lifecycle_mutex') AS relation")).rows[0]?.relation;
    if(lifecycle) {
      await tx.query('SELECT mutex_id FROM plat.artifact_lifecycle_mutex WHERE mutex_id=1 FOR UPDATE');
      const irIds=[...new Set([bundle.request.ir_run_id,...evidence.map(row=>row.ir_run_id),...[...sourceFacts.values()].map(fact=>fact.ir_run_id)].filter(Boolean))];
      const protectedSources=(await tx.query(`SELECT reservation_id FROM plat.artifact_lifecycle_reservations
        WHERE state <> 'released' AND ((scope->'job_ids') ? $1 OR (scope->'ir_run_ids') ?| $2::text[]
          OR ($3::text IS NOT NULL AND (scope->'source_sha256s') ? $3)) LIMIT 1`,[jobId,irIds,sourceSha256])).rows;
      if(protectedSources.length) throw Object.assign(new Error('来源正在回收或已清除；恢复后才能导入候选'),{code:'lifecycle_conflict'});
    }
    const knownReport = await tx.query('SELECT organization_ref,report_ref,source_artifact_ref,source_sha256 FROM esg.source_report_refs WHERE source_report_id=$1', [reportId]);
    if (knownReport.rows.length && ['organization_ref', 'report_ref', 'source_artifact_ref'].some(k => knownReport.rows[0][k] !== metadata[k]))
      throw new Error('Report identity collision; metadata disagrees with the imported report version');
    if (sourceSha256 && knownReport.rows[0]?.source_sha256 && knownReport.rows[0].source_sha256.toLowerCase() !== sourceSha256)
      throw new Error('Report identity collision; source_sha256 disagrees with the imported report version');
    if (sourceSha256 && knownReport.rows.length && !knownReport.rows[0].source_sha256)
      await tx.query('UPDATE esg.source_report_refs SET source_sha256=$1 WHERE source_report_id=$2 AND source_sha256 IS NULL', [sourceSha256, reportId]);
    const prior = await tx.query('SELECT import_batch_id FROM esg.import_batches WHERE import_batch_id=$1', [batchId]);
    if (prior.rows.length) return { import_batch_id: batchId, imported: 0, source_fact_snapshots: sourceFacts.size,
      idempotent: true, quality_status: 'candidate', publication_status: 'not_published' };
    await tx.query(`INSERT INTO esg.source_report_refs(source_report_id,upstream_system,organization_ref,report_ref,report_version_ref,
      source_artifact_ref,source_sha256,report_title_snapshot,metadata) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING`,
      [reportId, metadata.upstream_system, metadata.organization_ref, metadata.report_ref, metadata.report_version_ref,
       metadata.source_artifact_ref, sourceSha256, metadata.report_title || null,
       { language_code: metadata.language_code || 'und', report_period_start: metadata.report_period_start || null,
         report_period_end: metadata.report_period_end || null, publication_date: metadata.publication_date || null }]);
    await tx.query(`INSERT INTO esg.import_batches(import_batch_id,source_report_id,bundle_job_id,bundle_digest,package_id,package_version,
      package_digest,bundle_ref,importer_version) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
      [batchId, reportId, jobId, bundle.bundleDigest, bundle.request.package_id, bundle.request.package_version,
       bundle.standard.compilation.source_digest, bundle.root, IMPORTER_VERSION]);
    await archiveReportSourceFacts(tx,{bundle,source_report_id:reportId,import_batch_id:batchId,facts:sourceFacts,bindings});
    await archiveReportSourceFacts(tx,{bundle,source_report_id:reportId,import_batch_id:batchId,facts:neutralFacts,bindings:new Map(),neutral:true});
    if(bundle.runtimeSnapshot) {
      await tx.query('INSERT INTO esg.extension_import_snapshots(import_batch_id,runtime_digest,runtime_snapshot) VALUES($1,$2,$3)',
        [batchId,bundle.runtimeSnapshot.runtime_digest,bundle.runtimeSnapshot]);
      for(const selection of bundle.runtimeSnapshot.selections) {
        await tx.query(`INSERT INTO esg.extension_import_releases(import_batch_id,extension_id,definition_version,release_digest,include_reported,derivation_rule_ids)
          VALUES($1,$2,$3,$4,$5,$6)`,[batchId,selection.extension_id,selection.definition_version,selection.release_digest,
            selection.value_origins.includes('reported'),selection.derivation_rule_ids]);
      }
    }
    const framework = bundle.standard.manifest.standard?.framework_id || bundle.standard.manifest.framework_id || 'ESRS';
    const version = bundle.standard.manifest.standard?.framework_version || bundle.standard.manifest.framework_version || '2023-set1';
    const frameworkCode = typeof framework === 'string' ? framework.toUpperCase() : framework.framework_id || 'ESRS';
    await tx.query(`INSERT INTO esg.standard_frameworks(framework_id,framework_code,framework_name,status) VALUES($1,$2,$2,'proposed')
      ON CONFLICT(framework_code) DO NOTHING`, [uuid(frameworkCode), frameworkCode]);
    const frameworkId = (await tx.query('SELECT framework_id FROM esg.standard_frameworks WHERE framework_code=$1', [frameworkCode])).rows[0].framework_id;
    const frameworkVersionId = uuid(`${frameworkCode}:${version}:${bundle.request.package_id}:${bundle.request.package_version}`);
    await tx.query(`INSERT INTO esg.framework_versions(framework_version_id,framework_id,version_label,status) VALUES($1,$2,$3,'draft') ON CONFLICT DO NOTHING`,
      [frameworkVersionId, frameworkId, `${version}:${bundle.request.package_id}@${bundle.request.package_version}`]);
    let imported = 0;
    for (const [kind, rows] of [['quantitative', bundle.records.quantitative_observations], ['qualitative', bundle.records.qualitative_assertions]]) {
      for (const row of rows) {
        const recordId = row.observation_id || row.assertion_id;
        const metric = metrics.get(row.metric_id);
        if (!metric) throw new Error(`Unknown fact metric ${row.metric_id}`);
        const sources = evidence.filter(e => e.source_record_id === recordId);
        if (!sources.length || sources.some(e => !Number.isInteger(e.pdf_page_index) || e.pdf_page_index < 0 || !e.ir_object_id))
          throw new Error(`Fact ${recordId} lacks valid physical provenance`);
        if (new Set(sources.map(e => e.document_id)).size !== 1) throw new Error(`Fact ${recordId} mixes reports`);
        const versions = new Set(sources.map(e => `${e.ir_run_id}:${e.ir_revision}`));
        const allowedRuns = new Set([bundle.evidenceTransition?.from_ir_run_id, bundle.evidenceTransition?.to_ir_run_id].filter(Boolean));
        if (versions.size !== 1 && (!allowedRuns.size || sources.some(e => !allowedRuns.has(e.ir_run_id))))
          throw new Error(`Fact ${recordId} mixes undocumented evidence versions`);
        const binding = bindings.get(recordId);
        const sourceFact = hasSourceFacts && binding ? sourceFacts.get(binding.source_fact_id) : null;
        if (sourceFact && sources.some(source => source.document_id !== sourceFact.document_id))
          throw new Error(`SourceFact ${sourceFact.source_fact_id} belongs to another report`);
        if (sourceFact && !sources.some(source => source.ir_run_id === sourceFact.ir_run_id && source.ir_revision === sourceFact.ir_revision))
          throw new Error(`SourceFact ${sourceFact.source_fact_id} disagrees with the Core evidence revision`);
        const sourceFactRef = sourceFact?.source_fact_id || `${sources[0].ir_run_id}:${jobId}:${recordId}`;
        let disclosureId = uuid(`${reportId}:${sourceFactRef}`);
        const sourcePayload = sourceFact ? { origin: 'source_fact', source_fact: sourceFact } : { origin: 'legacy_projection', kind, value_raw: row.value_raw ?? row.statement_raw, unit_raw: row.unit_raw ?? null,
          comparator: row.comparator ?? null, value_numeric: row.value_numeric ?? null, value_lower: row.value_lower ?? null,
          value_upper: row.value_upper ?? null, period_start: row.period_start ?? null, period_end: row.period_end ?? null,
          reporting_period_raw: row.reporting_period_raw ?? null, reporting_boundary: row.reporting_boundary ?? null };
        // Re-reading a report changes the extraction fact id, but the physical
        // cell, clause, printed value and period are the same fact. Reuse the
        // existing disclosure instead of delivering a duplicate candidate.
        const printedValue = sourceFact ? sourceFact.value_raw ?? sourceFact.statement_raw ?? '' : row.value_raw ?? row.statement_raw ?? '';
        const printedPeriodStart = sourceFact ? sourceFact.identity_raw?.period_start || metadata.report_period_start || null
          : row.period_start || metadata.report_period_start || null;
        const printedPeriodEnd = sourceFact ? sourceFact.identity_raw?.period_end || metadata.report_period_end || null
          : row.period_end || metadata.report_period_end || null;
        const primarySource = sources[0];
        const duplicate = sourceFact && primarySource ? await tx.query(`SELECT d.disclosure_id
          FROM esg.esg_disclosures d
          JOIN esg.disclosure_standard_mappings m USING(disclosure_id)
          JOIN esg.standard_requirements s USING(requirement_id)
          JOIN esg.disclosure_sources ds USING(disclosure_id)
          WHERE d.source_report_id=$1 AND s.requirement_code=$2 AND d.raw_disclosure_text=$3
            AND ds.page_number=$4 AND ds.source_node_id=$5
            AND d.period_start IS NOT DISTINCT FROM $6::date AND d.period_end IS NOT DISTINCT FROM $7::date
            AND COALESCE(d.source_identity->>'reporting_boundary','')=COALESCE($8,'')
          LIMIT 1`, [reportId, metric.source_datapoint_id || row.metric_id, String(printedValue),
          Number(primarySource.pdf_page_index) + 1, primarySource.ir_object_id,
          printedPeriodStart, printedPeriodEnd,
          sourceFact.identity_raw?.reporting_boundary || row.reporting_boundary || null]) : { rows: [] };
        const reused = duplicate.rows[0]?.disclosure_id || null;
        if (reused) disclosureId = reused;
        const existing = await tx.query('SELECT source_payload,source_identity FROM esg.esg_disclosures WHERE disclosure_id=$1', [disclosureId]);
        if (existing.rows.length && (!reused || reused === disclosureId)
            && stableJSON(existing.rows[0].source_payload) !== stableJSON(sourcePayload)) {
          const previous = existing.rows[0].source_payload?.source_fact || existing.rows[0].source_identity;
          const previousIdentity = previous.identity_raw || {};
          const nextIdentity = sourceFact.identity_raw || {};
          const sameReportedIdentity = Object.keys({...previousIdentity, ...nextIdentity}).every(key =>
            key.startsWith('component_') || stableJSON(previousIdentity[key]) === stableJSON(nextIdentity[key]));
          const samePrintedFact = previous && sourceFact && ['value_raw', 'statement_raw', 'unit_raw', 'source_row_label',
            'document_id'].every(key => stableJSON(previous[key]) === stableJSON(sourceFact[key]))
            && sameReportedIdentity
            && stableJSON(previous.physical_sources?.map(source => ({
              ir_object_id: source.ir_object_id, pdf_page_index: source.pdf_page_index, bbox: source.bbox,
            }))) === stableJSON(sourceFact.physical_sources?.map(source => ({
              ir_object_id: source.ir_object_id, pdf_page_index: source.pdf_page_index, bbox: source.bbox,
            })));
          if (sourceFact && samePrintedFact) {
            await archiveReportSourceFacts(tx, {bundle, source_report_id: reportId, import_batch_id: batchId,
              facts: new Map([[sourceFactRef, sourceFact]]), bindings});
          } else {
            const differing = ['value_raw', 'statement_raw', 'unit_raw', 'source_row_label', 'document_id', 'ir_run_id', 'ir_revision']
              .filter(key => stableJSON(previous?.[key]) !== stableJSON(sourceFact?.[key]));
            if (stableJSON(previous?.identity_raw) !== stableJSON(sourceFact?.identity_raw)) differing.push('identity_raw');
            if (stableJSON(previous?.physical_sources) !== stableJSON(sourceFact?.physical_sources)) differing.push('physical_sources');
            throw new Error(`Source fact collision: ${sourceFactRef}; import rolled back (${differing.join(',')})`);
          }
        }
        if (!existing.rows.length) {
          const sourceIdentity = sourceFact ? { ...sourceFact, source_fact_ref: sourceFactRef, origin: 'source_fact' } : { source_fact_ref: sourceFactRef, physical_sources: sources,
            dimensions: dimensions.filter(d => d.parent_record_id === recordId),
            attributes: attributes.filter(a => a.parent_record_id === recordId),
            formula: row.derivation || null, logical_measurement_id: row.logical_measurement_id || null,
            evidence_transition: bundle.evidenceTransition || null, origin: 'legacy_projection', unverified_source_binding: binding || null };
          // The printed reporting scope travels with the fact identity so the
          // platform can show and publish two scopes of one clause separately.
          const identity = { ...(sourceFact?.identity_raw || {}) };
          if (row.reporting_boundary && !identity.reporting_boundary) identity.reporting_boundary = row.reporting_boundary;
          if (row.reporting_boundary && !sourceIdentity.reporting_boundary) sourceIdentity.reporting_boundary = row.reporting_boundary;
          const rawValue = sourceFact ? sourceFact.value_raw ?? sourceFact.statement_raw ?? '' : row.value_raw ?? row.statement_raw ?? '';
          const rawUnit = sourceFact ? sourceFact.unit_raw ?? null : row.unit_raw || null;
          // A fact that prints no dates belongs to the reporting period the
          // report's own identity states; without that fallback every fact from
          // a normal report looked like it had no period and needed review.
          const factPeriodStart = sourceFact ? identity.period_start || null : row.period_start || null;
          const factPeriodEnd = sourceFact ? identity.period_end || null : row.period_end || null;
          const periodStart = factPeriodStart || metadata.report_period_start || null;
          const periodEnd = factPeriodEnd || metadata.report_period_end || null;
          if (!factPeriodStart && periodStart) identity.period_start = periodStart;
          if (!factPeriodEnd && periodEnd) identity.period_end = periodEnd;
          if ((!factPeriodStart || !factPeriodEnd) && (metadata.report_period_start || metadata.report_period_end))
            sourceIdentity.period_source = 'report_identity';
          await tx.query(`INSERT INTO esg.esg_disclosures(disclosure_id,source_report_id,disclosure_type,raw_indicator_name,raw_disclosure_text,
            period_type,period_start,period_end,data_nature,quality_status,extraction_version,source_ir_run_id,source_ir_revision,
            source_fact_ref,source_identity,source_payload) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'candidate',$10,$11,$12,$13,$14,$15)`,
            [disclosureId, reportId, kind, sourceFact ? sourceFact.source_row_label || identity.source_row_label || null : row.source_row_label || row.source_row_topic || metric.labels?.zh || metric.labels?.en || row.metric_id,
             String(rawValue), periodStart || periodEnd ? 'duration' : 'unknown',
             periodStart, periodEnd, row.value_origin === 'derived' ? 'calculated' : 'reported',
             IMPORTER_VERSION, sourceFact?.ir_run_id || sources[0].ir_run_id, sourceFact?.ir_revision ?? sources[0].ir_revision, sourceFactRef, sourceIdentity, sourcePayload]);
          if (kind === 'quantitative') {
            const comparator = row.comparator || 'exact';
            const numeric = comparator === 'exact' ? decimal(row.value_numeric) : null;
            const lower = decimal(row.value_lower ?? (['greater_than', 'greater_or_equal', 'gt', 'gte'].includes(comparator) ? row.value_numeric : null));
            const upper = decimal(row.value_upper ?? (['less_than', 'less_or_equal', 'lt', 'lte'].includes(comparator) ? row.value_numeric : null));
            if (numeric == null && lower == null && upper == null) throw new Error(`Fact ${recordId} has no numeric point or range`);
            await tx.query(`INSERT INTO esg.structured_values(disclosure_id,value_type,raw_value_text,numeric_value,lower_bound,upper_bound,raw_unit,precision_qualifier)
              VALUES($1,'numeric',$2,$3,$4,$5,$6,$7)`, [disclosureId, rawValue, numeric, lower, upper, rawUnit, comparator]);
          } else {
            if (!String(rawValue).trim() || /^\d+(?:\.\d+)?$/.test(String(rawValue).trim())) throw new Error(`Fact ${recordId} is not a readable statement`);
            await tx.query(`INSERT INTO esg.qualitative_claims(disclosure_id,claim_type,statement_raw,summary_text,qualifiers)
              VALUES($1,'other',$2,$3,$4)`, [disclosureId, rawValue, row.statement_summary || null, { source_record_id: recordId }]);
          }
          const rawDimensions = sourceFact ? Object.entries(identity).map(([key, raw]) => ({ key,
            raw: raw && typeof raw === 'object' ? stableJSON(raw) : raw, canonical: null })) : [
            ...dimensions.filter(d => d.parent_record_id === recordId).map(d => ({ key: d.dimension_id, raw: d.value_raw, canonical: d.value_code })),
            ...attributes.filter(a => a.parent_record_id === recordId).map(a => ({ key: elements.get(a.element_id)?.element_code || a.element_id,
              raw: a.value_text ?? a.value_code ?? a.value_numeric ?? a.value_boolean ?? a.value_date, canonical: a.value_code })),
          ];
          const dimensionOrdinals = new Map();
          for (const d of rawDimensions) {
            if (d.raw == null || String(d.raw) === '') continue;
            const ordinal = (dimensionOrdinals.get(d.key) || 0) + 1;
            dimensionOrdinals.set(d.key, ordinal);
            await tx.query(`INSERT INTO esg.disclosure_dimensions(disclosure_dimension_id,disclosure_id,dimension_key,ordinal,raw_value,canonical_value)
              VALUES($1,$2,$3,$4,$5,$6)`, [uuid(`${disclosureId}:${d.key}:${ordinal}`), disclosureId, d.key, ordinal, String(d.raw), d.canonical == null ? null : String(d.canonical)]);
          }
          imported++;
        }
        for (const source of sources) {
          const sourceId = uuid(`${disclosureId}:${source.ir_object_id}:${source.pdf_page_index}:${source.evidence_excerpt}`);
          if ((await tx.query('SELECT 1 FROM esg.disclosure_sources WHERE disclosure_source_id=$1', [sourceId])).rows.length) continue;
          const ordinal = Number((await tx.query('SELECT COALESCE(max(ordinal),0)+1 AS next FROM esg.disclosure_sources WHERE disclosure_id=$1 AND source_role=$2', [disclosureId, 'primary'])).rows[0].next);
          await tx.query(`INSERT INTO esg.disclosure_sources(disclosure_source_id,disclosure_id,page_number,printed_page_label,source_node_id,source_quote,bbox,ordinal)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, [sourceId, disclosureId, source.pdf_page_index + 1, source.printed_page_label || null,
            source.ir_object_id, source.evidence_excerpt || '', { x0: source.bbox_x0, y0: source.bbox_y0, x1: source.bbox_x1, y1: source.bbox_y1 }, ordinal]);
        }
        const requirementId = uuid(`${frameworkVersionId}:${row.metric_id}`);
        const clauseKind=metric.clause_kind || 'original_datapoint';
        const clauseCode=clauseKind==='project_extension'?metric.extension_id:metric.source_datapoint_id || row.metric_id;
        await tx.query(`INSERT INTO esg.standard_requirements(requirement_id,framework_version_id,requirement_code,requirement_title,requirement_text,requirement_type,clause_kind,extension_id)
          VALUES($1,$2,$3,$4,$5,'datapoint',$6,$7) ON CONFLICT DO NOTHING`, [requirementId, frameworkVersionId,clauseCode,
          metric.labels?.zh || metric.labels?.en || row.metric_id, metric.description || '',clauseKind,metric.extension_id || null]);
        // The extraction's own evidence decides how closely this fact matches
        // the clause: a guard-accepted reading with no narrower scope bound to
        // it is the exact concept, while a printed subset boundary is a
        // narrower disclosure. The mapping still needs a business review, but
        // it must not be labelled "not equivalent" when the evidence says it is.
        const printedBoundary = sourceFact?.identity_raw?.reporting_boundary || row.reporting_boundary || null;
        const boundaryReading = normalizeBoundary(printedBoundary);
        const subsetScope = Boolean(boundaryReading && !boundaryReading.is_group);
        const acceptedReading = row.review_status === 'pending';
        const derivedRelation = subsetScope ? 'narrower' : acceptedReading ? 'exact' : 'partial';
        const derivedSatisfaction = subsetScope ? 'partially_supports' : acceptedReading ? 'supports' : 'unknown';
        await tx.query(`INSERT INTO esg.disclosure_standard_mappings(disclosure_standard_mapping_id,disclosure_id,requirement_id,mapping_relation,satisfaction_status,mapping_reason,mapper_version)
          VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING`, [uuid(`${disclosureId}:${requirementId}:${IMPORTER_VERSION}`), disclosureId, requirementId,
          derivedRelation, derivedSatisfaction,
          stableJSON({ review_note: acceptedReading
              ? 'Imported extraction mapping derived as exact from the guard-accepted reading; independent business review still required.'
              : 'Imported extraction mapping; requires independent business review.',
            bundle_job_id: jobId, core_record_id: recordId, source_binding: binding || null,
            mapping_evidence: { rule: 'bundle-review-status-v1', observation_review_status: row.review_status,
              printed_boundary: printedBoundary, boundary_code: boundaryReading?.code || null,
              binding_match: binding?.match || null,
              derived_relation: derivedRelation, derived_satisfaction: derivedSatisfaction },
            candidate_scope_role:bundle.internalScope?.metric_ids.includes(row.metric_id)?'internal_dependency':'selected_output',
            required_by_extension_ids:bundle.internalScope?.metrics.find(metric=>metric.metric_id===row.metric_id)?.required_by_extension_ids || [],
            projection_dimensions: dimensions.filter(d => d.parent_record_id === recordId),
            projection_attributes: attributes.filter(a => a.parent_record_id === recordId) }), IMPORTER_VERSION]);
        await tx.query('INSERT INTO esg.import_items VALUES($1,$2,$3)', [batchId, recordId, disclosureId]);
      }
    }
    const importedIds = new Map((await tx.query('SELECT bundle_record_id,disclosure_id FROM esg.import_items WHERE import_batch_id=$1', [batchId])).rows.map(r => [r.bundle_record_id, r.disclosure_id]));
    const relationTypes = { repeated_disclosure: 'duplicate_disclosure', alternative_unit_presentation: 'alternative_display' };
    async function relation(left, right, type, status, detail, formulaVersion = null) {
      if (type !== 'operand' && left > right) [left, right] = [right, left];
      const sourceId = importedIds.get(left), targetId = importedIds.get(right) || null;
      if (!sourceId || sourceId === targetId) return;
      await tx.query(`INSERT INTO esg.disclosure_relations(relation_id,source_disclosure_id,target_disclosure_id,relation_type,relation_status,formula_version,detail)
        VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING`, [uuid(`${sourceId}:${targetId || right}:${type}:${formulaVersion || ''}`), sourceId, targetId, type, status, formulaVersion, detail]);
    }
    for (const group of bundle.organization?.groups || []) {
      for (let i = 1; i < group.fact_ids.length; i++) {
        const kind = relationTypes[group.relations?.[i]];
        if (kind) await relation(group.fact_ids[0], group.fact_ids[i], kind, 'confirmed', { logical_measurement_id: group.logical_measurement_id });
      }
    }
    for (const [left, annotation] of Object.entries(bundle.organization?.facts || {})) {
      for (const candidate of annotation.possible_relations || []) {
        const kind = relationTypes[candidate.relation];
        if (kind) await relation(left, candidate.fact_id, kind, 'candidate', candidate);
      }
    }
    for (const rule of bundle.derivations?.rules || []) {
      for (const binding of rule.dependency_bindings || []) {
        for (const operand of binding.source_observation_ids || []) {
          await relation(binding.derived_observation_id, operand, 'operand', 'confirmed', {
            ...binding, source_observation_id: operand,
            internal_fact: bundle.internalDependencies?.records?.quantitative_observations?.find(o => o.observation_id === operand) || null,
          }, binding.formula_version || `${bundle.request.package_id}@${bundle.request.package_version}:${rule.rule_id}`);
        }
      }
    }
    return { import_batch_id: batchId, imported, source_fact_snapshots: sourceFacts.size,
      idempotent: false, quality_status: 'candidate', publication_status: 'not_published' };
  });
}
