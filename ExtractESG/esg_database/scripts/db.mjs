import { existsSync, rmSync } from "node:fs";
import { mkdir, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { PGlite } from "@electric-sql/pglite";

const projectRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const dataDir = path.join(projectRoot, ".local", "pglite");
const migrationsDir = path.join(projectRoot, "migrations");
const demoFile = path.join(projectRoot, "examples", "0001_demo_disclosures.sql");

export async function openDatabase() {
  await mkdir(path.dirname(dataDir), { recursive: true });
  return PGlite.create(dataDir);
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

export async function loadDemo(db) {
  const sql = await readFile(demoFile, "utf8");
  await db.transaction(async (tx) => {
    await tx.exec(sql);
  });
  process.stdout.write("loaded examples/0001_demo_disclosures.sql\n");
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

async function printStatus(db) {
  const status = await readStatus(db);
  process.stdout.write(`${JSON.stringify(status, null, 2)}\n`);
}

async function main() {
  const command = process.argv[2] ?? "status";

  if (command === "reset") {
    if (existsSync(dataDir)) {
      rmSync(dataDir, { recursive: true, force: true });
      process.stdout.write(`removed ${dataDir}\n`);
    } else {
      process.stdout.write("database is already absent\n");
    }
    return;
  }

  const db = await openDatabase();
  try {
    if (command === "init") {
      await applyMigrations(db);
      await printStatus(db);
      return;
    }
    if (command === "demo") {
      await applyMigrations(db);
      await loadDemo(db);
      await printStatus(db);
      return;
    }
    if (command === "status") {
      await ensureMigrationLedger(db);
      await printStatus(db);
      return;
    }
    throw new Error(`Unknown command: ${command}`);
  } finally {
    await db.close();
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (import.meta.url === invokedPath) {
  main().catch((error) => {
    process.stderr.write(`${error.stack ?? error.message}\n`);
    process.exitCode = 1;
  });
}

