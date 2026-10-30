// Explicit, immutable publication membership. Withdrawal changes visibility, never facts.
import {digest,canonical,fault,cursorAfter,encodeCursor} from '../runtime/canonical.mjs';
import {productInput} from './capture_products.mjs';

const version=row=>({schema_version:'capture-dataset-version-1.0',dataset_version_id:row.dataset_version_id,
  title:row.title,content_digest:row.content_digest,policy:row.policy,state:row.state,revision:row.revision,
  published_at:row.published_at instanceof Date?row.published_at.toISOString():row.published_at,
  observations:row.snapshot.selected,periods:row.snapshot.periods});

export async function datasetVersion(db,{dataset_version_id,published_only=true}) {
  const row=(await db.query('SELECT * FROM esg.capture_dataset_versions WHERE dataset_version_id=$1'+(published_only?" AND state='published'":''),[dataset_version_id])).rows[0];
  if(!row)throw fault('not_found','Dataset is not available');
  return version(row);
}

export async function publishDataset(db,{preview,actor,reason}) {
  const {preview_digest,...snapshot}=preview;
  if(digest(snapshot)!==preview_digest)throw fault('invalid_preview','Dataset preview digest differs');
  const id='dataset-'+preview_digest;
  return db.transaction(async tx=>{
    const existing=(await tx.query('SELECT * FROM esg.capture_dataset_versions WHERE dataset_version_id=$1 FOR UPDATE',[id])).rows[0];
    if(existing)return version(existing); // Replaying publication never restores a withdrawn version.
    await tx.query('SELECT projection_id FROM esg.capture_projections WHERE projection_id=ANY($1) ORDER BY projection_id FOR UPDATE',[preview.projection_ids]);
    const source=await productInput(tx,preview);
    const sources=Object.fromEntries(source.projections.map(row=>[row.projection_id,row.content_digest]));
    if(canonical(sources)!==canonical(preview.projection_digests)||canonical(source.links.map(row=>row.link_id).sort())!==canonical(preview.restatement_link_ids))
      throw fault('preview_changed','Projection or reviewed source relationships changed');
    const available=new Set(source.observations.map(row=>row.observation_id));
    const decisions=new Set(preview.decisions.map(row=>row.observation_id));
    const selected=preview.decisions.filter(row=>row.selected);
    if(!selected.length||preview.total!==available.size||preview.selected!==selected.length||decisions.size!==preview.decisions.length||decisions.size!==available.size||[...decisions].some(key=>!available.has(key)))
      throw fault('invalid_preview','Dataset preview must account for all source observations');
    await tx.query(`INSERT INTO esg.capture_dataset_versions(dataset_version_id,title,content_digest,policy,snapshot,state)
      VALUES($1,$2,$3,$4::jsonb,$5::jsonb,'published') ON CONFLICT(dataset_version_id) DO NOTHING`,
      [id,preview.title,preview_digest,JSON.stringify(preview.policy),JSON.stringify(preview)]);
    // A concurrent idempotent publisher may have committed the same immutable version.
    const stored=(await tx.query('SELECT * FROM esg.capture_dataset_versions WHERE dataset_version_id=$1 FOR UPDATE',[id])).rows[0];
    if((await tx.query('SELECT 1 FROM esg.capture_dataset_events WHERE dataset_version_id=$1',[id])).rows.length)return version(stored);
    for(const projection of preview.projection_ids)await tx.query('INSERT INTO esg.capture_dataset_projections VALUES($1,$2)',[id,projection]);
    for(const row of selected)await tx.query('INSERT INTO esg.capture_dataset_members VALUES($1,$2,$3)',[id,row.observation_id,row.reason]);
    await tx.query(`INSERT INTO esg.capture_dataset_events(event_id,dataset_version_id,revision,kind,actor,reason)
      VALUES($1,$2,1,'published',$3,$4)`,['event-'+digest([id,1]),id,actor,reason]);
    return version(stored);
  });
}

