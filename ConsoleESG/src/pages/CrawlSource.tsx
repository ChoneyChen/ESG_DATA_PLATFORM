import {useMemo,useState} from 'react';
import {items,navigate,type Row} from '../api';
import {useResource} from '../state';
import {Notice,ResourceState,Section} from '../components/ui';

/**
 * CrawlESG is a read-only filing catalogue. The current local export has an
 * index and ESEF packages, not PDF assets, so this page only exposes metadata
 * and the reserved PDF handoff contract. It never runs the crawler or imports
 * a filing into the PDF chain.
 */
export function CrawlSource(){
 const status=useResource('document','/api/console/crawl-index/status');
 const [query,setQuery]=useState(''),[year,setYear]=useState(''),[submitted,setSubmitted]=useState('');
 const path=`/api/console/crawl-index?query=${encodeURIComponent(submitted)}${year?`&fiscal_year=${encodeURIComponent(year)}`:''}&limit=50`;
 const reports=useResource('document',path);
 const rows=items(reports.data);
 const counts=useMemo(()=>({index:status.data?.record_count ?? 0,pdf:status.data?.pdf_available===true}),[status.data]);
 return <>
  <div className="page-heading"><div><span className="eyebrow">报告来源</span><h1>CrawlESG 报告源</h1><p>只读索引用于确认企业、财年与报告编号。Crawl 当前没有可直接导入的 PDF；PDF 交接接口保留，不把 ESEF 包当作 PDF。</p></div><div className="button-row"><button className="secondary" onClick={()=>navigate('/reports')}>返回报告库</button></div></div>
  <div className="stat-grid">
   <article><small>索引记录</small><strong>{status.error?'—':counts.index}</strong></article>
   <article><small>PDF 文件</small><strong>{counts.pdf?'可用':'未提供'}</strong></article>
   <article><small>来源模式</small><strong>{status.data?.mode || '只读'}</strong></article>
   <article><small>索引状态</small><strong>{status.data?.available?'可读':'未连接'}</strong></article>
  </div>
  <ResourceState error={status.error}/>
  <Section title="报告索引">
   <Notice>索引只提供申报身份，不提供 PDF、标题或发布主体引用。采用索引时仍由报告身份编辑器逐项确认。</Notice>
   <form className="crawl-source-search" onSubmit={event=>{event.preventDefault();setSubmitted(query);}}>
    <input aria-label="搜索 Crawl 报告索引" placeholder="企业名称、LEI 或报告编号" value={query} onChange={event=>setQuery(event.target.value)}/>
    <input aria-label="Crawl 财年" inputMode="numeric" placeholder="财年" value={year} onChange={event=>setYear(event.target.value.replace(/[^0-9]/g,'').slice(0,4))}/>
    <button type="submit" className="secondary">查询</button>
   </form>
   {status.data?.available===false&&<Notice>本地没有可用 CrawlESG 索引；仍可在报告身份编辑器中手动填写。</Notice>}
   {status.data?.available&&<small className="muted">索引 {status.data.record_count} 条 · 当前显示 {rows.length} 条 · 只读</small>}
   <ResourceState error={reports.error} loading={reports.loading&&!reports.data} empty={!!reports.data&&!rows.length}/>
   {rows.length>0&&<div className="table-scroll crawl-source-table"><table><thead><tr><th>企业</th><th>LEI</th><th>财年 / 期末</th><th>报告类型</th><th>索引编号</th><th>公开页</th></tr></thead><tbody>
    {rows.map((row:Row)=><tr key={`${row.source_report_id}:${row.period_end}`}>
     <td data-label="企业"><strong>{row.entity_name || '未命名企业'}</strong></td>
     <td data-label="LEI" className="mono">{row.lei || '—'}</td>
     <td data-label="财年 / 期末">{row.fiscal_year || '—'}<small className="table-secondary">{row.period_end || '期末未提供'}</small></td>
     <td data-label="报告类型">{row.report_type || '—'}</td>
     <td data-label="索引编号" className="mono">{row.source_report_id || '—'}</td>
     <td data-label="公开页">{row.viewer_url?<a href={row.viewer_url} target="_blank" rel="noreferrer">查看来源</a>:<span className="muted">无</span>}</td>
    </tr>)}
   </tbody></table></div>}
   <p className="muted">在报告详情的“报告身份”中打开“从本地 CrawlESG 索引查找”，选择记录只会填入索引编号、LEI、财年与来源链接；发布主体、报告期起始与测量主体仍需人工确认。</p>
  </Section>
  <Section title="PDF 交接接口（预留）">
   <Notice>当前本地 Crawl 导出只有索引与 ESEF 包，没有可直接进入本链路 PDF 资产库的文件。因此本页不提供导入按钮，也不读取 ESEF 内容。接口保留如下，待 Crawl 侧提供 PDF 后再实现。</Notice>
   <dl className="facts">
    <dt>来源系统</dt><dd>CrawlESG</dd>
    <dt>报告编号</dt><dd className="mono">source_report_id</dd>
    <dt>PDF 文件</dt><dd className="mono">pdf_path（服务端本地路径）</dd>
    <dt>文件身份</dt><dd className="mono">pdf_sha256（64 位十六进制）</dd>
    <dt>报告元数据</dt><dd>entity_name、lei、period_end、fiscal_year、report_type、viewer_url</dd>
    <dt>接收方</dt><dd>报告资源：PDF 资产 + 报告身份；候选与发布仍由现有审核链路完成</dd>
   </dl>
   <button type="button" className="secondary" disabled title="待 Crawl 提供 PDF 文件后启用">导入 PDF（接口预留）</button>
  </Section>
 </>;
}
