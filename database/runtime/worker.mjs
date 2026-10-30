import { createInterface } from 'node:readline';
import { openPlatformDatabase } from './connection.mjs';
import { dispatch } from '../queries/service.mjs';
import { scientificProduct } from '../queries/scientific_products.mjs';
import { reconcileScientificModel } from '../scripts/scientific-model.mjs';
import { processOutbox } from '../scripts/publication.mjs';
import {ingestExport, receiptFor} from '../queries/ingestion.mjs';
import {capturedFacts} from '../queries/captures.mjs';

const protocolWrite = process.stdout.write.bind(process.stdout);
// Migration diagnostics cannot corrupt the JSON-lines RPC protocol.
process.stdout.write = process.stderr.write.bind(process.stderr);
const db = await openPlatformDatabase();
// Schema installation and dictionary registration belong to the explicit management command.
const installed = await db.query("SELECT to_regclass('esg.dictionary_membership') AS schema_name");
if (!installed.rows[0].schema_name) {
  await db.close();
  throw new Error("Database schema is not installed; run database management init explicitly");
}
// A restart must respect a deliberate hold on publication. Decisions and
// pending events remain durable until the configured processor is enabled.
if (process.env.PLATESG_AUTO_PUBLISH === '1') await processOutbox(db, { limit: 100 });
process.stdout.write = protocolWrite;
try {
  for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    let request;
    try {
      request = JSON.parse(line);
      const params = request.params || {};
      const result = request.method === 'ingest_export' ? await ingestExport(db, params)
        : request.method === 'ingestion_health' ? {ready:!!(await db.query("SELECT to_regclass('esg.capture_exports') AS name")).rows[0].name}
        : request.method === 'capture_facts' ? await capturedFacts(db,params)
        : request.method === 'export_receipt' ? await receiptFor(db, params.export_id)
        : request.method === 'scientific_product' ? await scientificProduct(params, db)
        : request.method === 'scientific_model_reconcile' ? await reconcileScientificModel(db)
        : await dispatch(db, request.method, params);
      protocolWrite(JSON.stringify({ id: request.id, result }) + '\n');
    } catch (error) {
      protocolWrite(JSON.stringify({ id: request?.id, error: {
        code: error.code || 'database_error', message: error.message,
      } }) + '\n');
    }
  }
} finally { await db.close(); }
