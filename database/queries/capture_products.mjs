// Product persistence owns transactions; interpretation is the database SDK's deterministic compiler.
import {digest,fault,cursorAfter,encodeCursor} from '../runtime/canonical.mjs';

export async function projectionInput(db,{export_id}) {
  const source=(await db.query('SELECT export_id,input_digest,manifest,coverage FROM esg.capture_exports WHERE export_id=$1',[export_id])).rows[0];
  if(!source)throw fault('not_found','Capture export does not exist');
  return {...source,
    facts:(await db.query('SELECT f.record FROM esg.captured_facts f JOIN esg.capture_export_facts e USING(source_fact_id) WHERE e.export_id=$1 ORDER BY f.source_fact_id',[export_id])).rows,
    targets:(await db.query('SELECT clause_key,contract FROM esg.capture_clause_targets WHERE export_id=$1 ORDER BY clause_key',[export_id])).rows,
    relations:(await db.query('SELECT source_fact_id,clause_key,relation,mapping FROM esg.capture_clause_relations WHERE export_id=$1 ORDER BY source_fact_id,clause_key',[export_id])).rows};
}

export async function storeProjection(db,{summary,definitions,observations}) {
  return db.transaction(async tx=>{
    const source=(await tx.query('SELECT input_digest FROM esg.capture_exports WHERE export_id=$1 FOR UPDATE',[summary.export_id])).rows[0];
    if(!source||source.input_digest!==summary.input_digest)throw fault('projection_source_changed','Projection source differs');
    const previous=(await tx.query('SELECT content_digest,summary FROM esg.capture_projections WHERE export_id=$1 AND transform_version=$2',[summary.export_id,summary.transform_version])).rows[0];
    if(previous) {
      if(previous.content_digest!==summary.content_digest)throw fault('projection_identity_conflict','Rebuild with a new transform version');
      return previous.summary;
    }
    const expected=(await tx.query(`SELECT count(*)::int AS facts,COALESCE(sum(GREATEST(1,(SELECT count(*) FROM esg.capture_clause_relations r
      WHERE r.export_id=e.export_id AND r.source_fact_id=e.source_fact_id))),0)::int AS observations
      FROM esg.capture_export_facts e WHERE export_id=$1`,[summary.export_id])).rows[0];
    if(summary.facts!==expected.facts || observations.length!==expected.observations || summary.observations!==observations.length || new Set(observations.map(row=>row.observation_id)).size!==observations.length)
      throw fault('projection_count_mismatch','Projection cannot omit admitted facts or duplicate observations');
    for(const definition of definitions) {
      const target=(await tx.query('SELECT clause_digest FROM esg.capture_clause_targets WHERE export_id=$1 AND clause_key=$2 AND contract=$3::jsonb',
        [summary.export_id,definition.clause_key,JSON.stringify(definition.contract)])).rows[0];
      if(!target||target.clause_digest!==definition.clause_digest)throw fault('projection_definition_conflict','Definition must be the frozen capture target');
      await tx.query('INSERT INTO esg.capture_product_definitions VALUES($1,$2,$3,$4::jsonb) ON CONFLICT(clause_digest) DO NOTHING',
        [definition.clause_digest,definition.clause_key,definition.title,JSON.stringify(definition.contract)]);
    }
    await tx.query('INSERT INTO esg.capture_projections(projection_id,export_id,input_digest,transform_version,content_digest,summary) VALUES($1,$2,$3,$4,$5,$6::jsonb)',
      [summary.projection_id,summary.export_id,summary.input_digest,summary.transform_version,summary.content_digest,JSON.stringify(summary)]);
    const admitted=new Set();
    for(const row of observations) {
      const member=(await tx.query('SELECT 1 FROM esg.capture_export_facts WHERE export_id=$1 AND source_fact_id=$2',[summary.export_id,row.source_fact_id])).rows.length;
      const links=(await tx.query('SELECT clause_key,clause_digest,relation FROM esg.capture_clause_relations r JOIN esg.capture_clause_targets t USING(export_id,clause_key) WHERE export_id=$1 AND source_fact_id=$2',
        [summary.export_id,row.source_fact_id])).rows;
      if(!member || (row.clause_key ? !links.some(link=>link.clause_key===row.clause_key&&link.clause_digest===row.clause_digest&&link.relation===row.relation) : links.length))
        throw fault('projection_lineage_conflict','Observation must retain its admitted source and frozen relation');
      await tx.query(`INSERT INTO esg.capture_observations(observation_id,source_fact_id,occurrence_id,clause_key,clause_digest,relation,comparison_key,record)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb) ON CONFLICT(observation_id) DO NOTHING`,
        [row.observation_id,row.source_fact_id,row.occurrence_id,row.clause_key,row.clause_digest,row.relation,row.comparison_key,JSON.stringify(row)]);
      if(!(await tx.query('SELECT 1 FROM esg.capture_observations WHERE observation_id=$1 AND record=$2::jsonb',[row.observation_id,JSON.stringify(row)])).rows.length)
        throw fault('observation_identity_conflict','Observation identity already has different content');
      await tx.query('INSERT INTO esg.capture_projection_members VALUES($1,$2)',[summary.projection_id,row.observation_id]);
      admitted.add(row.source_fact_id);
    }
    if(admitted.size!==expected.facts)throw fault('projection_count_mismatch','All admitted facts need an observation');
    await tx.query(`INSERT INTO esg.capture_projection_attempts(export_id,transform_version,status) VALUES($1,$2,'completed')
      ON CONFLICT(export_id,transform_version) DO UPDATE SET status='completed',error_code=NULL,retryable=false,updated_at=CURRENT_TIMESTAMP`,[summary.export_id,summary.transform_version]);
    return summary;
  });
}

