import type {Row} from './api';

/** Review work is grouped by why it is still open, not by table position. */
export type ReviewReasonGroup={key:string;label:string;hint:string;rows:Row[]};

const GROUPS:Array<{key:string;label:string;hint:string;match:RegExp}>=[
 {key:'conflict',label:'数值与来源冲突',hint:'同期间同边界出现多个值，或同一单元格映射到互斥条款',match:/conflict|consistent_value|single_source_mapping|duplicate/i},
 {key:'unit',label:'单位与分母',hint:'报告单位未知、不能安全换算，或分母口径不一致',match:/unit|normaliz|denominator/i},
 {key:'context',label:'主体、期间与边界',hint:'测量主体、报告期或边界尚未核对',match:/context|missing_entity|missing_period|missing_boundary|missing_required|reviewed_context/i},
 {key:'mapping',label:'条款映射',hint:'披露含义与条款不完全对应，或映射关系未确认',match:/mapping|not_equivalent|broader|narrower|partial|unsupported/i},
 {key:'fact',label:'事实与证据',hint:'数值或陈述缺失、来源不可读或已被替代',match:/fact_|statement_missing|numeric_value|physical_source|source_not_available|superseded|source_unit/i},
];

function bucket(row:Row):string{
 // ``publication_not_evaluated`` only says the gate has not run; it is not a
 // review reason and must not hide the actual missing step.
 const codes:string[]=((row.reason_codes || []) as unknown[])
  .map((code:unknown)=>String(code))
  .filter((code:string)=>code!=='publication_not_evaluated');
 for(const group of GROUPS){
  if(codes.some(code=>group.match.test(code)))return group.key;
 }
 if(codes.length)return 'other';
 // No explicit reason code: the missing review step is the grouping.
 if(row.fact_review_status && row.fact_review_status !== 'approved')return 'fact';
 if(row.mapping_review_status && row.mapping_review_status !== 'approved')return 'mapping';
 if(!row.context)return 'context';
 return 'other';
}

export function groupReviewCandidates(rows:Row[]):ReviewReasonGroup[]{
 const buckets=new Map<string,Row[]>();
 for(const row of rows){
  const key=bucket(row);
  buckets.set(key,[...(buckets.get(key) || []),row]);
 }
 const ordered=[...GROUPS.map(group=>({...group,rows:buckets.get(group.key) || []})),
  {key:'other',label:'其他',hint:'尚未归类的阻塞原因',rows:buckets.get('other') || []}]
  .filter(group=>group.rows.length>0)
  .map(({key,label,hint,rows})=>({key,label,hint,rows}));
 return ordered;
}
