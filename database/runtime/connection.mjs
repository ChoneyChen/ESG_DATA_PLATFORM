import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
import { mkdir } from 'node:fs/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const requireResultDB = createRequire(path.join(root, 'package.json'));

export function databaseDataDir(environment = process.env) {
  return path.resolve(environment.ESG_DATABASE_DATA_DIR ||
    path.join(environment.ESG_DATA_ROOT || path.join(root, '../.local/data'), 'databases/capture'));
}

// Both runtimes expose one transaction API. No SQL is passed over HTTP.
// A PostgreSQL transaction always uses the same checked-out client.
export async function openDatabase() {
  const connectionUrl=process.env.ESG_DATABASE_URL;
  if (connectionUrl) {
    const pgModule = await import(pathToFileURL(requireResultDB.resolve('pg')).href);
    const { Pool } = pgModule.default || pgModule;
    const pool = new Pool({ connectionString: connectionUrl, max: 5 });
    const wrap = client => ({
      query: (sql, params = []) => client.query(sql, params),
      exec: sql => client.query(sql),
    });
    return {
      ...wrap(pool),
      async transaction(fn) {
        const client = await pool.connect();
        try {
          await client.query('BEGIN');
          const result = await fn(wrap(client));
          await client.query('COMMIT');
          return result;
        } catch (error) {
          await client.query('ROLLBACK');
          throw error;
        } finally { client.release(); }
      },
      close: () => pool.end(),
    };
  }
  const { PGlite } = await import(pathToFileURL(requireResultDB.resolve('@electric-sql/pglite')).href);
  const dataDir = databaseDataDir();
  await mkdir(path.dirname(dataDir), { recursive: true });
  return PGlite.create(dataDir);
}
