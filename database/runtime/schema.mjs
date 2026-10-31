// The database module owns its current immutable migrations. Startup never applies them.
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const core=['capture_exports','captured_facts','capture_export_facts','capture_clause_targets','capture_clause_relations','captured_properties','captured_evidence'];
const products=['capture_projection_attempts','capture_projections','capture_product_definitions','capture_observations','capture_projection_members','capture_restatements','capture_dataset_versions','capture_dataset_projections','capture_dataset_members','capture_dataset_events'];
const sources=['capture_source_locks','capture_source_reservations'];
const migrations=[['0019_capture_ingestion.sql',core],['capture/0020_products.sql',products],['capture/0021_source_lifecycle.sql',sources]];
async function installedTables(db){return new Set((await db.query("SELECT table_name FROM information_schema.tables WHERE table_schema='esg' AND table_name=ANY($1)",[[...core,...products,...sources]])).rows.map(row=>row.table_name));}
export async function captureSchemaStatus(db){
  const tables=await installedTables(db);
  const ledger=(await db.query("SELECT to_regclass('public.capture_schema_migrations') AS name")).rows[0].name;
  const applied=ledger?new Set((await db.query('SELECT name FROM public.capture_schema_migrations')).rows.map(row=>row.name)):new Set();
  const ready=core.every(name=>tables.has(name))&&sources.every(name=>tables.has(name))&&applied.has(migrations[0][0])&&applied.has(migrations[2][0]);
  return {profile:'capture',ready,missing:core.filter(name=>!tables.has(name)),sources_ready:sources.every(name=>tables.has(name))&&applied.has(migrations[2][0]),
    products_ready:ready&&products.every(name=>tables.has(name))&&applied.has(migrations[1][0]),missing_products:products.filter(name=>!tables.has(name))};
}
export async function applyCaptureSchema(db){
  await db.exec(`CREATE SCHEMA IF NOT EXISTS esg;CREATE SCHEMA IF NOT EXISTS esg_api;
    CREATE TABLE IF NOT EXISTS public.capture_schema_migrations(name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP);`);
  for(const [name,owned] of migrations){
    const sql=await readFile(fileURLToPath(new URL('../migrations/'+name,import.meta.url)),'utf8');
    const checksum=createHash('sha256').update(sql).digest('hex');
    const previous=(await db.query('SELECT sha256 FROM public.capture_schema_migrations WHERE name=$1',[name])).rows[0];
    if(previous){if(previous.sha256!==checksum)throw new Error('Applied current migration content changed');continue;}
    const current=await installedTables(db);
    if(owned.some(table=>current.has(table)))throw new Error('Unmanaged tables are not adopted; use a clean current database');
    await db.transaction(async tx=>{await tx.exec(sql);await tx.query('INSERT INTO public.capture_schema_migrations(name,sha256) VALUES($1,$2)',[name,checksum]);});
  }
  const status=await captureSchemaStatus(db);
  if(!status.ready||!status.products_ready)throw new Error('Current database schema is incomplete');
  return status;
}
