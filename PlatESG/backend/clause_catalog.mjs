// The current clause source no longer has legacy package/version projections.
// Keep database writes behind an explicitly implemented consumer contract.
import path from 'node:path';
import {fileURLToPath} from 'node:url';
export function defaultStandardsRoot(){
  return process.env.ESG_CLAUSE_ROOT || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../ExtractESG/standard');
}
export async function readClauseCatalog(){
  const error=new Error('Independent clause contracts use clause-contract-1.0. Legacy package-version database projection is retired; no projection was written.');
  error.code='clause_projection_retired';throw error;
}
