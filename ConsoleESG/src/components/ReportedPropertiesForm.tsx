import type {Row} from '../api';
import {Field,Notice} from './ui';

export interface PropertySelection {source_field:string;ordinal:number;label_zh:string}
const identity = (value:{source_field:string;ordinal?:number}) => `${value.source_field}:${value.ordinal || 1}`;

/** Values and evidence remain server-owned; this form only selects and labels. */
export function ReportedPropertiesForm({available,selections,onChange}:{
 available:Row[];selections:PropertySelection[];onChange:(values:PropertySelection[])=>void;
}) {
 const selected = new Map(selections.map(value=>[identity(value),value]));
 if (!available.length) return null;
 return <details className="reported-properties-review"><summary>报告补充信息（{selections.length} 项待发布）</summary>
  <Notice>选择需要展示的报告原始属性，并确认中文名称。原值与证据由后端读取；这些说明不会自动成为比较口径。未选属性继续保留在候选库。</Notice>
  {available.map((property,index)=>{
   const key=identity(property as PropertySelection),choice=selected.get(key);
   const name=property.label_suggestion || property.field_key || `补充信息 ${index+1}`;
   return <article className="task-card" key={key}>
    <label><input type="checkbox" disabled={!property.selectable} checked={!!choice}
     onChange={event=>onChange(event.target.checked
      ? [...selections,{source_field:property.source_field,ordinal:property.ordinal || 1,label_zh:property.label_suggestion || ''}]
      : selections.filter(value=>identity(value)!==key))}/>{name}</label>
    <p className="raw-text">{Array.isArray(property.value_raw)?property.value_raw.join('、'):property.value_raw ?? '此属性尚未形成可发布的结构化原值'}</p>
    <small>{property.evidence_scope==='field'?'字段证据已定位':'来源级说明，需核对所选属性与原文的关系'}</small>
    {(property.evidence || []).map((evidence:Row,i:number)=><blockquote key={i}>{evidence.source_quote}<footer>{evidence.page_number?`PDF 第 ${evidence.page_number} 页`:'页码未记录'}</footer></blockquote>)}
    {choice&&<Field label={`补充信息中文名称：${name}`}><input required maxLength={160} value={choice.label_zh}
     onChange={event=>onChange(selections.map(value=>identity(value)===key?{...value,label_zh:event.target.value}:value))}/></Field>}
    {!property.selectable&&<p className="muted">原始内容已保留，当前类型或证据尚不满足发布要求。</p>}
   </article>;
  })}
 </details>;
}
