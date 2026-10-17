/** Approved historical import provenance; never activates a publication dictionary. */
import {readFile,realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const platformRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const catalogRoot=path.join(platformRoot,'catalog');
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)
  ?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
const fail=(message,code='import_catalog_invalid')=>{throw Object.assign(new Error(message),{code});};
const digest=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const text=value=>typeof value==='string'&&value.trim().length>0;

async function catalogArtifact(ref,expected) {
  if(!text(ref)||path.isAbsolute(ref)||ref.split(/[\\/]/).includes('..')||!digest(expected))
    fail('Historical catalog artifact identity is invalid');
  const root=await realpath(catalogRoot),target=await realpath(path.join(root,ref));
  if(!target.startsWith(root+path.sep))fail('Historical catalog artifact is outside the allowlist root');
  const bytes=await readFile(target);
  if(bytes.length>5_000_000||sha(bytes)!==expected)fail('Historical catalog raw-byte digest differs','import_catalog_digest_mismatch');
  return JSON.parse(bytes.toString('utf8'));
}

export function describeImportCatalog(dictionary,profile) {
  if(!text(dictionary.dictionary_version)||dictionary.profile_id!==profile.profile_id
    ||dictionary.profile_version!==profile.profile_version||!Array.isArray(dictionary.metrics)
    ||!Array.isArray(profile.source_datapoint_ids)||new Set(profile.source_datapoint_ids).size!==profile.source_datapoint_ids.length)
    fail('Dictionary and original profile identities differ');
  const originals=dictionary.metrics.filter(m=>(m.clause_kind||'original_datapoint')==='original_datapoint');
  if(new Set(originals.map(m=>m.source_datapoint_id)).size!==originals.length
    ||new Set(dictionary.metrics.map(m=>m.metric_id)).size!==dictionary.metrics.length)
    fail('Import dictionary contains duplicate concepts or original IDs');
  const ready=originals.filter(m=>m.readiness==='ready');
  const bindings={};
  for(const metric of ready) {
    const b=metric.standard_binding;
    if(!profile.source_datapoint_ids.includes(metric.source_datapoint_id)||!b
      ||![b.package_id,b.package_version,b.metric_id].every(text)||!digest(b.digest))
      fail('Ready historical original lacks its profile membership or exact package binding');
    const existing=bindings[b.package_id];
    if(existing&&(existing.package_version!==b.package_version||existing.digest!==b.digest))
      fail('Historical package has conflicting bindings');
    bindings[b.package_id]={package_version:b.package_version,digest:b.digest};
  }
  return {ready_source_datapoint_ids:ready.map(m=>m.source_datapoint_id),package_bindings:bindings};
}

export async function loadAllowedImportContract(contractId) {
  const index=JSON.parse(await readFile(path.join(catalogRoot,'import-contracts/index.json'),'utf8'));
  const allowed=index.releases?.find(item=>item.contract_id===contractId);
  if(!allowed)fail('Historical import contract is not allowlisted','import_catalog_unregistered');
  const release=await catalogArtifact(allowed.artifact_ref,allowed.sha256);
  if(release.schema_version!=='import-contract-release-v1'||release.contract_id!==contractId
    ||release.permission!=='import_only')fail('Historical import release identity differs');
  const dictionary=await catalogArtifact(release.dictionary.artifact_ref,release.dictionary.sha256);
  const profile=await catalogArtifact(release.profile.artifact_ref,release.profile.sha256);
  if(dictionary.dictionary_version!==release.dictionary.version||profile.profile_id!==release.profile.id
    ||profile.profile_version!==release.profile.version)fail('Historical artifact version differs');
  const described=describeImportCatalog(dictionary,profile);
  // All ready bindings must still resolve to their immutable compiled package.
  for(const [packageId,binding] of Object.entries(described.package_bindings)) {
    const compiled=JSON.parse(await readFile(path.join(platformRoot,'../ExtractESG/standard_packages/dist',packageId,binding.package_version,'package.json'),'utf8'));
    if(compiled.compilation.source_digest!==binding.digest)fail('Historical compiled package digest differs');
    for(const metric of dictionary.metrics.filter(m=>m.readiness==='ready'&&m.standard_binding?.package_id===packageId)) {
      const target=compiled.metrics.find(m=>m.metric_id===metric.standard_binding.metric_id);
      if(target?.source_datapoint_id!==metric.source_datapoint_id)fail('Historical original ID differs from compiled package');
    }
  }
  return {contract_id:contractId,permission:'import_only',release_sha256:allowed.sha256,
    dictionary_version:dictionary.dictionary_version,dictionary_file_sha256:release.dictionary.sha256,
    profile_id:profile.profile_id,profile_version:profile.profile_version,profile_file_sha256:release.profile.sha256,
    ...described,dictionary_snapshot:dictionary,profile_snapshot:profile,release_snapshot:release};
}