export async function projectionStatus(db,{export_id,transform_version}) {
  if(!(await db.query('SELECT 1 FROM esg.capture_exports WHERE export_id=$1',[export_id])).rows.length)throw fault('not_found','Unknown capture');
  const projection=(await db.query('SELECT summary FROM esg.capture_projections WHERE export_id=$1 AND transform_version=$2',[export_id,transform_version])).rows[0];
  if(projection)return {export_id,status:'completed',projection:projection.summary,error_code:null,retryable:false};
  const attempt=(await db.query('SELECT status,error_code,retryable FROM esg.capture_projection_attempts WHERE export_id=$1 AND transform_version=$2',[export_id,transform_version])).rows[0];
  return {export_id,status:attempt?.status||'pending',projection:null,error_code:attempt?.error_code||null,retryable:attempt?.retryable??true};
}

export async function pendingProjections(db,{transform_version}) {
  return (await db.query(`SELECT e.export_id FROM esg.capture_exports e LEFT JOIN esg.capture_projection_attempts a
    ON a.export_id=e.export_id AND a.transform_version=$1 WHERE a.export_id IS NULL OR (a.status='failed' AND a.retryable AND a.updated_at<CURRENT_TIMESTAMP-interval '30 seconds')
    ORDER BY e.accepted_at,e.export_id LIMIT 10`,[transform_version])).rows;
}

export async function recordProjectionFailure(db,{export_id,transform_version,error_code,retryable=false}) {
  await db.query(`INSERT INTO esg.capture_projection_attempts(export_id,transform_version,status,error_code,retryable)
    VALUES($1,$2,'failed',$3,$4) ON CONFLICT(export_id,transform_version) DO UPDATE
    SET status='failed',error_code=excluded.error_code,retryable=excluded.retryable,updated_at=CURRENT_TIMESTAMP`,[export_id,transform_version,error_code,retryable]);
  return {recorded:true};
}

export async function productInput(db,{projection_ids}) {
  if(!Array.isArray(projection_ids)||!projection_ids.length||projection_ids.length>1000)throw fault('invalid_query','Select projection identities');
  const ids=[...new Set(projection_ids)].sort();
  const projections=(await db.query('SELECT projection_id,content_digest FROM esg.capture_projections WHERE projection_id=ANY($1) ORDER BY projection_id',[ids])).rows;
  if(projections.length!==ids.length)throw fault('not_found','A projection does not exist');
  const observations=(await db.query('SELECT DISTINCT o.observation_id,o.record FROM esg.capture_observations o JOIN esg.capture_projection_members m USING(observation_id) WHERE m.projection_id=ANY($1) ORDER BY o.observation_id',[ids])).rows.map(row=>row.record);
  const links=(await db.query(`SELECT link_id,newer_observation_id,older_observation_id FROM esg.capture_restatements
    WHERE newer_observation_id=ANY($1) AND older_observation_id=ANY($1) ORDER BY link_id`,[observations.map(row=>row.observation_id)])).rows;
  return {projections,observations,links};
}

export async function projectionCatalog(db,{limit=100,cursor=null}={}) {
  if(!Number.isInteger(limit)||limit<1||limit>1000)throw fault('invalid_query','Invalid page size');
  const source=(await db.query('SELECT projection_id,content_digest FROM esg.capture_projections ORDER BY projection_id')).rows;
  const query=digest([source,limit]);
  const after=cursorAfter(cursor,query);
  const rows=(await db.query('SELECT projection_id,summary FROM esg.capture_projections WHERE projection_id>$1 ORDER BY projection_id LIMIT $2',[after,limit+1])).rows;
  const more=rows.length>limit;if(more)rows.pop();
  return {schema_version:'capture-projection-page-1.0',query_digest:query,total:source.length,returned:rows.length,items:rows.map(row=>row.summary),
    next_cursor:more?encodeCursor(query,rows.at(-1).projection_id):null};
}
