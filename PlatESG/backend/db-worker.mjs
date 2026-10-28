import { createInterface } from 'node:readline';
import { openPlatformDatabase, applyPlatformMigrations } from './database.mjs';
import { dispatch, dictionaryFile } from './service.mjs';
import { scientificProduct } from './scientific_products.mjs';
import { projectClauseVersions, projectObservations, reconcileScientificModel } from '../../database/scripts/scientific-model.mjs';
import { readClauseCatalog } from './clause_catalog.mjs';
import { applyMigrations } from '../../database/scripts/db.mjs';
import { seedMetricDictionary, processOutbox } from '../../database/scripts/publication.mjs';

const protocolWrite = process.stdout.write.bind(process.stdout);
// Migration diagnostics cannot corrupt the JSON-lines RPC protocol.
process.stdout.write = process.stderr.write.bind(process.stderr);
const db = await openPlatformDatabase();
await applyMigrations(db);
await applyPlatformMigrations(db);
const { file_sha256, ...dictionary } = await dictionaryFile();
await seedMetricDictionary(db, dictionary);
// A restart must respect a deliberate hold on publication. Decisions and
// pending events remain durable until the configured processor is enabled.
if (process.env.PLATESG_AUTO_PUBLISH !== '0') await processOutbox(db, { limit: 100 });
async function projectModel(db, params) {
  const catalog = await readClauseCatalog(params.standards_root);
  const clauses = await projectClauseVersions(db, catalog.clauses);
  const observations = await projectObservations(db, { limit: params.limit || 100000 });
  return { clauses, observations, reconciliation: await reconcileScientificModel(db) };
}
process.stdout.write = protocolWrite;
try {
  for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    let request;
    try {
      request = JSON.parse(line);
      const params = request.params || {};
      const result = request.method === 'scientific_product' ? await scientificProduct(params)
        : request.method === 'scientific_model_project' ? await projectModel(db, params)
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
