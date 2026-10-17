import {createHash,randomUUID} from 'node:crypto';

const nonempty=value=>typeof value==='string' && value.trim().length>0;
const known=value=>nonempty(value) && !['unknown','unspecified','not_reported','unverified'].includes(value);
const decimal=value=>typeof value==='string' && /^[+-]?\d+(?:\.\d+)?$/.test(value);
const codepointOrder=(left,right)=>{
  const a=Array.from(left),b=Array.from(right);
  for(let i=0;i<Math.min(a.length,b.length);i++) {
    const difference=a[i].codePointAt(0)-b[i].codePointAt(0);if(difference)return difference;
  }
  return a.length-b.length;
};
// Serialize sorted keys directly: JSON.stringify(Object.fromEntries(...))
// would reorder integer-looking keys and uses UTF-16 ordering by default.
const stable=value=>{
  if(Array.isArray(value))return '['+value.map(item=>stable(item) ?? 'null').join(',')+']';
  if(value && typeof value==='object')return '{'+Object.keys(value).sort(codepointOrder)
    .filter(key=>value[key]!==undefined).map(key=>JSON.stringify(key)+':'+stable(value[key])).join(',')+'}';
  return JSON.stringify(value);
};
// The standards library canonical JSON has a final LF; do not reuse the older
// publication dictionary digest (whose normalization has another contract).
export const extensionContractDigest=value=>createHash('sha256').update(stable(value)+'\n').digest('hex');
const same=(a,b)=>stable(a)===stable(b);
const fail=(message,code='invalid_extension_request')=>{const error=new Error(message);error.code=code;throw error;};
const single=async(tx,sql,params)=>(await tx.query(sql,params)).rows[0];
const lock=tx=>tx.query('SELECT singleton FROM esg.publication_control FOR UPDATE');
const arithmeticVersion='exact-decimal-sum-1.0.0';

export async function saveExtensionProposal(db,{proposal,submitted_by}) {
  if(!nonempty(submitted_by) || proposal?.schema_version!=='project-extension-proposal-1.0'
    || proposal.status!=='proposed' || !nonempty(proposal.proposal_id)
    || proposal.definition?.clause_kind!=='project_extension' || !nonempty(proposal.definition.extension_id))
    fail('A validated proposed extension and trusted submitting actor are required');
  if(['approval','decision','authority','authority_roles'].some(key=>key in proposal)) fail('A proposal cannot approve itself');
  const definitionDigest=extensionContractDigest(proposal.definition);
  return db.transaction(async tx=>{
    await lock(tx);
    const previous=await single(tx,'SELECT * FROM esg.extension_proposals WHERE proposal_id=$1',[proposal.proposal_id]);
    if(previous) {
      if(!same(previous.proposal,proposal) || previous.submitted_by!==submitted_by) fail('Immutable extension proposal differs','immutable_proposal_conflict');
      return {...previous,idempotent:true};
    }
    await tx.query(`INSERT INTO esg.extension_proposals(proposal_id,extension_id,definition_version,definition_digest,proposal,submitted_by)
      VALUES($1,$2,$3,$4,$5,$6)`,[proposal.proposal_id,proposal.definition.extension_id,proposal.definition.definition_version,definitionDigest,proposal,submitted_by]);
    return {...await single(tx,'SELECT * FROM esg.extension_proposals WHERE proposal_id=$1',[proposal.proposal_id]),idempotent:false};
  });
}

