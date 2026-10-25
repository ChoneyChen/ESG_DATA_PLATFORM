import {useResource} from '../state';
import {ResourceState} from './ui';

/**
 * Read-only connection state for the local CrawlESG export. The console never
 * runs the crawler, writes its index, or treats an ESEF package as a PDF.
 */
export function CrawlConnection(){
 const status=useResource('document','/api/console/crawl-index/status');
 const value=status.data;
 return <details className="crawl-connection">
  <summary><span>CrawlESG 报告源</span><small>{value?.available?`索引 ${value.record_count ?? 0} 条 · 已下载 ${value.downloaded_count ?? 0} 个 ESEF 包`:'未连接'}</small></summary>
  <ResourceState error={status.error} loading={status.loading&&!value}/>
  <p className="muted">只读接口。CrawlESG 负责采集申报索引与下载 ESEF ZIP；本链路的 PDF、报告身份、候选与发布库保持独立，导入包不会自动变成 PDF 资产或候选。</p>
  <dl className="facts">
   <dt>索引文件</dt><dd className="mono">{value?.index_path || '未配置'}</dd>
   <dt>下载台账</dt><dd className="mono">{value?.ledger_path || '未配置'}（{value?.download_ledger_available?'可读':'不可读'}）</dd>
   <dt>包目录</dt><dd className="mono">{value?.package_dir || '未配置'}</dd>
   <dt>产物类型</dt><dd>ESEF ZIP（不是 PDF）</dd>
  </dl>
 </details>;
}
