// Explicit database management. Application startup never installs a schema.
import {openPlatformDatabase, applyPlatformMigrations} from './connection.mjs';
import {applyMigrations} from '../scripts/db.mjs';
import {seedMetricDictionary} from '../scripts/publication.mjs';
import {dictionaryFile} from '../queries/service.mjs';

const command = process.argv[2];
if (!['init','status'].includes(command)) throw new Error('Usage: manage.mjs init|status');
if (!process.env.ESG_DATABASE_DATA_DIR && !process.env.ESG_DATABASE_URL && !process.env.PLATESG_DATA_DIR && !process.env.PLATESG_DATABASE_URL)
  throw new Error('Configure the intended database explicitly before management');
const db = await openPlatformDatabase();
try {
  if (command === 'init') {
    await applyMigrations(db);
    await applyPlatformMigrations(db);
    const {file_sha256, ...dictionary} = await dictionaryFile();
    await seedMetricDictionary(db, dictionary);
  }
  const tables=await db.query("SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema IN ('esg','esg_api','plat') ORDER BY table_schema,table_name");
  console.log(JSON.stringify({schema_version:'database-management-1.0',command,table_count:tables.rows.length}));
} finally {await db.close();}