// The HTTP handler must construct authority from its authenticated principal.
// This library does not authenticate JSON, and never derives roles from receipt.
export async function recordExtensionDefinitionDecision(db,{receipt},authority) {
  if(!authority || !nonempty(authority.actor_ref) || !Array.isArray(authority.authority_roles)
    || !authority.authority_roles.includes('approve_extension_definition')) fail('Trusted definition approval authority required','forbidden');
  if(receipt?.schema_version!=='project-extension-approval-1.0' || !['approved','rejected'].includes(receipt.decision)
    || ![receipt.approval_id,receipt.proposal_id,receipt.actor_ref,receipt.reason].every(nonempty)
    || receipt.actor_ref!==authority.actor_ref || !/^[a-f0-9]{64}$/.test(receipt.definition_digest)
    || !/T.*(?:Z|[+-]\d\d:\d\d)$/.test(receipt.decided_at || '') || !Number.isFinite(Date.parse(receipt.decided_at)))
    fail('Validated approval receipt must match the authenticated actor');
  return db.transaction(async tx=>{
    await lock(tx);
    const proposal=await single(tx,'SELECT * FROM esg.extension_proposals WHERE proposal_id=$1',[receipt.proposal_id]);
    if(!proposal || proposal.definition_digest!==receipt.definition_digest) fail('Approval does not bind a stored proposal');
    const previous=await single(tx,'SELECT * FROM esg.extension_definition_decisions WHERE approval_id=$1',[receipt.approval_id]);
    if(previous) {
      if(!same(previous.receipt,receipt) || !same(previous.authority_roles,[...new Set(authority.authority_roles)].sort()))
        fail('Immutable extension approval differs','immutable_approval_conflict');
      return {...previous,idempotent:true};
    }
    const registered=await single(tx,'SELECT definition_digest FROM esg.extension_releases WHERE extension_id=$1 AND definition_version=$2',
      [proposal.extension_id,proposal.definition_version]);
    if(registered && (receipt.decision!=='approved' || receipt.definition_digest!==registered.definition_digest))
      fail('Registered definition decisions require a new version or a dedicated revocation workflow','registered_definition_decision_locked');
    await tx.query(`INSERT INTO esg.extension_definition_decisions(approval_id,proposal_id,extension_id,definition_version,definition_digest,decision,
      actor_ref,reason,decided_at,authority_roles,receipt) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
      [receipt.approval_id,receipt.proposal_id,proposal.extension_id,proposal.definition_version,receipt.definition_digest,receipt.decision,
       receipt.actor_ref,receipt.reason,receipt.decided_at,[...new Set(authority.authority_roles)].sort(),receipt]);
    await tx.query("INSERT INTO esg.outbox_events(event_id,event_type,target_ref) VALUES($1,'extension_definition',$2)",[randomUUID(),receipt.approval_id]);
    return {...await single(tx,'SELECT * FROM esg.extension_definition_decisions WHERE approval_id=$1',[receipt.approval_id]),idempotent:false};
  });
}

export async function registerExtensionRelease(db,{release}) {
  const definition=release?.definition;
  if(release?.schema_version!=='project-extension-release-1.0' || definition?.clause_kind!=='project_extension'
    || release.definition_digest!==extensionContractDigest(definition)
    || release.release_digest!==extensionContractDigest(Object.fromEntries(Object.entries(release).filter(([k])=>k!=='release_digest'))))
    fail('Compiled extension digest differs from its immutable payload');
  return db.transaction(async tx=>{
    await lock(tx);
    const approval=await single(tx,'SELECT * FROM esg.extension_definition_decisions WHERE approval_id=$1',[release.approval?.approval_id]);
    const proposal=approval && await single(tx,'SELECT * FROM esg.extension_proposals WHERE proposal_id=$1',[approval.proposal_id]);
    const latest=await single(tx,`SELECT * FROM esg.extension_definition_decisions WHERE extension_id=$1 AND definition_version=$2
      ORDER BY decision_order DESC LIMIT 1`,[definition.extension_id,definition.definition_version]);
    if(!approval || approval.decision!=='approved' || !same(approval.receipt,release.approval)
      || approval.definition_digest!==release.definition_digest || !same(proposal?.proposal.definition,definition)
      || latest?.decision!=='approved' || latest.definition_digest!==release.definition_digest)
      fail('Compiled extension has no authoritative current approval','definition_not_approved');
    const previous=await single(tx,'SELECT * FROM esg.extension_releases WHERE extension_id=$1 AND definition_version=$2',
      [definition.extension_id,definition.definition_version]);
    if(previous) {
      if(!same(previous.release,release)) fail('Cannot replace an immutable extension release','immutable_release_conflict');
      return {...previous,idempotent:true};
    }
    await tx.query(`INSERT INTO esg.extension_releases(extension_id,definition_version,release_digest,definition_digest,approval_id,
      parent_package_id,parent_package_version,parent_source_digest,release) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
      [definition.extension_id,definition.definition_version,release.release_digest,release.definition_digest,approval.approval_id,
       definition.parent_package_id,definition.parent_package_version,definition.parent_source_digest,release]);
    return {...await single(tx,'SELECT * FROM esg.extension_releases WHERE extension_id=$1 AND definition_version=$2',
      [definition.extension_id,definition.definition_version]),idempotent:false};
  });
}

export async function listExtensionRegistry(db) {
  const [proposals,decisions,releases]=await Promise.all([
    db.query('SELECT * FROM esg.extension_proposals ORDER BY created_at,proposal_id'),
    db.query('SELECT * FROM esg.extension_definition_decisions ORDER BY decision_order'),
    db.query('SELECT * FROM esg.extension_releases ORDER BY extension_id,definition_version'),
  ]);
  return {proposals:proposals.rows,decisions:decisions.rows,releases:releases.rows};
}

