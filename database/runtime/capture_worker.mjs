// Raw reception needs no old dictionary, platform catalog or extraction tree.
import {openDatabase} from './connection.mjs';
import {captureSchemaStatus} from './schema.mjs';
import {serve} from './rpc.mjs';
import {ingestExport,receiptFor} from '../queries/ingestion.mjs';
import {capturedFacts} from '../queries/captures.mjs';
import {projectionInput,storeProjection,projectionStatus,pendingProjections,recordProjectionFailure,productInput,projectionCatalog} from '../queries/capture_products.mjs';
import {publishDataset,withdrawDataset,restatementInput,recordRestatement,datasetVersion,datasetCatalog,observationPage,productDefinitions,publicSource,observationDetail,datasetSources} from '../queries/capture_publication.mjs';
import {captureProductState} from '../queries/capture_workflow.mjs';
import {sourceReferences,reserveSource,updateSourceReservation} from '../queries/capture_sources.mjs';

process.env.ESG_DATABASE_PROFILE='capture';
const db=await openDatabase();
const schema=await captureSchemaStatus(db);
if(!schema.ready){await db.close();throw new Error('Capture schema is not installed; run explicit capture management init');}
await serve(db,async(method,params)=>{
  if(method==='health')return {status:'ok',profile:'capture',auto_publish:false};
  if(method==='ingestion_health')return captureSchemaStatus(db);
  if(method==='ingest_export')return ingestExport(db,params);
  if(method==='export_receipt')return receiptFor(db,params.export_id);
  if(method==='capture_facts')return capturedFacts(db,params);
  if(method==='capture_product_state')return captureProductState(db,params);
  if(method==='source_references')return sourceReferences(db,params);
  if(method==='reserve_source')return reserveSource(db,params);
  if(method==='update_source_reservation')return updateSourceReservation(db,params);
  const products={projection_input:projectionInput,store_projection:storeProjection,projection_status:projectionStatus,
    pending_projections:pendingProjections,record_projection_failure:recordProjectionFailure,product_input:productInput,
    publish_dataset:publishDataset,withdraw_dataset:withdrawDataset,restatement_input:restatementInput,record_restatement:recordRestatement,
    dataset_version:datasetVersion,dataset_catalog:datasetCatalog,observation_page:observationPage,projection_catalog:projectionCatalog,product_definitions:productDefinitions,public_source:publicSource,
    observation_detail:observationDetail,dataset_sources:datasetSources};
  if(products[method]){
    if(!(await captureSchemaStatus(db)).products_ready)throw Object.assign(new Error('Install the product schema explicitly'),{code:'product_schema_unavailable'});
    return products[method](db,params);
  }
  throw Object.assign(new Error('Unsupported capture command'),{code:'unknown_command'});
});
