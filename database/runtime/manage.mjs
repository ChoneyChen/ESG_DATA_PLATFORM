// Explicit current-schema management. Application startup never installs a schema.
import {openDatabase} from './connection.mjs';
import {applyCaptureSchema,captureSchemaStatus} from './schema.mjs';
const command=process.argv[2];
if(!['init','status'].includes(command))throw new Error('Usage: manage.mjs init|status [--profile capture]');
const profileIndex=process.argv.indexOf('--profile');
if(profileIndex>=0&&process.argv[profileIndex+1]!=='capture')throw new Error('Only the current capture schema is available');
if(!process.env.ESG_DATABASE_DATA_DIR&&!process.env.ESG_DATABASE_URL)throw new Error('Choose an explicit ESG_DATABASE_DATA_DIR or ESG_DATABASE_URL');
const db=await openDatabase();
try{
  if(command==='init')await applyCaptureSchema(db);
  const state=await captureSchemaStatus(db);
  console.log(JSON.stringify({schema_version:'database-management-1.2',command,...state}));
}finally{await db.close();}
