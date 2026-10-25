import type {ReactNode} from 'react';
import type {ReportReviewUnit} from '../reviewReports';

export function ReviewReportCard({unit,onOpen,action='审核这份报告',hint,actions,children}:{unit:ReportReviewUnit,onOpen?:()=>void,action?:string,hint?:ReactNode,actions?:ReactNode,children?:ReactNode}){
 const stateLabel=unit.state==='blocked'?'有阻塞':unit.state==='published'?'已发布':unit.state==='ready'?'待发布':unit.pending?'待审核':'已核对';
 return <article className={`review-report-card ${unit.state}`}>
  <header>
   <div className="review-report-id"><span className="review-report-icon" aria-hidden="true">R</span><div><h3>{unit.title}</h3><small>{unit.publisher}{unit.code?` · ${unit.code}`:''}</small></div></div>
   <span className={`review-report-state ${unit.state}`}>{stateLabel}{unit.publicationVersion>0?` · 第 ${unit.publicationVersion} 版`:''}</span>
  </header>
  <div className="review-report-counts"><span><strong>{unit.total}</strong>候选</span><span className={unit.pending?'warning':''}><strong>{unit.pending}</strong>待审核</span><span className={unit.blocked?'critical':''}><strong>{unit.blocked}</strong>阻塞</span><span><strong>{unit.approvedCount}</strong>已审核</span><span><strong>{unit.published}</strong>已发布</span></div>
  {hint&&<div className="review-report-auto">{hint}</div>}
  <div className="review-report-progress"><span><i style={{width:`${unit.progress}%`}}/></span><small>{unit.progress}% 已核对</small></div>
  <div className="review-report-footer"><small className="mono">{unit.ref || '报告引用未记录'}</small><span className="review-report-actions">{actions}{onOpen&&<button type="button" className="secondary" onClick={onOpen}>{action}</button>}</span></div>
  {children&&<div className="review-report-body">{children}</div>}
 </article>;
}
