import assert from 'node:assert/strict';
import { existsSync, realpathSync, mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { DATABASE_PATHS } from '../runtime/frozen_schema.mjs';
import {databaseDataDir} from '../runtime/connection.mjs';

test('canonical module owns code and uses one explicit data-root resolver', async () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  assert.equal(DATABASE_PATHS.projectRoot,path.join(root,'database'));
  assert.equal(DATABASE_PATHS.dataDir,databaseDataDir());
  assert.equal(databaseDataDir({ESG_DATA_ROOT:'/isolated/root'}),'/isolated/root/databases/capture');
  assert.equal(databaseDataDir({ESG_DATABASE_DATA_DIR:'/explicit/db',ESG_DATA_ROOT:'/unused'}),'/explicit/db');
  assert.equal(databaseDataDir({}),path.join(root,'.local/data/databases/capture'));
});

test('canonical CLI uses an explicitly isolated store', () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const sandbox=mkdtempSync(path.join(os.tmpdir(),'esg-layout-cli-'));
  try {
    const result=spawnSync(process.execPath,[path.join(root,'database/runtime/manage.mjs'),'init','--profile','capture'],{
      env:{...process.env,ESG_DATABASE_DATA_DIR:path.join(sandbox,'data')},encoding:'utf8',timeout:20000});
    assert.equal(result.status,0,result.stderr);
    const status=JSON.parse(result.stdout.trim());
    assert.equal(status.profile,'capture');assert.equal(status.ready,true);
    assert.equal(status.table_count,8);
  } finally {rmSync(sandbox,{recursive:true,force:true});}
});

test('retired production CLI has no executable implementation', () => {
  const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
  const sandbox=mkdtempSync(path.join(os.tmpdir(),'esg-retired-cli-'));
  try {
    const data=path.join(sandbox,'must-not-exist');
    assert.equal(existsSync(path.join(root,'database/scripts/db.mjs')),false);
    assert.equal(existsSync(data),false);
  } finally {rmSync(sandbox,{recursive:true,force:true});}
});
