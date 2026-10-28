// Read-only scientific table and CSV/Parquet products for the platform worker.
import {openPlatformDatabase} from './database.mjs';

const TABLES = {
  observations: 'scientific_observations', observation_dimensions: 'scientific_observation_dimensions',
  observation_evidence: 'scientific_observation_evidence', observation_sources: 'scientific_observation_sources',
  metric_definitions: 'scientific_metric_definitions', report_publication_members: 'scientific_report_publication_members',
  coverage_decisions: 'scientific_coverage_decisions',
};

export async function scientificExport(db, {table, limit = 100000} = {}) {
  if (!Object.hasOwn(TABLES, table)) throw new Error('Unknown scientific table');
  if (!Number.isInteger(limit) || limit < 1 || limit > 100000) throw new Error('Scientific export limit must be between 1 and 100000');
  const rows = (await db.query(`SELECT * FROM esg_api.${TABLES[table]} LIMIT $1`, [limit])).rows;
  return {schema_version: 'platesg-scientific-product-v1', table, row_count: rows.length, rows, limited: rows.length === limit};
}

function csvValue(value) {
  const raw = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const safe = typeof raw === 'string' && !['value_numeric','lower_bound','upper_bound'].includes(raw) && /^[=+\-@]/.test(raw) ? `'${raw}` : raw;
  return `"${String(safe).replaceAll('"', '""')}"`;
}

export function scientificCsv(result) {
  const fields = result.rows.length ? Object.keys(result.rows[0]) : [];
  return '﻿' + [fields.map(csvValue).join(','), ...result.rows.map(row => fields.map(field => csvValue(row[field])).join(','))].join('\n') + '\n';
}

export async function scientificProduct({table, format = 'json', limit = 100000} = {}) {
  const db = await openPlatformDatabase();
  try {
    const result = await scientificExport(db, {table, limit});
    if (format === 'json') return result;
    if (format === 'csv') return {content_base64: Buffer.from(scientificCsv(result), 'utf8').toString('base64')};
    if (format === 'parquet') {
      const {execFileSync} = await import('node:child_process');
      const output = execFileSync(process.env.PLATESG_PYTHON || 'python3', ['-I', '-c',
        'import io,json,sys; import pyarrow as pa; import pyarrow.parquet as pq; rows=json.load(sys.stdin); '
        + 'fields=list(rows[0]) if rows else []; schema=pa.schema([pa.field(k,pa.string()) for k in fields]); '
        + 'data=[{k:None if v is None else str(v) for k,v in row.items()} for row in rows]; '
        + 'out=io.BytesIO();pq.write_table(pa.Table.from_pylist(data,schema=schema),out);sys.stdout.buffer.write(out.getvalue())'],
        {input: JSON.stringify(result.rows), maxBuffer: 1024 * 1024 * 256});
      return {content_base64: output.toString('base64')};
    }
    throw new Error('Unsupported scientific export format');
  } finally { await db.close(); }
}
