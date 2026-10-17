import {useEffect,useState} from 'react';
import {items,type Row} from '../api';
import {useResource} from '../state';
import {Notice,ResourceState} from './ui';
import type {ParentSnapshot} from './ScopeSelector';

const sha256=(value:unknown):value is string=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const version=(value:unknown):value is string=>typeof value==='string'&&/^\d+\.\d+\.\d+$/.test(value);
const nonempty=(value:unknown):value is string=>typeof value==='string'&&!!value.trim();

function releaseBlocker(release:Row):string|undefined {
 const definition=release.definition,approval=release.approval;
 if(release.schema_version!=='project-extension-release-1.0'||approval?.schema_version!=='project-extension-approval-1.0'
  ||approval?.decision!=='approved'||!nonempty(approval?.approval_id)||!sha256(release.definition_digest)
  ||approval.definition_digest!==release.definition_digest||!sha256(release.release_digest))
  return '此定义尚未具有可核对的批准与登记收据，不能选择草稿。';
 if(!definition||!nonempty(definition.extension_id)||!version(definition.definition_version)
  ||!nonempty(definition.parent_package_id)||!version(definition.parent_package_version)||!sha256(definition.parent_source_digest))
  return '此延伸的固定父包身份不完整，不能创建提取选择。';
}

/** A selection is bound to the actual scope, never the latest authoring draft. */
export function extensionCompatibility(release:Row,parentSnapshots:ParentSnapshot[]) {
 const invalid=releaseBlocker(release);
 if(invalid)return {compatible:false,reason:invalid,parent_mode:'unavailable' as const};
 const definition=release.definition,parents=parentSnapshots.filter(p=>p.package_id===definition.parent_package_id);
 if(parents.length>1)return {compatible:false,reason:'本次范围含有多个同名父包身份，请先明确唯一的父包版本。',parent_mode:'selected_parent' as const};
 const parent=parents[0];
 if(!parent)return {compatible:true,parent_mode:'extension_parent' as const};
 if(parent.package_version!==definition.parent_package_version)return {compatible:false,parent_mode:'selected_parent' as const,
  reason:`本次已选父包 v${parent.package_version}；此延伸固定父包 v${definition.parent_package_version}，版本不兼容。请选择匹配的已批准延伸。`};
 if(!sha256(parent.source_digest)||parent.source_digest!==definition.parent_source_digest)return {compatible:false,parent_mode:'selected_parent' as const,
  reason:`本次已选父包 v${parent.package_version} 的来源摘要与延伸固定父包不一致，不能混用或自动换包。`};
 return {compatible:true,parent_mode:'selected_parent' as const};
}

export function extensionSelection(release:Row,reported:boolean,ruleIds:string[]):Row|undefined{
 if(releaseBlocker(release)||!reported&&!ruleIds.length)return;
 const definition=release.definition;
 return {extension_id:definition.extension_id,definition_version:definition.definition_version,
  release_digest:release.release_digest,selection_kind:'custom_extension',
  value_origins:[...(reported?['reported']:[]),...(ruleIds.length?['derived']:[])],derivation_rule_ids:ruleIds};
}

export function ExtensionSelector({onChange,parentSnapshots=[]}:{onChange:(selected:Row[],error?:string)=>void;parentSnapshots?:ParentSnapshot[]}){
 const resource=useResource('document','/api/pipeline/extensions');
 const [selected,setSelected]=useState<Row[]>([]);
 const releases=items(resource.data).filter(row=>row.definition&&typeof row.definition==='object');
 const parentKey=JSON.stringify(parentSnapshots);
 useEffect(()=>{
  const blockers=selected.map(selection=>{
   const release=releases.find(row=>row.definition.extension_id===selection.extension_id
    &&row.definition.definition_version===selection.definition_version&&row.release_digest===selection.release_digest);
   return release?extensionCompatibility(release,parentSnapshots).reason:'所选延伸版本当前不可读，保留选择并阻止执行。';
  }).filter(Boolean);
  onChange(selected,blockers.length?blockers.join('；'):selected.length&&resource.error?resource.error:undefined);
 },[resource.data,resource.error,selected,onChange,parentKey]);
 function update(release:Row,reported:boolean,ruleIds:string[]){
  if(!extensionCompatibility(release,parentSnapshots).compatible)return;
  const value=extensionSelection(release,reported,ruleIds);
  setSelected(old=>[...old.filter(s=>s.extension_id!==release.definition.extension_id),...(value?[value]:[])]);
 }
 return <details className="scope-package"><summary>可选延伸条款 · 已选 {selected.length} 项</summary>
  <Notice>延伸条款有独立身份，关联原编号。选择直接披露不增加计算操作数；选择公式只读取该公式所需操作数，正式计算须待审核后完成。</Notice>
  {selected.length>0&&<button type="button" className="ghost" onClick={()=>setSelected([])}>清空延伸选择</button>}
  <ResourceState error={resource.error} loading={resource.loading&&!resource.data}/>
  {resource.data&&!releases.length&&<p>当前尚无已批准登记的延伸条款。可在标准中心审核定义后登记。</p>}
  {releases.map(release=>{
   const d=release.definition,status=extensionCompatibility(release,parentSnapshots);
   const selection=selected.find(s=>s.extension_id===d.extension_id&&s.definition_version===d.definition_version&&s.release_digest===release.release_digest);
   const reported=selection?.value_origins?.includes('reported') || false,rules:string[]=selection?.derivation_rule_ids || [];
   return <article className="task-card" key={`${d.extension_id}@${d.definition_version}:${release.release_digest || 'unregistered'}`}>
    <h3>{d.labels?.zh || '延伸定义待核对'}</h3><p>{d.description}</p>
    <p>父包：{String(d.parent_package_id || '').split('.').pop()?.toUpperCase() || '待核对'} · 父版本 v{d.parent_package_version || '未确定'} · 延伸定义 v{d.definition_version} · {d.package_core?'包内延伸核心':'包内其它延伸'}</p>
    <p>关联原编号：{(d.original_clause_links || []).map((link:Row)=>link.source_datapoint_id).join('、')}</p>
    {status.compatible&&status.parent_mode==='extension_parent'&&<Notice>本次未选择此父包的原条款。勾选该延伸将明确使用它固定的父包 v{d.parent_package_version} 与来源摘要，后端预览继续核验并冻结；不改选新草稿，也不增加原条款输出。</Notice>}
    {!status.compatible&&<Notice tone="error">{status.reason}</Notice>}
    <details><summary>固定父包来源摘要</summary><code>{d.parent_source_digest || '未提供'}</code></details>
    <label className="check"><input type="checkbox" checked={reported} disabled={!status.compatible} onChange={event=>update(release,event.target.checked,rules)}/>提取报告直接披露</label>
    {(d.derivation_rules || []).map((rule:Row)=><label className="check" key={rule.rule_id}><input type="checkbox" checked={rules.includes(rule.rule_id)} disabled={!status.compatible} onChange={event=>update(release,reported,event.target.checked?[...rules,rule.rule_id]:rules.filter(id=>id!==rule.rule_id))}/>审核后计算：{(rule.operands || []).map((operand:Row)=>operand.source_datapoint_id).join(' ＋ ')}</label>)}
   </article>;
  })}
 </details>;
}
