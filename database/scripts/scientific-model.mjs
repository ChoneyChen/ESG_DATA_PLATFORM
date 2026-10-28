// Project legacy curated observations into the scientific model, then verify equality.
// The legacy tables stay authoritative until reconcile reports no differences.
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const nonempty = value => typeof value === 'string' && value.trim().length > 0;

export const SCIENTIFIC_MODEL_VERSION = 'scientific-model-v1';

export function clauseVersionFromPackage(clause) {
  if (!nonempty(clause.clause_id) || !nonempty(clause.metric_id) || !nonempty(clause.contract_digest))
    throw new Error('Clause version requires its identity, metric and contract digest');
  if (!['numeric', 'statement'].includes(clause.value_type))
    throw new Error(`Clause version has an invalid value type: ${clause.clause_id}`);
  if (clause.value_type === 'numeric' && !nonempty(clause.canonical_unit))
    throw new Error(`Numeric clause version requires its canonical unit: ${clause.clause_id}`);
  return {
    clause_version_id: clause.clause_id,
    package_id: clause.package_id,
    package_version: clause.package_version,
    source_datapoint_id: clause.source_datapoint_id,
    metric_id: clause.metric_id,
    contract_digest: clause.contract_digest,
    label_zh: clause.labels?.zh ?? null,
    value_type: clause.value_type,
    canonical_unit: clause.canonical_unit ?? null,
    scopes: JSON.stringify(clause.output_scopes ?? []),
    evidence: JSON.stringify(clause.evidence ?? []),
  };
}

export async function projectClauseVersions(tx, clauses) {
  let written = 0;
  for (const clause of clauses) {
    const row = clauseVersionFromPackage(clause);
    const existing = (await tx.query('SELECT contract_digest FROM esg.clause_versions WHERE clause_version_id=$1',
      [row.clause_version_id])).rows[0];
    // A clause id is a version identity: different content under the same id is
    // a new version, never an edit in place.
    if (existing && existing.contract_digest !== row.contract_digest)
      throw new Error(`Clause version content differs from its digest: ${row.clause_version_id}`);
    await tx.query(`INSERT INTO esg.clause_versions(
        clause_version_id,package_id,package_version,source_datapoint_id,metric_id,contract_digest,
        label_zh,value_type,canonical_unit,scopes,evidence,status,acceptance_state)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'active',COALESCE($12,'untested'))
      ON CONFLICT (clause_version_id) DO UPDATE SET projected_at=CURRENT_TIMESTAMP`,
      [row.clause_version_id, row.package_id, row.package_version, row.source_datapoint_id, row.metric_id,
       row.contract_digest, row.label_zh, row.value_type, row.canonical_unit, row.scopes, row.evidence,
       clause.acceptance_state ?? null]);
    written += 1;
  }
  return {clauses: written};
}

function sourceRowsByObservation(rows) {
  const grouped = new Map();
  for (const row of rows) {
    const list = grouped.get(row.observation_id) ?? [];
    list.push(row);
    grouped.set(row.observation_id, list);
  }
  return grouped;
}

function dimensionEntries(dimensions) {
  if (!object(dimensions)) return [];
  return Object.entries(dimensions)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '')
    .map(([axis, value]) => ({ axis, code: object(value) ? String(value.code ?? value.value ?? JSON.stringify(value)) : String(value),
      printed_label: object(value) && typeof value.printed_label === 'string' ? value.printed_label : null }));
}

function evidenceEntries(snapshot) {
  const identity = object(snapshot?.identity_evidence) ? snapshot.identity_evidence : {};
  const entries = [];
  for (const [field, items] of Object.entries(identity)) {
    for (const item of Array.isArray(items) ? items : []) {
      if (!object(item)) continue;
      entries.push({ field, ir_object_id: item.ir_object_id ?? null, page_index: item.pdf_page_index ?? null,
        bbox: item.bbox ? JSON.stringify(item.bbox) : null, quote: item.raw_text ?? null,
        source_fact_ref: snapshot?.source_fact_ref ?? null });
    }
  }
  return entries;
}

