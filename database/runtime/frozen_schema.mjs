// Frozen publication schema helpers, never the capture initializer.
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {openPlatformDatabase, databaseDataDir} from "./connection.mjs";

const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const dataDir = databaseDataDir();
const migrationsDir = path.join(projectRoot, "migrations");

export const DATABASE_PATHS = Object.freeze({ projectRoot, dataDir, migrationsDir });

export async function openDatabase() {
  return openPlatformDatabase();
}


async function ensureMigrationLedger(db) {
  await db.exec(`
    CREATE TABLE IF NOT EXISTS public.schema_migrations (
      migration_name text PRIMARY KEY,
      applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
  `);
}

export async function applyMigrations(db) {
  await ensureMigrationLedger(db);
  const names = (await readdir(migrationsDir))
    .filter((name) => name.endsWith(".sql"))
    .sort();

  for (const name of names) {
    const existing = await db.query(
      "SELECT 1 FROM public.schema_migrations WHERE migration_name = $1",
      [name],
    );
    if (existing.rows.length > 0) {
      continue;
    }

    const sql = await readFile(path.join(migrationsDir, name), "utf8");
    await db.transaction(async (tx) => {
      await tx.exec(sql);
      await tx.query(
        "INSERT INTO public.schema_migrations (migration_name) VALUES ($1)",
        [name],
      );
    });
    process.stdout.write(`applied ${name}\n`);
  }
}

export async function readStatus(db) {
  const version = await db.query(
    "SELECT current_setting('server_version') AS server_version",
  );
  const migrations = await db.query(
    "SELECT migration_name, applied_at FROM public.schema_migrations ORDER BY migration_name",
  );
  const counts = await db.query(`
    SELECT 'source_reports' AS entity, count(*)::integer AS count FROM esg.source_report_refs
    UNION ALL
    SELECT 'concepts', count(*)::integer FROM esg.canonical_concepts
    UNION ALL
    SELECT 'disclosures', count(*)::integer FROM esg.esg_disclosures
    UNION ALL
    SELECT 'quantitative_api_rows', count(*)::integer FROM esg_api.v_quantitative_observations
    UNION ALL
    SELECT 'qualitative_api_rows', count(*)::integer FROM esg_api.v_qualitative_disclosures
    ORDER BY entity
  `);

  return {
    dataDir,
    serverVersion: version.rows[0].server_version,
    migrations: migrations.rows,
    counts: counts.rows,
  };
}
