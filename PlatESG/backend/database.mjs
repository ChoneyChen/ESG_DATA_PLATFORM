import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
import { mkdir, readFile, readdir } from 'node:fs/promises';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const requireResultDB = createRequire(path.join(root, '../database/package.json'));

// Both runtimes expose one transaction API. No SQL is passed over HTTP.
// A PostgreSQL transaction always uses the same checked-out client.
export async function openPlatformDatabase() {
  if (process.env.PLATESG_DATABASE_URL) {
    const pgModule = await import(pathToFileURL(requireResultDB.resolve('pg')).href);
    const { Pool } = pgModule.default || pgModule;
    const pool = new Pool({ connectionString: process.env.PLATESG_DATABASE_URL, max: 5 });
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
  const dataDir = process.env.PLATESG_DATA_DIR || path.join(root, '.local/data');
  await mkdir(path.dirname(dataDir), { recursive: true });
  return PGlite.create(dataDir);
}

export async function applyPlatformMigrations(db) {
  await db.exec(`CREATE TABLE IF NOT EXISTS public.plat_schema_migrations (
    migration_name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)`);
  const directory = path.join(root, 'backend/migrations');
  for (const name of (await readdir(directory)).filter(x => x.endsWith('.sql')).sort()) {
    if ((await db.query('SELECT 1 FROM public.plat_schema_migrations WHERE migration_name=$1', [name])).rows.length) continue;
    const sql = await readFile(path.join(directory, name), 'utf8');
    await db.transaction(async tx => {
      await tx.exec(sql);
      await tx.query('INSERT INTO public.plat_schema_migrations(migration_name) VALUES($1)', [name]);
    });
  }
}