export async function projectObservations(tx, { limit = 100000 } = {}) {
  if (!Number.isInteger(limit) || limit < 1) throw new Error('Projection limit must be a positive integer');
  // The platform dictionary identifies metrics by its own ids; the clause
  // catalog by standard package metrics. The shared identity is the original
  // datapoint, so resolve through it and fall back to the metric id only when
  // the clause still uses the same identifier.
  const clauseByPackageMetric = new Map((await tx.query(
    'SELECT metric_id, clause_version_id FROM esg.clause_versions')).rows.map(row => [row.metric_id, row.clause_version_id]));
  const clauseBySource = new Map((await tx.query(
    'SELECT source_datapoint_id, clause_version_id FROM esg.clause_versions')).rows
    .map(row => [row.source_datapoint_id, row.clause_version_id]));
  const sourceByMetric = new Map((await tx.query(
    `SELECT metric_id, source_datapoint_id FROM esg.metric_definition_versions
     ORDER BY created_at DESC`)).rows.map(row => [row.metric_id, row.source_datapoint_id]));
  const sources = sourceRowsByObservation((await tx.query(`
    SELECT os.observation_id, os.context_id, os.disclosure_id, os.mapping_id, os.source_snapshot,
           d.source_report_id, r.report_ref
    FROM esg.observation_sources os
    JOIN esg.esg_disclosures d USING(disclosure_id)
    JOIN esg.source_report_refs r USING(source_report_id)`)).rows);
  const published = new Map((await tx.query(`
    SELECT m.observation_id, p.publication_version
    FROM esg.report_publication_members m
    JOIN esg.report_publications p USING(source_report_id)
    WHERE p.status='published'`)).rows.map(row => [row.observation_id, row.publication_version]));
  const observations = (await tx.query(`
    SELECT * FROM esg.curated_observations ORDER BY created_at, observation_id LIMIT $1`, [limit])).rows;

  let projected = 0;
  const unmappedMetrics = new Set();
  const missingSource = [];
  for (const observation of observations) {
    const clauseVersionId = (sourceByMetric.has(observation.metric_id)
      ? clauseBySource.get(sourceByMetric.get(observation.metric_id)) : null)
      ?? clauseByPackageMetric.get(observation.metric_id);
    if (!clauseVersionId) { unmappedMetrics.add(observation.metric_id); continue; }
    const links = sources.get(observation.observation_id) ?? [];
    if (!links.length) { missingSource.push(observation.observation_id); continue; }
    const reportRef = links[0].report_ref;
    const periodKind = observation.period_start && observation.period_end
      ? (observation.period_start === observation.period_end ? 'instant' : 'annual') : null;
    await tx.query(`
      INSERT INTO esg.scientific_observation_model(
        observation_id,entity_ref,clause_version_id,report_ref,period_start,period_end,period_kind,
        value_numeric,value_lower,value_upper,value_text,canonical_unit,value_role,measurement_method,
        aggregation_role,comparison_eligibility,publication_state,publication_version,source_observation_id)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$1)
      ON CONFLICT (observation_id) DO UPDATE SET
        entity_ref=excluded.entity_ref, clause_version_id=excluded.clause_version_id, report_ref=excluded.report_ref,
        period_start=excluded.period_start, period_end=excluded.period_end, period_kind=excluded.period_kind,
        value_numeric=excluded.value_numeric, value_lower=excluded.value_lower, value_upper=excluded.value_upper,
        value_text=excluded.value_text, canonical_unit=excluded.canonical_unit, value_role=excluded.value_role,
        measurement_method=excluded.measurement_method, aggregation_role=excluded.aggregation_role,
        comparison_eligibility=excluded.comparison_eligibility, publication_state=excluded.publication_state,
        publication_version=excluded.publication_version, projected_at=CURRENT_TIMESTAMP`,
      [observation.observation_id, observation.entity_ref, clauseVersionId, reportRef,
       observation.period_start, observation.period_end, periodKind,
       observation.numeric_value, observation.lower_bound, observation.upper_bound, observation.statement,
       observation.canonical_unit, observation.value_role, observation.measurement_method,
       object(observation.dimensions) ? observation.dimensions.aggregation_role ?? null : null,
       observation.comparison_eligibility,
       published.has(observation.observation_id) ? 'published' : 'draft',
       published.get(observation.observation_id) ?? null]);
    await tx.query('DELETE FROM esg.observation_dimensions WHERE observation_id=$1', [observation.observation_id]);
    for (const entry of dimensionEntries(observation.dimensions)) {
      await tx.query(`INSERT INTO esg.observation_dimensions(observation_id,axis,code,printed_label,evidence_ref)
        VALUES($1,$2,$3,$4,NULL) ON CONFLICT (observation_id,axis) DO UPDATE SET code=excluded.code,
        printed_label=excluded.printed_label`, [observation.observation_id, entry.axis, entry.code, entry.printed_label]);
    }
    await tx.query('DELETE FROM esg.observation_evidence WHERE observation_id=$1', [observation.observation_id]);
    for (const link of links) {
      for (const entry of evidenceEntries(link.source_snapshot)) {
        await tx.query(`INSERT INTO esg.observation_evidence(
            observation_id,field,ir_object_id,page_index,bbox,quote,source_fact_ref)
          VALUES($1,$2,$3,$4,$5,$6,$7)`,
          [observation.observation_id, entry.field, entry.ir_object_id, entry.page_index, entry.bbox,
           entry.quote, entry.source_fact_ref]);
      }
    }
    await tx.query('DELETE FROM esg.observation_model_sources WHERE observation_id=$1', [observation.observation_id]);
    for (const link of links) {
      await tx.query(`INSERT INTO esg.observation_model_sources(
          observation_id,report_ref,raw_value,raw_unit,raw_label,source_fact_ref,disclosure_id,mapping_id,context_id,source_snapshot)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
        [observation.observation_id, link.report_ref,
         link.source_snapshot?.raw_value_text ?? null, link.source_snapshot?.raw_unit ?? null,
         link.source_snapshot?.source_row_label ?? null, link.source_snapshot?.source_fact_ref ?? null,
         link.disclosure_id, link.mapping_id, link.context_id, JSON.stringify(link.source_snapshot ?? {})]);
    }
    projected += 1;
  }
  return { projected, unmapped_metrics: [...unmappedMetrics], observations_without_source: missingSource,
           legacy_observations: observations.length };
}

export async function reconcileScientificModel(tx) {
  const legacy = (await tx.query('SELECT COUNT(*)::int AS n FROM esg.curated_observations')).rows[0].n;
  const model = (await tx.query('SELECT COUNT(*)::int AS n FROM esg.scientific_observation_model')).rows[0].n;
  const missing = (await tx.query(`
    SELECT o.observation_id FROM esg.curated_observations o
    LEFT JOIN esg.scientific_observation_model m USING (observation_id)
    WHERE m.observation_id IS NULL LIMIT 50`)).rows.map(row => row.observation_id);
  const valueMismatch = (await tx.query(`
    SELECT o.observation_id FROM esg.curated_observations o
    JOIN esg.scientific_observation_model m USING (observation_id)
    WHERE o.numeric_value IS DISTINCT FROM m.value_numeric
       OR COALESCE(o.statement,'') IS DISTINCT FROM COALESCE(m.value_text,'')
       OR o.entity_ref IS DISTINCT FROM m.entity_ref
       OR o.period_start IS DISTINCT FROM m.period_start
       OR o.period_end IS DISTINCT FROM m.period_end
    LIMIT 50`)).rows.map(row => row.observation_id);
  const publicationMismatch = (await tx.query(`
    SELECT m.observation_id FROM esg.scientific_observation_model m
    JOIN LATERAL (
      SELECT p.publication_version FROM esg.report_publication_members pm
      JOIN esg.report_publications p USING (source_report_id)
      WHERE pm.observation_id=m.observation_id AND p.status='published'
      ORDER BY p.publication_version DESC LIMIT 1) pub ON TRUE
    WHERE m.publication_state<>'published' OR m.publication_version IS DISTINCT FROM pub.publication_version
    LIMIT 50`)).rows.map(row => row.observation_id);
  return { schema_version: SCIENTIFIC_MODEL_VERSION, legacy, model, missing,
           value_mismatches: valueMismatch, publication_mismatches: publicationMismatch,
           ok: legacy === model && !missing.length && !valueMismatch.length && !publicationMismatch.length };
}