function parts(text) {
  if(!decimal(text) || text.length>10000) fail('Finite decimal strings of bounded size are required');
  const negative=text.startsWith('-'),clean=text.replace(/^[+-]/,'');
  const [whole,fraction='']=clean.split('.');
  return {coefficient:BigInt(whole+fraction)*(negative?-1n:1n),scale:fraction.length};
}
function render(coefficient,scale) {
  if(coefficient===0n)return '0';
  const sign=coefficient<0n?'-':'',digits=(coefficient<0n?-coefficient:coefficient).toString().padStart(scale+1,'0');
  const value=scale?digits.slice(0,-scale)+'.'+digits.slice(-scale):digits;
  return sign+(scale?value.replace(/0+$/,'').replace(/\.$/,''):value);
}
const gcd=(a,b)=>{a=a<0n?-a:a;b=b<0n?-b:b;while(b){[a,b]=[b,a%b];}return a;};
export function exactDecimalSum(values) {
  if(!Array.isArray(values) || !values.length)fail('A sum requires explicit decimal operands');
  const parsed=values.map(parts),scale=Math.max(...parsed.map(v=>v.scale));
  return render(parsed.reduce((sum,p)=>sum+p.coefficient*10n**BigInt(scale-p.scale),0n),scale);
}
export function exactUnitConversion(value,multiplier,divisor) {
  const v=parts(value),m=parts(multiplier),d=parts(divisor);
  if(m.coefficient<=0n || d.coefficient<=0n)fail('Unit factors must be positive');
  let numerator=v.coefficient*m.coefficient*10n**BigInt(d.scale);
  let denominator=d.coefficient*10n**BigInt(v.scale+m.scale);
  const common=gcd(numerator,denominator);numerator/=common;denominator/=common;
  let twos=0,fives=0;
  while(denominator%2n===0n){denominator/=2n;twos++;}
  while(denominator%5n===0n){denominator/=5n;fives++;}
  if(denominator!==1n)fail('Non-terminating conversion cannot satisfy exact_decimal_sum','non_terminating_conversion');
  const scale=Math.max(twos,fives);
  return render(numerator*2n**BigInt(scale-twos)*5n**BigInt(scale-fives),scale);
}

function axes(row) {
  return {...row.dimensions,entity_ref:row.entity_ref,period_start:row.period_start,period_end:row.period_end,
    boundary_code:row.boundary_code,value_role:row.value_role,measurement_method:row.measurement_method};
}
function comparison(definition,values) {
  const reasons=[];
  for(const axis of definition.comparison_contract.required_axes) {
    if(!known(values[axis]))reasons.push(`missing_comparison_axis:${axis}`);
    else if(definition.comparison_contract.allowed_values[axis] && !definition.comparison_contract.allowed_values[axis].includes(values[axis]))
      reasons.push(`comparison_axis_mismatch:${axis}`);
  }
  return {comparison_eligibility:reasons.length?'not_comparable':'comparable',comparison_reasons:reasons};
}

