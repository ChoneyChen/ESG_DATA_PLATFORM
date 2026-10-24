import {useMemo, useState} from 'react';
import {credentials, items, post, type Row} from '../api';
import {useResource, useWorkspace} from '../state';
import {Badge, Notice, ResourceState, Section} from './ui';
import {blockerText} from '../publicationReasons';

/**
 * Machine verification: approve only the candidates whose every publication
 * requirement is already proven by the delivered evidence. The panel shows the
 * dry run first, so an operator sees which clauses are ready and which checks
 * still need a person; publishing remains a separate explicit action.
 */
const CHECK_TEXT: Record<string, string> = {
  metric_ready: '指标定义未就绪',
  mapping_exact: '映射尚未被证据判定为完全对应',
  physical_source: '缺少原文来源',
  value_numeric: '缺少可核对数值',
  period_present: '报告期未确定',
  entity_registered: '报告主体未登记',
  boundary_resolved: '报告边界未确定',
  unit_supported: '原单位无法换算',
  dimensions_complete: '测量维度仍需人工确认',
};

export function MachineVerification({reportRef}: {reportRef?: string}) {
  const {refresh} = useWorkspace();
  const query = reportRef ? `?report_ref=${encodeURIComponent(reportRef)}` : '';
  const proposals = useResource('publication', `/internal/v1/candidate-proposals${query}`, true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const rows: Row[] = items(proposals.data);
  const pending = rows.filter(row => !row.already_approved);
  const ready = pending.filter(row => row.resolvable);
  const blocked = pending.filter(row => !row.resolvable);
  const approved = rows.length - pending.length;
  const blockers = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const row of blocked) for (const code of row.blockers || []) counts[code] = (counts[code] || 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [blocked]);

  const apply = async () => {
    setBusy(true); setMessage('');
    try {
      const result = await post('publication', '/internal/v1/machine-verification', {
        ...(reportRef ? {report_ref: reportRef} : {}),
        apply: true,
        reason: '按证据逐条核验：指标就绪、映射完全对应、期间与主体可证、边界与单位可核、所需维度齐全。',
      });
      setMessage(`已按证据批准 ${result.applied ?? 0} 条；${result.blocked ?? 0} 条仍需人工审核。正式发布仍需点击“处理待发布事件”。`);
      refresh();
    } catch (error) {
      setMessage((error as Error).message);
    } finally { setBusy(false); }
  };

  return (
    <Section title="机器核验">
      <p className="muted">
        只批准证据能证明全部发布条件的候选：指标就绪、映射完全对应、期间与主体可证、边界与单位可核、维度齐全。
        其余保留人工审核，并在下方列出缺少项。
      </p>
      <ResourceState error={proposals.error} loading={proposals.loading && !proposals.data}/>
      <div className="stat-grid">
        <article><small>可机器核验</small><strong>{ready.length}</strong></article>
        <article><small>仍需人工审核</small><strong>{blocked.length}</strong></article>
        <article><small>已批准</small><strong>{approved}</strong></article>
        <article><small>当前范围</small><strong>{reportRef ? '当前报告' : '全部候选'}</strong></article>
      </div>
      {blockers.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead><tr><th>仍需人工的原因</th><th>条数</th></tr></thead>
            <tbody>{blockers.map(([code, count]) => <tr key={code}><td>{CHECK_TEXT[code] || blockerText(code)}</td><td>{count}</td></tr>)}</tbody>
          </table>
        </div>
      )}
      <button type="button" disabled={busy || !credentials.ready || !ready.length} onClick={apply}>
        {busy ? '核验中…' : `核验并批准 ${ready.length} 条`}
      </button>
      {message && <Notice>{message}</Notice>}
      {ready.length > 0 && (
        <details>
          <summary>将批准的候选（{ready.length}）</summary>
          <ul>{ready.map(row => <li key={row.mapping_id}><strong>{row.source_datapoint_id}</strong> · {row.raw_indicator_name} · {row.raw_value_text} {row.raw_unit || ''} <Badge value={row.mapping_relation}/></li>)}</ul>
        </details>
      )}
    </Section>
  );
}
