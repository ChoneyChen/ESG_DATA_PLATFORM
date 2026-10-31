// Immutable export membership is the query snapshot. No producer directories.
import {createHash} from 'node:crypto';

const fault=(code,message)=>Object.assign(new Error(message),{code});
const hash=value=>createHash('sha256').update(JSON.stringify(value)).digest('hex');
const PERIOD=`CASE
 WHEN jsonb_typeof(f.record#>'{identity,reporting_period,value}')='string'
 THEN f.record#>>'{identity,reporting_period,value}'
 WHEN f.record#>>'{identity,reporting_period,value,kind}'='quarter'
 THEN COALESCE((f.record#>>'{identity,reporting_period,value,year}')||' Q'||(f.record#>>'{identity,reporting_period,value,quarter}'),f.record#>>'{identity,reporting_period,value,raw}')
 ELSE COALESCE(f.record#>>'{identity,reporting_period,value,raw}',f.record#>>'{identity,reporting_period,value,year}') END`;

export async function capturedFacts(db,{export_id,limit=200,cursor=null,period=null,clause_key=null,relation=null}={}) {
  if (!Number.isInteger(limit)||limit<1||limit>1000) throw fault('invalid_query','Page size must be between 1 and 1000');
  if (relation && !['match','different','uncertain'].includes(relation)) throw fault('invalid_query','Unknown target relation');
  const header=(await db.query('SELECT input_digest FROM esg.capture_exports WHERE export_id=$1',[export_id])).rows[0];
  if(!header) throw fault('not_found','Capture export does not exist');
  const query=hash([export_id,header.input_digest,period,clause_key,relation,limit]);
  let after='';
  if(cursor) {
    try {
      const value=JSON.parse(Buffer.from(cursor,'base64url').toString('utf8'));
      if(value.query!==query||typeof value.after!=='string') throw new Error();
      after=value.after;
    } catch {throw fault('invalid_cursor','Cursor belongs to another capture query');}
  }
  const params=[export_id],conditions=['e.export_id=$1'];
  if(clause_key||relation) {
    const links=['r.export_id=e.export_id','r.source_fact_id=f.source_fact_id'];
    if(clause_key){params.push(clause_key);links.push(`r.clause_key=$${params.length}`);}
    if(relation){params.push(relation);links.push(`r.relation=$${params.length}`);}
    conditions.push(`EXISTS (SELECT 1 FROM esg.capture_clause_relations r WHERE ${links.join(' AND ')})`);
  }
  const base=`FROM esg.captured_facts f JOIN esg.capture_export_facts e USING(source_fact_id) WHERE ${conditions.join(' AND ')}`;
  const periods=(await db.query(`SELECT COALESCE(${PERIOD},'unknown') AS period,count(*)::int AS facts ${base} GROUP BY 1 ORDER BY 1`,params)).rows;
  if(period){params.push(period);conditions.push(`COALESCE(${PERIOD},'unknown')=$${params.length}`);}
  const filtered=`FROM esg.captured_facts f JOIN esg.capture_export_facts e USING(source_fact_id) WHERE ${conditions.join(' AND ')}`;
  const total=(await db.query(`SELECT count(*)::int AS n ${filtered}`,params)).rows[0].n;
  const items=(await db.query(`SELECT f.record,f.value_numeric::text AS exact_numeric ${filtered}
    AND f.source_fact_id>$${params.length+1} ORDER BY f.source_fact_id LIMIT $${params.length+2}`,[...params,after,limit+1])).rows;
  const more=items.length>limit;
  if(more)items.pop();
  return {schema_version:'capture-fact-page-1.0',export_id,query_digest:query,total,returned:items.length,periods,items,
    next_cursor:more?Buffer.from(JSON.stringify({query,after:items.at(-1).record.source_fact_id})).toString('base64url'):null};
}