async function resolveCalculation(tx,payload,currentReportedIds=null) {
  const reasons=[];
  const blocked=reason=>{reasons.push(reason);return {reasons};};
  const releaseRow=await single(tx,'SELECT * FROM esg.extension_releases WHERE extension_id=$1 AND definition_version=$2',
    [payload.extension_id,payload.definition_version]);
  if(!releaseRow || releaseRow.release_digest!==payload.release_digest)return blocked('extension_release_snapshot_mismatch');
  const release=releaseRow.release,definition=release.definition;
  const latest=await single(tx,`SELECT decision,definition_digest FROM esg.extension_definition_decisions WHERE extension_id=$1 AND definition_version=$2
    ORDER BY decision_order DESC LIMIT 1`,[payload.extension_id,payload.definition_version]);
  if(latest?.decision!=='approved' || latest.definition_digest!==release.definition_digest)return blocked('extension_definition_not_approved');
  const rule=definition.derivation_rules.find(r=>r.rule_id===payload.rule_id);
  if(!rule || rule.operation!=='sum' || rule.arithmetic!=='exact_decimal_sum' || rule.output_review_status!=='pending'
    || rule.unit_policy!=='reviewed_versioned_conversion_to_target_unit' || !rule.require_approved_operands
    || !rule.require_explicit_match_values || !rule.require_point_values)return blocked('unsupported_or_unapproved_derivation_rule');
  const target=await single(tx,'SELECT * FROM esg.metric_definition_versions WHERE metric_id=$1 AND definition_version=$2',
    [payload.target_metric_id,payload.target_definition_version]);
  const binding=target?.definition.extension_binding;
  if(target?.clause_kind!=='project_extension' || target.extension_id!==payload.extension_id
    || target.definition_version!==payload.definition_version
    || binding?.extension_id!==payload.extension_id || binding.definition_version!==payload.definition_version
    || binding.release_digest!==payload.release_digest || binding.definition_digest!==release.definition_digest
    || binding.parent_package_id!==definition.parent_package_id
    || binding.parent_package_version!==definition.parent_package_version || binding.parent_source_digest!==definition.parent_source_digest
    || target.definition.canonical_unit!==definition.canonical_unit || target.readiness!=='ready')return blocked('target_extension_definition_mismatch');
  const dataset=await single(tx,'SELECT * FROM esg.dataset_versions WHERE dataset_version=$1 AND sealed',[payload.input_dataset_version]);
  if(!dataset)return blocked('input_dataset_not_sealed');
  if(!Array.isArray(payload.operands) || payload.operands.length!==rule.operands.length
    || new Set(payload.operands.map(o=>o.operand_id)).size!==payload.operands.length
    || new Set(payload.operands.map(o=>o.observation_id)).size!==payload.operands.length
    || rule.operands.some(selector=>!payload.operands.find(o=>o.operand_id===selector.operand_id)))return blocked('operand_selection_does_not_match_rule');
  const operands=[];
  for(const selector of rule.operands) {
    const input=payload.operands.find(o=>o.operand_id===selector.operand_id),prefix=`operand:${selector.operand_id}:`;
    const row=await single(tx,`SELECT o.*,o.period_start::text,o.period_end::text,o.numeric_value::text,o.lower_bound::text,o.upper_bound::text,
      md.definition,md.source_datapoint_id,md.clause_kind,os.context_id,os.disclosure_id,os.mapping_id,os.source_snapshot,
      c.revision AS context_revision,c.dimensions AS context_dimensions,c.entity_ref AS context_entity_ref,c.period_start::text AS context_period_start,
      c.period_end::text AS context_period_end,c.boundary_code AS context_boundary_code,c.value_role AS context_value_role,
      c.measurement_method AS context_measurement_method,d.quality_status,m.review_status AS mapping_status,m.mapping_relation,m.satisfaction_status
      FROM esg.dataset_membership member JOIN esg.curated_observations o USING(observation_id)
      JOIN esg.metric_definition_versions md USING(metric_id,definition_version)
      JOIN esg.observation_sources os USING(observation_id) JOIN esg.measurement_contexts c USING(context_id)
      JOIN esg.esg_disclosures d ON d.disclosure_id=os.disclosure_id
      JOIN esg.disclosure_standard_mappings m ON m.disclosure_standard_mapping_id=os.mapping_id
      WHERE member.dataset_version=$1 AND o.observation_id=$2 AND os.context_id=$3`,
      [payload.input_dataset_version,input.observation_id,input.context_id]);
    if(!row){reasons.push(prefix+'not_in_input_dataset_or_context');continue;}
    const standard=row.definition.standard_binding;
    if(row.clause_kind!=='original_datapoint' || row.source_datapoint_id!==selector.source_datapoint_id
      || standard?.metric_id!==selector.source_metric_id || standard.package_id!==definition.parent_package_id
      || standard.package_version!==definition.parent_package_version || standard.digest!==definition.parent_source_digest)
      reasons.push(prefix+'frozen_standard_binding_mismatch');
    const source=row.source_snapshot;
    const fact=await single(tx,'SELECT * FROM esg.review_decisions WHERE decision_id=$1',[input.fact_review_decision_id]);
    const mapping=await single(tx,'SELECT * FROM esg.review_decisions WHERE decision_id=$1',[input.mapping_review_decision_id]);
    if(!fact || fact.disclosure_id!==row.disclosure_id || fact.decision!=='approved'
      || source.fact_review_decision_id!==input.fact_review_decision_id)reasons.push(prefix+'fact_approval_reference_invalid');
    if(!mapping || mapping.mapping_id!==row.mapping_id || mapping.decision!=='approved'
      || source.mapping_review_decision_id!==input.mapping_review_decision_id)reasons.push(prefix+'mapping_approval_reference_invalid');
    const currentFact=await single(tx,'SELECT decision FROM esg.review_decisions WHERE disclosure_id=$1 ORDER BY created_at DESC,decision_id DESC LIMIT 1',[row.disclosure_id]);
    const currentMapping=await single(tx,'SELECT decision FROM esg.review_decisions WHERE mapping_id=$1 ORDER BY created_at DESC,decision_id DESC LIMIT 1',[row.mapping_id]);
    if(row.quality_status!=='approved' || row.mapping_status!=='approved' || currentFact?.decision!=='approved' || currentMapping?.decision!=='approved'
      || row.mapping_relation!=='exact' || row.satisfaction_status!=='supports')reasons.push(prefix+'approval_no_longer_active');
    const state=await single(tx,'SELECT publication_status,observation_id FROM esg.publication_states WHERE mapping_id=$1',[row.mapping_id]);
    if(state?.publication_status!=='published' || state.observation_id!==row.observation_id
      || (currentReportedIds && !currentReportedIds.has(row.observation_id)))reasons.push(prefix+'not_currently_published');
    const frozenAxes=axes(row),contextAxes=axes({dimensions:row.context_dimensions,entity_ref:row.context_entity_ref,
      period_start:row.context_period_start,period_end:row.context_period_end,boundary_code:row.context_boundary_code,
      value_role:row.context_value_role,measurement_method:row.context_measurement_method});
    if(!same(frozenAxes,contextAxes))reasons.push(prefix+'context_snapshot_differs');
    if(!decimal(row.numeric_value) || row.lower_bound!==null || row.upper_bound!==null
      || ![null,'exact'].includes(row.precision_qualifier) || row.statement!==null)reasons.push(prefix+'point_value_required');
    for(const [axis,allowed] of Object.entries(selector.required_axes))if(!known(frozenAxes[axis]) || !allowed.includes(frozenAxes[axis]))
      reasons.push(prefix+`selector_mismatch:${axis}`);
    const normalization=await single(tx,'SELECT * FROM esg.normalization_rule_versions WHERE rule_version=$1',[row.normalization_rule_version]);
    if(!normalization || source.unit_conversion?.rule_version!==normalization.rule_version
      || source.unit_conversion.rule_digest!==normalization.rule_digest)reasons.push(prefix+'normalization_rule_snapshot_missing');
    const conversion=normalization?.definition.conversions?.find(c=>c.canonical_unit===definition.canonical_unit
      && c.aliases.some(alias=>alias.replace(/\s/g,'')===row.canonical_unit?.replace(/\s/g,'')));
    if(!conversion)reasons.push(prefix+'target_unit_conversion_unavailable');
    let converted=null;
    if(conversion && decimal(row.numeric_value)) {
      try{converted=exactUnitConversion(row.numeric_value,conversion.multiplier,conversion.divisor);}
      catch(error){reasons.push(prefix+(error.code || 'unit_conversion_failed'));}
    }
    if(converted!==null)operands.push({operand_id:selector.operand_id,observation_id:row.observation_id,metric_id:row.metric_id,
      standard_metric_id:standard?.metric_id,metric_definition_version:row.definition_version,source_datapoint_id:row.source_datapoint_id,
      context_id:row.context_id,context_revision:row.context_revision,fact_review_decision_id:input.fact_review_decision_id,
      mapping_review_decision_id:input.mapping_review_decision_id,numeric_value:converted,canonical_unit:definition.canonical_unit,
      normalization_rule_version:normalization.rule_version,normalization_rule_digest:normalization.rule_digest,
      reviewed_axes:Object.fromEntries([...new Set([...rule.shared_match_axes,...Object.keys(selector.required_axes)])]
        .filter(axis=>known(frozenAxes[axis])).map(axis=>[axis,frozenAxes[axis]])),
      database_snapshot:{disclosure_id:row.disclosure_id,mapping_id:row.mapping_id,original_numeric_value:row.numeric_value,
        original_canonical_unit:row.canonical_unit,unit_conversion:conversion,source_snapshot:source}});
  }
  if(reasons.length)return {reasons:[...new Set(reasons)],rule};
  const targetAxes={};
  for(const axis of rule.shared_match_axes) {
    const values=operands.map(o=>o.reviewed_axes[axis]);
    if(values.some(value=>!known(value)) || new Set(values).size!==1)reasons.push(`shared_axis_mismatch_or_missing:${axis}`);
    else targetAxes[axis]=values[0];
  }
  for(const projection of rule.target_axis_projections)targetAxes[projection.target_axis]=projection.fixed_value
    ?? operands.find(o=>o.operand_id===projection.from_operand_id)?.reviewed_axes[projection.source_axis];
  if(definition.identity_axes.some(axis=>!known(targetAxes[axis])))reasons.push('derived_target_identity_incomplete');
  if(reasons.length)return {reasons,rule};
  const output=exactDecimalSum(operands.map(o=>o.numeric_value));
  const constraints=definition.value_constraints;
  if(constraints && (constraints.minimum!=null || constraints.maximum!=null)) {
    const valid=await single(tx,'SELECT ($2::numeric IS NULL OR $1::numeric >= $2::numeric) AND ($3::numeric IS NULL OR $1::numeric <= $3::numeric) AS valid',
      [output,constraints.minimum,constraints.maximum]);
    if(!valid.valid)return blocked('derived_value_outside_definition_bounds');
  }
  const lineage={schema_version:'project-extension-lineage-1.0',value_origin:'derived',extension_id:definition.extension_id,
    definition_version:definition.definition_version,release_digest:release.release_digest,rule_id:rule.rule_id,rule_version:rule.rule_version,
    rule_digest:extensionContractDigest(rule),input_dataset_version:payload.input_dataset_version,
    operands:operands.map(({database_snapshot,...rest})=>rest),output_numeric_value:output,canonical_unit:definition.canonical_unit,arithmetic:'exact_decimal_sum'};
  return {reasons:[],rule,target,release,operands,targetAxes,output,lineage,...comparison(definition,targetAxes)};
}

