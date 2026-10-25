import {useState} from 'react';
import {items,type Row} from '../api';
import {useResource} from '../state';
import {Notice,ResourceState} from './ui';

/** Read-only filing lookup. Choosing a row is an explicit draft edit, never an import. */
export function CrawlIndexPicker({onSelect}:{onSelect:(candidate:Row)=>void}){
 const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[year,setYear]=useState(''),[submitted,setSubmitted]=useState('');
 const path=open?`/api/console/crawl-index?query=${encodeURIComponent(submitted)}${year?`&fiscal_year=${encodeURIComponent(year)}`:''}&limit=30`:null;
 const resource=useResource('document',path);
 const candidates=items(resource.data);
 return <div className="crawl-picker">
  <button type="button" className="secondary" onClick={()=>{setOpen(value=>!value);setSubmitted(query);}}>{open?'收起本地报告索引':'从本地 CrawlESG 索引查找'}</button>
  {open&&<div className="crawl-picker-body">
   <p className="muted">只读匹配索引；索引只提供申报身份，下载包是 ESEF ZIP，不是本链路的 PDF 资产。选择后仍需确认报告名称、主体引用与报告期。</p>
   <form className="crawl-picker-search" onSubmit={event=>{event.preventDefault();setSubmitted(query);}}>
    <input aria-label="搜索外部报告索引" placeholder="企业名称、LEI 或索引编号" value={query} onChange={event=>setQuery(event.target.value)}/>
    <input aria-label="外部报告财年" inputMode="numeric" placeholder="财年" value={year} onChange={event=>setYear(event.target.value.replace(/[^0-9]/g,'').slice(0,4))}/>
    <button type="submit" className="secondary">查询</button>
   </form>
   <ResourceState error={resource.error} loading={resource.loading&&!resource.data}/>
   {resource.data?.available===false&&<Notice>本地没有可用 CrawlESG 索引。当前仍可手动填写报告身份。</Notice>}
   {resource.data?.available&&<small className="muted">索引 {resource.data.record_count} 条 · 已下载 {resource.data.downloaded_count || 0} 个 ESEF 包 · 仅显示前 {candidates.length} 条匹配</small>}
   <div className="crawl-candidates">{candidates.map(candidate=><article key={`${candidate.source_report_id}:${candidate.period_end}`}>
    <div><strong>{candidate.entity_name || '未命名主体'}</strong><small>{candidate.fiscal_year || '财年未提供'} · 截止 {candidate.period_end || '未提供'} · {candidate.lei || '无 LEI'}</small><small>{candidate.report_type || '报告类型未提供'} · {candidate.source_report_id}</small></div>
    <div className="crawl-package-state">
     <span className={candidate.package_available?'chip ready':'chip'}>{candidate.package_available?`包已下载${candidate.package_size_mb?` · ${candidate.package_size_mb} MB`:''}`:candidate.download_status==='file_missing'?'台账有声明的包但文件缺失':'包未下载'}</span>
     {candidate.package_filename&&<small className="mono" title={candidate.package_filename}>{candidate.package_filename}</small>}
     {candidate.viewer_url&&<a href={candidate.viewer_url} target="_blank" rel="noreferrer">公开查看页</a>}
    </div>
    <button type="button" className="secondary" onClick={()=>onSelect(candidate)}>采用此索引</button>
   </article>)}</div>
   {resource.data?.available&&submitted&&!resource.loading&&!candidates.length&&<p className="muted">没有匹配记录。请尝试企业英文名或 LEI。</p>}
  </div>}
 </div>;
}
