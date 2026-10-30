// Raw reception needs no old dictionary, platform catalog or extraction tree.
import {openPlatformDatabase} from './connection.mjs';
import {captureSchemaStatus} from './schema.mjs';
import {serve} from './rpc.mjs';
import {ingestExport,receiptFor} from '../queries/ingestion.mjs';
import {capturedFacts} from '../queries/captures.mjs';

process.env.ESG_DATABASE_PROFILE='capture';
const db=await openPlatformDatabase();
const schema=await captureSchemaStatus(db);
if(!schema.ready){await db.close();throw new Error('Capture schema is not installed; run explicit capture management init');}
await serve(db,async(method,params)=>{
  if(method==='health')return {status:'ok',profile:'capture',auto_publish:false};
  if(method==='ingestion_health')return captureSchemaStatus(db);
  if(method==='ingest_export')return ingestExport(db,params);
  if(method==='export_receipt')return receiptFor(db,params.export_id);
  if(method==='capture_facts')return capturedFacts(db,params);
  throw Object.assign(new Error('Unsupported capture command'),{code:'unknown_command'});
});