export async function registerImportContract(db,input) {
  if(Object.keys(input).some(key=>!['contract_id','expected_release_sha256','operator_ref','reason'].includes(key))
    ||!text(input.operator_ref)||!text(input.reason)||input.reason.length>4000)
    fail('Import-only registration requires an authenticated operator and reason');
  const release=await loadAllowedImportContract(input.contract_id);
  if(input.expected_release_sha256!==release.release_sha256)
    fail('Expected import release digest differs','import_catalog_digest_mismatch');
  return db.transaction(async tx=>{
    await tx.query(`INSERT INTO plat.import_contract_releases(contract_id,permission,release_sha256,dictionary_version,
      dictionary_file_sha256,profile_id,profile_version,profile_file_sha256,ready_source_datapoint_ids,package_bindings,
      dictionary_snapshot,profile_snapshot,release_snapshot,operator_ref,reason)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) ON CONFLICT(contract_id) DO NOTHING`,
      [release.contract_id,release.permission,release.release_sha256,release.dictionary_version,release.dictionary_file_sha256,
        release.profile_id,release.profile_version,release.profile_file_sha256,release.ready_source_datapoint_ids,
        release.package_bindings,release.dictionary_snapshot,release.profile_snapshot,release.release_snapshot,input.operator_ref,input.reason]);
    const stored=(await tx.query('SELECT * FROM plat.import_contract_releases WHERE contract_id=$1',[release.contract_id])).rows[0];
    if(Object.keys(release).some(key=>stable(stored[key])!==stable(release[key])))fail('Immutable import-only registration differs','import_catalog_conflict');
    return {contract_id:stored.contract_id,permission:stored.permission,release_sha256:stored.release_sha256,
      dictionary_version:stored.dictionary_version,registered_by:stored.operator_ref,reason:stored.reason,created_at:stored.created_at};
  });
}

export async function listImportContracts(db) {
  const index=JSON.parse(await readFile(path.join(catalogRoot,'import-contracts/index.json'),'utf8'));
  return {allowed:index.releases.map(({contract_id,sha256})=>({contract_id,release_sha256:sha256,permission:'import_only'})),
    items:(await db.query(`SELECT contract_id,permission,release_sha256,dictionary_version,dictionary_file_sha256,
      profile_id,profile_version,profile_file_sha256,ready_source_datapoint_ids,package_bindings,operator_ref,reason,created_at
      FROM plat.import_contract_releases ORDER BY created_at,contract_id`)).rows};
}

/** Frozen major scopes are checked against current artifacts or a separately approved historical import release. */
export async function resolveImportCatalog(db,scope,current) {
  if(!digest(scope.dictionary_digest))fail('Frozen import dictionary requires its original file SHA','import_catalog_digest_missing');
  if(scope.dictionary_version===current.dictionary?.dictionary_version) {
    if(scope.dictionary_digest!==current.dictionary_file_sha256)fail('Frozen dictionary raw-byte digest differs','import_catalog_digest_mismatch');
    return {...current,...describeImportCatalog(current.dictionary,current.profile),permission:'current_import',contract_id:null};
  }
  const stored=(await db.query(`SELECT * FROM plat.import_contract_releases WHERE dictionary_version=$1
    AND dictionary_file_sha256=$2 AND permission='import_only'`,[scope.dictionary_version,scope.dictionary_digest])).rows[0];
  if(!stored)fail('Historical import dictionary is not registered with its original SHA','import_catalog_unregistered');
  // Verify allowlisted artifact bytes again; a registry entry is not permission to edit files.
  const allowed=await loadAllowedImportContract(stored.contract_id);
  if(Object.keys(allowed).some(key=>stable(stored[key])!==stable(allowed[key])))fail('Registered historical import artifacts differ','import_catalog_conflict');
  return {dictionary:stored.dictionary_snapshot,profile:stored.profile_snapshot,
    dictionary_file_sha256:stored.dictionary_file_sha256,profile_file_sha256:stored.profile_file_sha256,
    ready_source_datapoint_ids:stored.ready_source_datapoint_ids,package_bindings:stored.package_bindings,
    permission:'import_only',contract_id:stored.contract_id};
}

export function validateMajorImportScope(scope,catalog,bundle,originalIds) {
  if(scope.profile_id!==catalog.profile.profile_id||scope.profile_version!==catalog.profile.profile_version
    ||scope.profile_digest!==catalog.profile_file_sha256)fail('Frozen original profile identity or raw-byte digest differs','import_catalog_digest_mismatch');
  if(scope.source_datapoint_ids.some(id=>!catalog.profile.source_datapoint_ids.includes(id)
    ||!catalog.ready_source_datapoint_ids.includes(id)))fail('Major scope contains an unbuilt or non-major historical original','import_scope_mismatch');
  const packageDigests=scope.package_digests;
  if(!packageDigests||Array.isArray(packageDigests)||typeof packageDigests!=='object')fail('Frozen major scope lacks package bindings','import_scope_mismatch');
  for(const [id,value] of Object.entries(packageDigests))
    if(catalog.package_bindings[id]?.digest!==value)fail('Frozen major scope package digest differs','standard_snapshot_mismatch');
  for(const id of scope.source_datapoint_ids) {
    const metric=catalog.dictionary.metrics.find(m=>m.source_datapoint_id===id&&(m.clause_kind||'original_datapoint')==='original_datapoint');
    if(packageDigests[metric.standard_binding.package_id]!==metric.standard_binding.digest)
      fail('Frozen major scope omits an original package binding','standard_snapshot_mismatch');
  }
  const binding=catalog.package_bindings[bundle.request.package_id];
  if(!binding||binding.package_version!==bundle.request.package_version||binding.digest!==bundle.standard.compilation.source_digest
    ||packageDigests[bundle.request.package_id]!==binding.digest)fail('Bundle package differs from its frozen import catalog','standard_snapshot_mismatch');
  for(const id of originalIds) {
    const metric=catalog.dictionary.metrics.find(m=>m.source_datapoint_id===id);
    if(!bundle.request.metric_ids.includes(metric.standard_binding.metric_id))fail('Bundle original projection differs from historical dictionary','import_scope_mismatch');
  }
}
