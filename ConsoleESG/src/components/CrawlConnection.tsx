import {navigate} from '../api';
import {useResource} from '../state';
import {ResourceState} from './ui';

/**
 * Read-only CrawlESG index state. The current source has no PDF handoff, so
 * this panel must not imply that an ESEF package is a usable PDF asset.
 */
export function CrawlConnection(){
 const status=useResource('document','/api/console/crawl-index/status');
 const value=status.data;
 return <details className="crawl-connection">
  <summary><span>CrawlESG 报告源</span><small>{value?.available?`索引 ${value.record_count ?? 0} 条 · PDF ${value.pdf_available?'可用':'未提供'}`:'未连接'}</small></summary>
  <ResourceState error={status.error} loading={status.loading&&!value}/>
  <p className="muted">只读索引用于确认企业、财年与报告编号；当前 Crawl 导出没有可直接导入的 PDF。索引不会自动生成报告身份或候选。</p>
  <dl className="facts">
   <dt>索引文件</dt><dd className="mono">{value?.index_path || '未配置'}</dd>
   <dt>PDF 文件</dt><dd>{value?.pdf_available?'可用':'未提供（接口预留）'}</dd>
   <dt>来源模式</dt><dd>只读</dd>
  </dl>
  <div className="button-row"><button type="button" className="secondary" onClick={()=>navigate('/crawl')}>打开 Crawl 报告源</button></div>
 </details>;
}
