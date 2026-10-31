import assert from 'node:assert/strict';
import {existsSync,mkdtempSync,rmSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {databaseDataDir} from '../runtime/connection.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
test('one current data resolver ignores old platform locations',()=>{
  assert.equal(databaseDataDir({ESG_DATA_ROOT:'/isolated/root',PLATESG_DATA_DIR:'/retired/store'}),'/isolated/root/databases/capture');
  assert.equal(databaseDataDir({ESG_DATABASE_DATA_DIR:'/explicit/data'}),'/explicit/data');
});
test('management installs only the current schema in an explicit isolated store',()=>{
  const sandbox=mkdtempSync(path.join(os.tmpdir(),'esg-current-schema-'));
  try{
    const data=path.join(sandbox,'data');
    const environment={...process.env,ESG_DATABASE_DATA_DIR:data};delete environment.ESG_DATABASE_URL;
    const result=spawnSync(process.execPath,[path.join(root,'database/runtime/manage.mjs'),'init'],{env:environment,encoding:'utf8',timeout:20000});
    assert.equal(result.status,0,result.stderr);const status=JSON.parse(result.stdout.trim());
    assert.equal(status.ready,true);assert.equal(status.products_ready,true);
  }finally{rmSync(sandbox,{recursive:true,force:true});}
});
test('retired schema profile is rejected before opening any database',()=>{
  const sandbox=mkdtempSync(path.join(os.tmpdir(),'esg-no-legacy-'));
  try{
    const data=path.join(sandbox,'must-not-exist');
    const result=spawnSync(process.execPath,[path.join(root,'database/runtime/manage.mjs'),'init','--profile','frozen_publication'],{env:{...process.env,ESG_DATABASE_DATA_DIR:data},encoding:'utf8',timeout:20000});
    assert.notEqual(result.status,0);assert.equal(existsSync(data),false);
  }finally{rmSync(sandbox,{recursive:true,force:true});}
});
