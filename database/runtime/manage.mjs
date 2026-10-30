// Explicit database management. Application startup never installs a schema.
import {openPlatformDatabase, applyPlatformMigrations} from './connection.mjs';
import {applyCaptureSchema,captureSchemaStatus} from './schema.mjs';

const command = process.argv[2];
if (!['init','status'].includes(command)) throw new Error('Usage: manage.mjs init|status');
const profileIndex=process.argv.indexOf('--profile');
const profile=profileIndex<0?'capture':process.argv[profileIndex+1];
if(!['capture','frozen_publication'].includes(profile))throw new Error('Choose capture or frozen_publication schema profile');
if(profile==='capture'&&!process.env.ESG_DATABASE_DATA_DIR&&!process.env.ESG_DATABASE_URL)
  throw new Error('Capture management requires an explicit ESG_DATABASE_DATA_DIR or ESG_DATABASE_URL');
process.env.ESG_DATABASE_PROFILE=profile;
if (!process.env.ESG_DATABASE_DATA_DIR && !process.env.ESG_DATABASE_URL && !process.env.PLATESG_DATA_DIR && !process.env.PLATESG_DATABASE_URL)
  throw new Error('Configure the intended database explicitly before management');
const db = await openPlatformDatabase();
try {
  if (command === 'init') {
    if(profile==='capture') await applyCaptureSchema(db);
    else {
      const {applyMigrations}=await import('./frozen_schema.mjs');
      const {seedMetricDictionary}=await import('../scripts/publication.mjs');
      const {dictionaryFile}=await import('../queries/service.mjs');
      await applyMigrations(db);
      await applyPlatformMigrations(db);
      const {file_sha256, ...dictionary} = await dictionaryFile();
      await seedMetricDictionary(db, dictionary);
    }
  }
  const tables=await db.query("SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema IN ('esg','esg_api','plat') ORDER BY table_schema,table_name");
  console.log(JSON.stringify({schema_version:'database-management-1.1',command,profile,table_count:tables.rows.length,
    ...(profile==='capture'?await captureSchemaStatus(db):{})}));
} finally {await db.close();}
