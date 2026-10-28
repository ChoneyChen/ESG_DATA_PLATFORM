// Read the clause directory catalog for database projection.
// The database stores clause versions; the files remain their authoring source.
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

export function defaultStandardsRoot() {
  return process.env.ESG_STANDARDS_ROOT || path.join(ROOT, 'ExtractESG/standards');
}

export async function readClauseCatalog(standardsRoot = defaultStandardsRoot()) {
  const root = path.join(standardsRoot, 'clauses');
  const catalog = JSON.parse(await readFile(path.join(root, 'catalog.json'), 'utf8'));
  const clauses = [];
  for (const entry of catalog.packages || []) {
    for (const clause of entry.clauses || []) {
      const body = JSON.parse(await readFile(path.join(root, clause.path, 'clause.json'), 'utf8'));
      clauses.push({...body, package_source_digest: entry.source_digest});
    }
  }
  return { schema_version: catalog.schema_version, packages: (catalog.packages || []).length, clauses };
}
