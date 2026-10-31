// Receiver persistence is one transaction. Optional context never rejects a fact.
import {createHash} from 'node:crypto';
import {lockCaptureSource} from './capture_sources.mjs';
const fault=(code,message)=>Object.assign(new Error(message),{code});
export async function receiptFor(db, exportId) {
  const row=(await db.query('SELECT receipt FROM esg.capture_exports WHERE export_id=$1',[exportId])).rows[0];
  return row?.receipt || null;
}
export async function ingestExport(db, payload) {
  const {manifest,digest,facts,relations,targets,coverage,diagnostics}=payload;
  if ((manifest?.schema_version!=='extraction-export-1.1'||manifest.authority!=='facts') || !Array.isArray(facts)) throw fault('unsupported_export','Unsupported extraction export');
  return db.transaction(async tx=>{
    await lockCaptureSource(tx,manifest);
    // The unique row serializes concurrent retries, including an uncertain RPC outcome.
    await tx.query(`INSERT INTO esg.capture_exports(export_id,input_digest,manifest,receipt,coverage,diagnostics)
      VALUES ($1,$2,$3::jsonb,'{}'::jsonb,$4::jsonb,$5::jsonb) ON CONFLICT (export_id) DO NOTHING`,
      [manifest.export_id,digest,JSON.stringify(manifest),JSON.stringify(coverage),JSON.stringify(diagnostics)]);
    const existing=(await tx.query('SELECT input_digest,receipt FROM esg.capture_exports WHERE export_id=$1 FOR UPDATE',[manifest.export_id])).rows[0];
    if (existing.input_digest!==digest) throw fault('export_identity_conflict','Export identity already belongs to different content');
    if (existing.receipt.schema_version) return existing.receipt;
    const accepted=[], rejected=[...(payload.rejected||[])];
    for (const [key,contract] of Object.entries(targets)) await tx.query(`INSERT INTO esg.capture_clause_targets(export_id,clause_key,clause_digest,contract)
      VALUES($1,$2,$3,$4::jsonb)`,[manifest.export_id,key,manifest.clause_targets[key],JSON.stringify(contract)]);
    for (const row of facts) {
      const f=row.fact,v=f.value;
      const old=(await tx.query('SELECT record_digest FROM esg.captured_facts WHERE source_fact_id=$1',[f.source_fact_id])).rows[0];
      if(old && old.record_digest!==row.digest){rejected.push({source_fact_id:f.source_fact_id,code:'fact_identity_conflict'});continue;}
      const inserted = await tx.query(`INSERT INTO esg.captured_facts(source_fact_id,source_anchor_id,document_id,ir_run_id,ir_revision,kind,
        value_raw,value_numeric,value_lower,value_upper,unit_raw,statement_raw,record_digest,record)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8::numeric,$9::numeric,$10::numeric,$11,$12,$13,$14::jsonb)
        ON CONFLICT(source_fact_id) DO NOTHING RETURNING source_fact_id`,[f.source_fact_id,f.source_anchor_id,f.document_id,f.ir_run_id,f.ir_revision,v.kind,
        v.value_raw??null,v.value_numeric??null,v.value_lower??null,v.value_upper??null,v.unit_raw??null,v.statement_raw??null,row.digest,JSON.stringify(f)]);
      const actual=(await tx.query('SELECT record_digest FROM esg.captured_facts WHERE source_fact_id=$1 FOR UPDATE',[f.source_fact_id])).rows[0];
      if(actual.record_digest!==row.digest){rejected.push({source_fact_id:f.source_fact_id,code:'fact_identity_conflict'});continue;}
      await tx.query('INSERT INTO esg.capture_export_facts VALUES($1,$2)',[manifest.export_id,f.source_fact_id]);
      if(inserted.rows.length) {
        const addEvidence=async(container,key,items)=>{for(let i=0;i<items.length;i++) await tx.query('INSERT INTO esg.captured_evidence VALUES($1,$2,$3,$4,$5::jsonb)',[f.source_fact_id,container,key,i,JSON.stringify(items[i])]);};
        await addEvidence('fact','',f.evidence);
        for(const [container,fields] of [['identity',Object.fromEntries(Object.entries(f.identity).filter(([key])=>key!=='dimensions'))],['dimension',f.identity.dimensions]]) {
          for(const [key,field] of Object.entries(fields)) {
            if(field.state==='not_read' && field.value==null) continue;
            await tx.query('INSERT INTO esg.captured_properties VALUES($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)',[f.source_fact_id,container,key,field.state,field.raw_text,JSON.stringify(field.value),JSON.stringify(field.evidence)]);
            await addEvidence(container,key,field.evidence);
          }
        }
      }
      accepted.push(f.source_fact_id);
    }
    const admitted=new Set(accepted);
    for(const link of relations) if(admitted.has(link.source_fact_id)) await tx.query('INSERT INTO esg.capture_clause_relations VALUES($1,$2,$3,$4,$5::jsonb)',[manifest.export_id,link.source_fact_id,link.clause_key,link.match,JSON.stringify(link)]);
    const receipt={schema_version:'extraction-receipt-1.0',export_id:manifest.export_id,input_digest:digest,
      receipt_id:'receipt-'+createHash('sha256').update(manifest.export_id+digest).digest('hex'),
      status:rejected.length?(accepted.length?'partial':'rejected'):'accepted',accepted_facts:accepted.length,
      rejected_facts:rejected.length,dispositions:[...accepted.map(source_fact_id=>({source_fact_id,status:'accepted'})),...rejected.map(row=>({...row,status:'rejected'}))],publication_status:'not_evaluated',received_at:new Date().toISOString()};
    await tx.query('UPDATE esg.capture_exports SET receipt=$2::jsonb WHERE export_id=$1',[manifest.export_id,JSON.stringify(receipt)]);
    return receipt;
  });
}
