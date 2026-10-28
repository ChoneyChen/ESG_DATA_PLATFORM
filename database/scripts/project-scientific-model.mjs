#!/usr/bin/env node
/**
 * Re-project the scientific model from the clause catalogue.
 *
 *   node database/scripts/project-scientific-model.mjs           # project, then reconcile
 *   node database/scripts/project-scientific-model.mjs --check   # reconcile only
 *
 * The publication service keeps the local PGlite directory open, so stop it
 * first (the projection writes rows; it never migrates or resets the database).
 */
import {openPlatformDatabase} from '../../PlatESG/backend/database.mjs';
import {readClauseCatalog} from '../../PlatESG/backend/clause_catalog.mjs';
import {projectClauseVersions, projectObservations, reconcileScientificModel} from './scientific-model.mjs';

const checkOnly = process.argv.includes('--check');
const db = await openPlatformDatabase();
try {
  if (!checkOnly) {
    const {clauses} = await readClauseCatalog();
    const projected = await db.transaction(tx => projectClauseVersions(tx, clauses));
    const observations = await db.transaction(tx => projectObservations(tx));
    process.stdout.write(`${JSON.stringify({clauses: clauses.length, ...projected, observations})}\n`);
  }
  process.stdout.write(`${JSON.stringify(await reconcileScientificModel(db))}\n`);
} finally {
  await db.close?.();
}
