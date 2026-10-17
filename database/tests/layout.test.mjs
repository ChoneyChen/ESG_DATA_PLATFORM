import assert from 'node:assert/strict';
import { realpathSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { DATABASE_PATHS } from '../scripts/db.mjs';

test('canonical source and compatibility aliases share code while default data stays in legacy store', async () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const legacy=path.join(root,'ExtractESG/esg_database/scripts/db.mjs');
  assert.equal(realpathSync(legacy),path.join(root,'database/scripts/db.mjs'));
  assert.equal(DATABASE_PATHS.projectRoot,path.join(root,'database'));
  if(!process.env.ESG_DATABASE_DATA_DIR) assert.equal(DATABASE_PATHS.dataDir,path.join(root,'ExtractESG/esg_database/.local/pglite'));
  const old=await import('../../ExtractESG/esg_database/scripts/db.mjs');
  assert.equal(old.DATABASE_PATHS,DATABASE_PATHS);
});

test('old symlink CLI dispatches the canonical main against an explicitly isolated store', () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const sandbox=mkdtempSync(path.join(os.tmpdir(),'esg-layout-cli-'));
  try {
    const result=spawnSync(process.execPath,[path.join(root,'ExtractESG/esg_database/scripts/db.mjs'),'init'],{
      env:{...process.env,ESG_DATABASE_DATA_DIR:path.join(sandbox,'data')},encoding:'utf8',timeout:20000});
    assert.equal(result.status,0,result.stderr);
    assert.match(result.stdout,/applied 0001_reference_data.sql/);
    assert.match(result.stdout,/esg-layout-cli-/);
  } finally {
    rmSync(sandbox,{recursive:true,force:true});
  }
});
