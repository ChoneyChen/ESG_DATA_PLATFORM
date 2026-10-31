// One read projection for Console: reception, projection and publication are distinct.
import {receiptFor} from './ingestion.mjs';
import {capturedFacts} from './captures.mjs';
import {projectionStatus} from './capture_products.mjs';
import {datasetCatalog} from './capture_publication.mjs';
import {captureSchemaStatus} from '../runtime/schema.mjs';
import {fault} from '../runtime/canonical.mjs';

export async function captureProductState(db,{export_id,transform_version}) {
  const receipt=await receiptFor(db,export_id);
  if(!receipt)throw fault('not_found','Capture export has not been received');
  const facts=await capturedFacts(db,{export_id,limit:1});
  const schema=await captureSchemaStatus(db);
  const base={schema_version:'capture-product-state-1.0',export_id,receipt,captured_facts:facts.total,capture_periods:facts.periods,auto_publish:false};
  if(!schema.products_ready)return {...base,projection:{export_id,status:'schema_unavailable',projection:null,error_code:'product_schema_unavailable',retryable:false},datasets:null,published_dataset_count:null};
  const datasets=await datasetCatalog(db,{export_id,published_only:false,limit:20});
  const published=(await db.query(`SELECT count(*)::int AS n FROM esg.capture_dataset_versions d WHERE state='published' AND EXISTS(
    SELECT 1 FROM esg.capture_dataset_projections m JOIN esg.capture_projections p USING(projection_id)
    WHERE m.dataset_version_id=d.dataset_version_id AND p.export_id=$1)`,[export_id])).rows[0].n;
  return {...base,projection:await projectionStatus(db,{export_id,transform_version}),datasets,published_dataset_count:published};
}
