import type {Row} from './api';

export type ReportReviewUnit={
 ref:string;
 title:string;
 publisher:string;
 code:string;
 total:number;
 pending:number;
 reviewed:number;
 blocked:number;
 published:number;
 approvedCount:number;
 publicationStatus:string;
 publicationVersion:number;
 progress:number;
 state:'blocked'|'published'|'ready'|'review';
};

function reportMap(reports:Row[]){
 const map=new Map<string,Row>();
 for(const report of reports){
  const ref=report.report_id || report.report_ref;
  if(ref)map.set(String(ref),report);
 }
 return map;
}
function metadata(report?:Row){return (report?.metadata || report?.report_metadata || {}) as Row;}
function reportTitle(report:Row|undefined,fallback:string){
 const meta=metadata(report);
 return String(meta.title || report?.title || report?.report_title_snapshot || fallback);
}
function reportCode(report:Row|undefined,meta:Row){
 if(report?.report_code)return String(report.report_code);
 return meta.publisher_entity_ref&&meta.reporting_year?`${meta.publisher_entity_ref}/${meta.reporting_year}`:'';
}

/** One publication/review unit per report, with evidence rows kept inside it. */
export function reviewReportUnits(candidates:Row[],reports:Row[]=[],publications:Row[]=[]):ReportReviewUnit[]{
 const known=reportMap(reports),groups=new Map<string,ReportReviewUnit>();
 const publicationMap=new Map(publications.map(item=>[String(item.report_ref || ''),item]));
 for(const candidate of candidates){
  const ref=String(candidate.report_ref || '');
  const report=known.get(ref);
  const meta=metadata(report);
  const title=reportTitle(report,String(candidate.report_title_snapshot || ref || '未绑定报告'));
  const publisher=String(candidate.organization_ref || meta.publisher_entity_ref || '发布者未记录');
  const unit=groups.get(ref) || {ref,title,publisher,code:reportCode(report,meta),total:0,pending:0,reviewed:0,blocked:0,published:0,approvedCount:0,publicationStatus:'draft',publicationVersion:0,progress:0,state:'review'};
  unit.total+=1;
  const approved=candidate.fact_review_status==='approved'&&candidate.mapping_review_status==='approved'&&Boolean(candidate.context);
  if(approved)unit.reviewed+=1;
  else if(candidate.reason_codes?.length||candidate.publication_status==='blocked')unit.blocked+=1;
  else unit.pending+=1;
  groups.set(ref,unit);
 }
 return [...groups.values()].map(unit=>{
  const publication=publicationMap.get(unit.ref);
  const publicationStatus=String(publication?.status || 'draft');
  const approvedCount=Number(publication?.approved_count ?? unit.reviewed);
  const published=publicationStatus==='published'?Number(publication?.observation_count || 0):0;
  const reviewed=Math.max(0,approvedCount-published);
  const state:ReportReviewUnit['state']=publicationStatus==='published'?'published'
   :unit.blocked?'blocked':approvedCount>0?'ready':'review';
  return {...unit,publicationStatus,publicationVersion:Number(publication?.publication_version || 0),
   approvedCount,reviewed,published,
   progress:unit.total?Math.round(((reviewed+published)/unit.total)*100):0,
   state};
 }).sort((a,b)=>(b.blocked-a.blocked)||(b.pending-a.pending)||a.title.localeCompare(b.title,'zh-CN'));
}