export async function withdrawDataset(db,{dataset_version_id,expected_revision,actor,reason}) {
  return db.transaction(async tx=>{
    const row=(await tx.query('SELECT * FROM esg.capture_dataset_versions WHERE dataset_version_id=$1 FOR UPDATE',[dataset_version_id])).rows[0];
    if(!row)throw fault('not_found','Unknown dataset');
    if(row.state==='withdrawn')return version(row);
    if(row.revision!==expected_revision)throw fault('revision_conflict','Dataset visibility changed');
    const revision=row.revision+1;
    await tx.query("UPDATE esg.capture_dataset_versions SET state='withdrawn',revision=$2 WHERE dataset_version_id=$1",[dataset_version_id,revision]);
    await tx.query("INSERT INTO esg.capture_dataset_events(event_id,dataset_version_id,revision,kind,actor,reason) VALUES($1,$2,$3,'withdrawn',$4,$5)",
      ['event-'+digest([dataset_version_id,revision]),dataset_version_id,revision,actor,reason]);
    return version({...row,state:'withdrawn',revision});
  });
}

export async function restatementInput(db,{newer_observation_id,older_observation_id}) {
  const read=async id=>{
    const row=(await db.query('SELECT record FROM esg.capture_observations WHERE observation_id=$1',[id])).rows[0];
    if(!row)throw fault('not_found','Observation does not exist');
    return row.record;
  };
  return {newer:await read(newer_observation_id),older:await read(older_observation_id)};
}

export async function recordRestatement(db,request) {
  const {newer_observation_id:newer,older_observation_id:older,reason,actor,expected_records}=request;
  return db.transaction(async tx=>{
    await tx.query(`SELECT p.projection_id FROM esg.capture_projections p JOIN esg.capture_projection_members m USING(projection_id)
      WHERE m.observation_id=ANY($1) ORDER BY p.projection_id FOR UPDATE OF p`,[[newer,older]]);
    const actual=await restatementInput(tx,request);
    if(canonical(actual)!==canonical(expected_records))throw fault('restatement_source_changed','Observation source differs');
    const cycle=(await tx.query(`WITH RECURSIVE successors(id) AS (
      SELECT newer_observation_id FROM esg.capture_restatements WHERE older_observation_id=$1
      UNION SELECT r.newer_observation_id FROM esg.capture_restatements r JOIN successors s ON r.older_observation_id=s.id)
      SELECT 1 FROM successors WHERE id=$2`,[newer,older])).rows.length;
    if(cycle||newer===older)throw fault('restatement_cycle','Replacement relationships cannot cycle');
    const link='restatement-'+digest([newer,older]);
    await tx.query(`INSERT INTO esg.capture_restatements(link_id,newer_observation_id,older_observation_id,reason,actor)
      VALUES($1,$2,$3,$4,$5) ON CONFLICT(newer_observation_id,older_observation_id) DO NOTHING`,[link,newer,older,reason,actor]);
    return (await tx.query('SELECT link_id,newer_observation_id,older_observation_id,reason,actor FROM esg.capture_restatements WHERE link_id=$1',[link])).rows[0];
  });
}

export async function datasetCatalog(db,{published_only=true,limit=100,cursor=null}={}) {
  if(!Number.isInteger(limit)||limit<1||limit>1000)throw fault('invalid_query','Invalid page size');
  const where=published_only?"WHERE state='published'":'';
  const revision=(await db.query(`SELECT dataset_version_id,revision,state FROM esg.capture_dataset_versions ${where} ORDER BY dataset_version_id`)).rows;
  const query=digest([revision,published_only,limit]);
  const after=cursorAfter(cursor,query);
  const items=(await db.query(`SELECT * FROM esg.capture_dataset_versions ${where?where+' AND':'WHERE'} dataset_version_id>$1 ORDER BY dataset_version_id LIMIT $2`,[after,limit+1])).rows;
  const more=items.length>limit;if(more)items.pop();
  return {schema_version:'capture-dataset-page-1.0',query_digest:query,total:revision.length,returned:items.length,items:items.map(version),
    next_cursor:more?encodeCursor(query,items.at(-1).dataset_version_id):null};
}