async function requestResult(tx,request) {
  const candidate=await single(tx,`SELECT c.*,c.numeric_value::text,(SELECT decision FROM esg.extension_candidate_reviews r WHERE r.candidate_id=c.candidate_id
    ORDER BY review_order DESC LIMIT 1) AS current_review_status FROM esg.extension_derived_candidates c WHERE request_id=$1`,[request.request_id]);
  return {...request,candidate:candidate?{...candidate,review_status:candidate.current_review_status || 'pending'}:null};
}
async function executeRequest(tx,request) {
  const existing=await single(tx,'SELECT 1 FROM esg.extension_derived_candidates WHERE request_id=$1',[request.request_id]);
  if(existing)return requestResult(tx,request);
  const calculation=await resolveCalculation(tx,request.payload);
  if(calculation.reasons.length) {
    await tx.query("UPDATE esg.extension_derivation_requests SET status='blocked',reason_codes=$2,evaluated_at=CURRENT_TIMESTAMP WHERE request_id=$1",
      [request.request_id,calculation.reasons]);
    return requestResult(tx,await single(tx,'SELECT * FROM esg.extension_derivation_requests WHERE request_id=$1',[request.request_id]));
  }
  const candidateId=randomUUID(),lineageDigest=extensionContractDigest(calculation.lineage);
  await tx.query(`INSERT INTO esg.extension_derived_candidates(candidate_id,request_id,metric_id,definition_version,numeric_value,canonical_unit,
    reviewed_axes,comparison_eligibility,comparison_reasons,lineage_snapshot,lineage_digest) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
    [candidateId,request.request_id,request.target_metric_id,request.target_definition_version,calculation.output,calculation.lineage.canonical_unit,
      calculation.targetAxes,calculation.comparison_eligibility,calculation.comparison_reasons,calculation.lineage,lineageDigest]);
  for(const operand of calculation.operands)await tx.query(`INSERT INTO esg.extension_operand_lineage(candidate_id,operand_id,observation_id,
    input_dataset_version,platform_metric_id,metric_definition_version,standard_metric_id,source_datapoint_id,context_id,context_revision,disclosure_id,mapping_id,
    fact_review_decision_id,mapping_review_decision_id,original_numeric_value,original_canonical_unit,converted_numeric_value,canonical_unit,
    normalization_rule_version,normalization_rule_digest,reviewed_axes,operand_snapshot) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)`,
    [candidateId,operand.operand_id,operand.observation_id,request.input_dataset_version,operand.metric_id,operand.metric_definition_version,operand.standard_metric_id,operand.source_datapoint_id,
      operand.context_id,operand.context_revision,operand.database_snapshot.disclosure_id,operand.database_snapshot.mapping_id,
      operand.fact_review_decision_id,operand.mapping_review_decision_id,operand.database_snapshot.original_numeric_value,
      operand.database_snapshot.original_canonical_unit,operand.numeric_value,operand.canonical_unit,operand.normalization_rule_version,
      operand.normalization_rule_digest,operand.reviewed_axes,operand.database_snapshot]);
  await tx.query("UPDATE esg.extension_derivation_requests SET status='candidate_created',reason_codes='[]',evaluated_at=CURRENT_TIMESTAMP WHERE request_id=$1",[request.request_id]);
  await tx.query("INSERT INTO esg.outbox_events(event_id,event_type,target_ref) VALUES($1,'extension_derivation',$2)",[randomUUID(),candidateId]);
  return requestResult(tx,await single(tx,'SELECT * FROM esg.extension_derivation_requests WHERE request_id=$1',[request.request_id]));
}

export async function requestExtensionDerivation(db,payload,{requested_by}={}) {
  const fields=['idempotency_key','extension_id','definition_version','release_digest','rule_id','target_metric_id','target_definition_version'];
  if(!nonempty(requested_by) || fields.some(key=>!nonempty(payload[key])) || !Number.isSafeInteger(payload.input_dataset_version)
    || payload.input_dataset_version<0 || !Array.isArray(payload.operands))fail('A fixed derivation selection and trusted requester are required');
  const allowed=new Set([...fields,'input_dataset_version','operands']);
  if(Object.keys(payload).some(key=>!allowed.has(key)))fail('Unexpected derivation request field');
  for(const operand of payload.operands)if(!['operand_id','observation_id','context_id','fact_review_decision_id','mapping_review_decision_id'].every(key=>nonempty(operand?.[key])))
    fail('Explicit published operand and review references are required');
  const operandFields=new Set(['operand_id','observation_id','context_id','fact_review_decision_id','mapping_review_decision_id']);
  if(payload.operands.some(operand=>Object.keys(operand).some(key=>!operandFields.has(key))))fail('Unexpected operand request field');
  const payloadDigest=extensionContractDigest(payload);
  const calculationDigest=extensionContractDigest({...Object.fromEntries(Object.entries(payload).filter(([key])=>key!=='idempotency_key')),
    operands:[...payload.operands].sort((a,b)=>a.operand_id<b.operand_id?-1:a.operand_id>b.operand_id?1:0)});
  return db.transaction(async tx=>{
    await lock(tx);
    const key=await single(tx,'SELECT * FROM esg.extension_derivation_idempotency_keys WHERE idempotency_key=$1',[payload.idempotency_key]);
    const prior=key && await single(tx,'SELECT * FROM esg.extension_derivation_requests WHERE request_id=$1',[key.request_id]);
    if(prior) {
      if(key.payload_digest!==payloadDigest)fail('Idempotency key names another derivation request','idempotency_conflict');
      return {...await executeRequest(tx,prior),idempotent:true};
    }
    const sameCalculation=await single(tx,'SELECT * FROM esg.extension_derivation_requests WHERE calculation_digest=$1',[calculationDigest]);
    if(sameCalculation) {
      await tx.query('INSERT INTO esg.extension_derivation_idempotency_keys(idempotency_key,request_id,payload_digest,requested_by) VALUES($1,$2,$3,$4)',
        [payload.idempotency_key,sameCalculation.request_id,payloadDigest,requested_by]);
      return {...await executeRequest(tx,sameCalculation),requested_idempotency_key:payload.idempotency_key,idempotent:true};
    }
    const release=await single(tx,'SELECT release FROM esg.extension_releases WHERE extension_id=$1 AND definition_version=$2',
      [payload.extension_id,payload.definition_version]);
    const rule=release?.release.definition.derivation_rules.find(rule=>rule.rule_id===payload.rule_id);
    if(!rule || release.release.release_digest!==payload.release_digest)fail('Registered derivation release/rule does not exist','not_found');
    if(!await single(tx,'SELECT 1 FROM esg.dataset_versions WHERE dataset_version=$1',[payload.input_dataset_version]))fail('Input dataset does not exist','not_found');
    if(!await single(tx,'SELECT 1 FROM esg.metric_definition_versions WHERE metric_id=$1 AND definition_version=$2',[payload.target_metric_id,payload.target_definition_version]))fail('Target metric definition does not exist','not_found');
    const id=randomUUID();
    await tx.query(`INSERT INTO esg.extension_derivation_requests(request_id,idempotency_key,payload_digest,calculation_digest,payload,input_dataset_version,extension_id,
      definition_version,release_digest,rule_id,rule_digest,target_metric_id,target_definition_version,requested_by,status)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'blocked')`,[id,payload.idempotency_key,payloadDigest,calculationDigest,payload,
      payload.input_dataset_version,payload.extension_id,payload.definition_version,payload.release_digest,payload.rule_id,
      extensionContractDigest(rule),payload.target_metric_id,payload.target_definition_version,requested_by]);
    await tx.query('INSERT INTO esg.extension_derivation_idempotency_keys(idempotency_key,request_id,payload_digest,requested_by) VALUES($1,$2,$3,$4)',
      [payload.idempotency_key,id,payloadDigest,requested_by]);
    return {...await executeRequest(tx,await single(tx,'SELECT * FROM esg.extension_derivation_requests WHERE request_id=$1',[id])),idempotent:false};
  });
}

export async function retryExtensionDerivation(db,{request_id}) {
  return db.transaction(async tx=>{
    await lock(tx);const request=await single(tx,'SELECT * FROM esg.extension_derivation_requests WHERE request_id=$1',[request_id]);
    if(!request)fail('Derivation request does not exist','not_found');
    return executeRequest(tx,request);
  });
}

export async function reviewDerivedCandidate(db,{candidate_id,decision,operator_ref,reason}) {
  if(![candidate_id,operator_ref,reason].every(nonempty) || !['approved','rejected','review_required'].includes(decision))fail('Explicit calculation review is required');
  return db.transaction(async tx=>{
    await lock(tx);
    if(!await single(tx,'SELECT 1 FROM esg.extension_derived_candidates WHERE candidate_id=$1',[candidate_id]))fail('Derived candidate does not exist','not_found');
    const latest=await single(tx,'SELECT * FROM esg.extension_candidate_reviews WHERE candidate_id=$1 ORDER BY review_order DESC LIMIT 1',[candidate_id]);
    if(latest && latest.decision===decision && latest.operator_ref===operator_ref && latest.reason===reason)return {...latest,idempotent:true};
    const id=randomUUID();
    await tx.query('INSERT INTO esg.extension_candidate_reviews(decision_id,candidate_id,decision,operator_ref,reason) VALUES($1,$2,$3,$4,$5)',
      [id,candidate_id,decision,operator_ref,reason]);
    await tx.query("INSERT INTO esg.outbox_events(event_id,event_type,target_ref) VALUES($1,'extension_review',$2)",[id,candidate_id]);
    return {...await single(tx,'SELECT * FROM esg.extension_candidate_reviews WHERE decision_id=$1',[id]),idempotent:false};
  });
}

// Caller already owns publication_control inside its dataset/outbox transaction.
// Merge returned IDs into that transaction's desired membership before sealing.
export async function projectDerivedCandidates(tx,{dictionary_version,current_reported_observation_ids}) {
  if(!nonempty(dictionary_version) || !Array.isArray(current_reported_observation_ids))fail('Publication reconciliation requires an authoritative reported set');
  const current=new Set(current_reported_observation_ids),candidates=(await tx.query(`SELECT c.*,c.numeric_value::text,r.payload,
    (SELECT jsonb_build_object('decision_id',rv.decision_id,'decision',rv.decision) FROM esg.extension_candidate_reviews rv WHERE rv.candidate_id=c.candidate_id
      ORDER BY rv.review_order DESC LIMIT 1) AS review FROM esg.extension_derived_candidates c JOIN esg.extension_derivation_requests r USING(request_id)
    ORDER BY c.created_at,c.candidate_id`)).rows;
  const ready=[];
  for(const candidate of candidates) {
    const reasons=[];
    if(candidate.review?.decision!=='approved')reasons.push(`calculation_${candidate.review?.decision || 'pending'}`);
    const member=await single(tx,'SELECT 1 FROM esg.dictionary_membership WHERE dictionary_version=$1 AND metric_id=$2 AND definition_version=$3',
      [dictionary_version,candidate.metric_id,candidate.definition_version]);
    if(!member)reasons.push('extension_not_in_published_dictionary');
    const calculation=await resolveCalculation(tx,candidate.payload,current);
    reasons.push(...calculation.reasons);
    if(!calculation.reasons.length && (extensionContractDigest(calculation.lineage)!==candidate.lineage_digest
      || !same(calculation.targetAxes,candidate.reviewed_axes) || calculation.output!==candidate.numeric_value))reasons.push('frozen_calculation_receipt_differs');
    if(!reasons.length)ready.push({candidate,calculation});
    candidate.reasons=reasons;
  }
  // Independently produced/reported values remain first-class and cannot silently
  // replace one another. An equal result keeps both source lineages.
  const groups=new Map();
  for(const item of ready) {
    const identity=extensionContractDigest({metric_id:item.candidate.metric_id,definition_version:item.candidate.definition_version,axes:item.calculation.targetAxes});
    if(!groups.has(identity))groups.set(identity,[]);groups.get(identity).push(item);
  }
  const published=[];
  const reportedRows=current.size?(await tx.query(`SELECT metric_id,definition_version,numeric_value::text,dimensions,entity_ref,period_start::text,
    period_end::text,boundary_code,value_role,measurement_method FROM esg.curated_observations WHERE observation_id=ANY($1::uuid[])`,[[...current]])).rows:[];
  for(const group of groups.values()) {
    const values=new Set(group.map(item=>exactDecimalSum([item.candidate.numeric_value])));
    const sample=group[0],a=sample.calculation.targetAxes;
    for(const reported of reportedRows) {
      if(reported?.metric_id===sample.candidate.metric_id && reported.definition_version===sample.candidate.definition_version
        && same(Object.fromEntries(Object.keys(a).map(axis=>[axis,axes(reported)[axis]])),a))
        values.add(decimal(reported.numeric_value)?exactDecimalSum([reported.numeric_value]):'non_point_reported_value');
    }
    if(values.size>1){for(const item of group)item.candidate.reasons.push('unresolved_reported_derived_conflict');continue;}
    for(const {candidate,calculation} of group) {
      const identity={metric_id:candidate.metric_id,definition_version:candidate.definition_version,axes:calculation.targetAxes};
      const observationId=await single(tx,'SELECT observation_id FROM esg.derived_observation_lineage WHERE candidate_id=$1 AND calculation_review_decision_id=$2',
        [candidate.candidate_id,candidate.review.decision_id]);
      const id=observationId?.observation_id || randomUUID(),dims={...calculation.targetAxes};
      for(const typed of ['entity_ref','period_start','period_end','boundary_code','value_role','measurement_method'])delete dims[typed];
      await tx.query(`INSERT INTO esg.curated_observations(observation_id,metric_id,definition_version,identity_key,entity_ref,period_start,period_end,
        boundary_code,value_role,measurement_method,dimensions,canonical_unit,numeric_value,precision_qualifier,comparison_eligibility,
        comparison_reasons,normalization_rule_version,value_origin) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,NULL,$10,$11,$12,'exact',$13,$14,$15,'derived') ON CONFLICT DO NOTHING`,
        [id,candidate.metric_id,candidate.definition_version,extensionContractDigest(identity),a.entity_ref,a.period_start,a.period_end,a.boundary_code,
         a.value_role,dims,candidate.canonical_unit,candidate.numeric_value,candidate.comparison_eligibility,candidate.comparison_reasons,arithmeticVersion]);
      await tx.query(`INSERT INTO esg.derived_observation_lineage(observation_id,candidate_id,calculation_review_decision_id,lineage_snapshot,lineage_digest)
        VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING`,[id,candidate.candidate_id,candidate.review.decision_id,candidate.lineage_snapshot,candidate.lineage_digest]);
      candidate.observation_id=id;published.push(id);
    }
  }
  for(const candidate of candidates) {
    const prior=await single(tx,'SELECT observation_id FROM esg.derived_publication_states WHERE candidate_id=$1',[candidate.candidate_id]);
    const status=candidate.reasons.length?(prior?.observation_id?'withdrawn':candidate.review?'blocked':'not_published'):'published';
    await tx.query(`INSERT INTO esg.derived_publication_states(candidate_id,review_status,publication_status,reason_codes,observation_id)
      VALUES($1,$2,$3,$4,$5) ON CONFLICT(candidate_id) DO UPDATE SET review_status=EXCLUDED.review_status,publication_status=EXCLUDED.publication_status,
      reason_codes=EXCLUDED.reason_codes,observation_id=EXCLUDED.observation_id,evaluated_at=CURRENT_TIMESTAMP`,
      [candidate.candidate_id,candidate.review?.decision || 'pending',status,[...new Set(candidate.reasons)],candidate.observation_id || prior?.observation_id || null]);
  }
  return {observation_ids:[...new Set(published)].sort(),blocked:candidates.filter(c=>c.reasons.length).length};
}
