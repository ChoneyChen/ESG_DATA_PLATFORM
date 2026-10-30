// Capture schema ownership. Only the explicit management command calls install.
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const tables=['capture_exports','captured_facts','capture_export_facts','capture_clause_targets',
  'capture_clause_relations','captured_properties','captured_evidence'];
export async function captureSchemaStatus(db) {
  const rows=(await db.query("SELECT table_name FROM information_schema.tables WHERE table_schema='esg' AND table_name=ANY($1)",[tables])).rows;
  const present=new Set(rows.map(row=>row.table_name));
  return {profile:'capture',ready:tables.every(table=>present.has(table)),missing:tables.filter(table=>!present.has(table))};
}

export async function applyCaptureSchema(db) {
  await db.exec(`CREATE SCHEMA IF NOT EXISTS esg;CREATE SCHEMA IF NOT EXISTS esg_api;
    CREATE TABLE IF NOT EXISTS public.capture_schema_migrations(name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP);`);
  const name='0019_capture_ingestion.sql';
  const sql=await readFile(fileURLToPath(new URL('../migrations/'+name,import.meta.url)),'utf8');
  const checksum=createHash('sha256').update(sql).digest('hex');
  const prior=(await db.query('SELECT sha256 FROM public.capture_schema_migrations WHERE name=$1',[name])).rows[0];
  if(prior && prior.sha256!==checksum) throw new Error('Applied capture migration content changed');
  if(!prior) {
    const status=await captureSchemaStatus(db);
    if(status.missing.length && status.missing.length!==tables.length) throw new Error('Capture schema is incomplete; inspect it before applying a migration');
    if(status.ready) {
      const ledger=(await db.query("SELECT to_regclass('public.schema_migrations') AS name")).rows[0].name;
      const known=ledger && (await db.query('SELECT 1 FROM public.schema_migrations WHERE migration_name=$1',[name])).rows.length;
      if(!known) throw new Error('Existing capture tables have no known migration; inspect before adopting');
    }
    await db.transaction(async tx=>{
      if(!status.ready)await tx.exec(sql);
      await tx.query('INSERT INTO public.capture_schema_migrations(name,sha256) VALUES($1,$2)',[name,checksum]);
    });
  }
  const status=await captureSchemaStatus(db);
  if(!status.ready)throw new Error('Capture schema is incomplete');
  return status;
}