export async function observationPage(db,{projection_id=null,dataset_version_id=null,published_only=true,limit=200,cursor=null,period=null,clause_key=null,relation=null}) {
  if(!Number.isInteger(limit)||limit<1||limit>1000||Boolean(projection_id)===Boolean(dataset_version_id))throw fault('invalid_query','Select one product snapshot');
  if(relation&&!['match','uncertain','different'].includes(relation))throw fault('invalid_query','Unknown relation');
  const id=projection_id||dataset_version_id;
  const snapshot=projection_id?(await db.query('SELECT content_digest FROM esg.capture_projections WHERE projection_id=$1',[id])).rows[0]:await datasetVersion(db,{dataset_version_id,published_only});
  if(!snapshot)throw fault('not_found','Unknown projection');
  const query=digest([id,snapshot.content_digest,snapshot.revision||1,limit,period,clause_key,relation]);
  const after=cursorAfter(cursor,query);
  const table=projection_id?'capture_projection_members':'capture_dataset_members',key=projection_id?'projection_id':'dataset_version_id';
  const params=[id],conditions=[`m.${key}=$1`];
  if(period){params.push(period);conditions.push(`CASE WHEN o.record#>>'{period,kind}'='unknown' THEN 'unknown'
    WHEN o.record#>>'{period,kind}'='annual' THEN o.record#>>'{period,year}'
    WHEN o.record#>>'{period,kind}'='quarter' THEN (o.record#>>'{period,year}')||' Q'||(o.record#>>'{period,quarter}')
    ELSE o.record#>>'{period,raw}' END=$${params.length}`);}
  for(const [column,value]of [['clause_key',clause_key],['relation',relation]])if(value){params.push(value);conditions.push(`o.${column}=$${params.length}`);}
  const from=`FROM esg.capture_observations o JOIN esg.${table} m USING(observation_id) WHERE ${conditions.join(' AND ')}`;
  const total=(await db.query(`SELECT count(*)::int AS n ${from}`,params)).rows[0].n;
  const rows=(await db.query(`SELECT o.record ${from} AND o.observation_id>$${params.length+1} ORDER BY o.observation_id LIMIT $${params.length+2}`,[...params,after,limit+1])).rows;
  const more=rows.length>limit;if(more)rows.pop();
  return {schema_version:'capture-observation-page-1.0',snapshot_id:id,query_digest:query,total,returned:rows.length,items:rows.map(row=>row.record),
    next_cursor:more?encodeCursor(query,rows.at(-1).record.observation_id):null};
}

export async function productDefinitions(db,{projection_id=null,dataset_version_id=null,published_only=true}) {
  if(Boolean(projection_id)===Boolean(dataset_version_id))throw fault('invalid_query','Select one product snapshot');
  if(dataset_version_id)await datasetVersion(db,{dataset_version_id,published_only});
  else if(!(await db.query('SELECT 1 FROM esg.capture_projections WHERE projection_id=$1',[projection_id])).rows.length)throw fault('not_found','Unknown projection');
  const table=projection_id?'capture_projection_members':'capture_dataset_members',key=projection_id?'projection_id':'dataset_version_id';
  return (await db.query(`SELECT DISTINCT d.clause_key,d.clause_digest,d.title,d.contract FROM esg.capture_product_definitions d
    JOIN esg.capture_observations o USING(clause_digest) JOIN esg.${table} m USING(observation_id) WHERE m.${key}=$1 ORDER BY d.clause_key,d.clause_digest`,[projection_id||dataset_version_id])).rows;
}
