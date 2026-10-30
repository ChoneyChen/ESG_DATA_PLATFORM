import assert from 'node:assert/strict';
import { existsSync, realpathSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { DATABASE_PATHS } from '../scripts/db.mjs';

test('canonical module owns code and retains the registered runtime data path', async () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  assert.equal(DATABASE_PATHS.projectRoot,path.join(root,'database'));
  if(!process.env.ESG_DATABASE_DATA_DIR) {const old=path.join(root,'ExtractESG/esg_database/.local/pglite');assert.equal(DATABASE_PATHS.dataDir,existsSync(old)?old:path.join(root,'database/.local/pglite'));}
});

test('canonical CLI uses an explicitly isolated store', () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const sandbox=mkdtempSync(path.join(os.tmpdir(),'esg-layout-cli-'));
  try {
    const result=spawnSync(process.execPath,[path.join(root,'database/scripts/db.mjs'),'init'],{
      env:{...process.env,ESG_DATABASE_DATA_DIR:path.join(sandbox,'data')},encoding:'utf8',timeout:20000});
    assert.equal(result.status,0,result.stderr);
    assert.match(result.stdout,/applied 0001_reference_data.sql/);
    assert.match(result.stdout,/esg-layout-cli-/);
  } finally {rmSync(sandbox,{recursive:true,force:true});}
});

test('retired import CLI refuses before opening any store', () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const sandbox=mkdtempSync(path.join(os.tmpdir(),'esg-retired-cli-'));
  try {
    const data=path.join(sandbox,'must-not-exist');
    const result=spawnSync(process.execPath,[path.join(root,'database/scripts/db.mjs'),'import'],{env:{...process.env,ESG_DATABASE_DATA_DIR:data},encoding:'utf8'});
    assert.notEqual(result.status,0);assert.match(result.stderr,/production import is retired/);assert.equal(existsSync(data),false);
  } finally {rmSync(sandbox,{recursive:true,force:true});}
});
