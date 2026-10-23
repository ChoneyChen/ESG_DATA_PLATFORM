import {useEffect, useMemo, useState} from 'react';
import {navigate, type Row} from '../api';
import {useResource} from '../state';
import {Notice, ResourceState, Section} from './ui';
import {coverageText} from '../pipeline';
import '../foundation.css';

const STATUS_TONE: Record<string, string> = {
  found: 'ok',
  review: 'review',
  conflict: 'conflict',
  not_found_in_selected_sources: 'none',
  only_rejected_or_other_period: 'none',
};

const BOUNDARY_WORD: Record<string, string> = {
  group: '集团／合并口径',
  battery_production_bases: '电池生产基地',
  subsidiary: '子公司',
  overseas: '海外',
  hong_kong: '香港',
  mainland: '中国内地',
};

const STATUS_WORD: Record<string, string> = {
  found: '已找到',
  review: '待复核',
  conflict: '冲突',
  not_found_in_selected_sources: '未披露',
  only_rejected_or_other_period: '仅有被拒候选',
};

const CHILD_RELATION: Record<string, string> = {
  component: '分项',
  related_measure: '相关度量',
};

const SOURCE_KINDS: [string, string][] = [
  ['agreeing_sources', '一致来源'],
  ['conflicting_sources', '冲突来源'],
  ['alternative_unit_sources', '其他单位'],
  ['related_subset_sources', '相关子口径'],
  ['component_sources', '分项构成'],
];

/** Filter keys are outcome words, not colour tones: the counts must match. */
function bucketOf(status: string) {
  if (status === 'found') return 'found';
  if (status === 'review') return 'review';
  if (status === 'conflict') return 'conflict';
  return 'none';
}

const FILTERS: [string, string][] = [
  ['all', '全部'],
  ['found', '已找到'],
  ['review', '待复核'],
  ['conflict', '冲突'],
  ['none', '未披露'],
];

export type Conclusion = Row & {code: string; status: string; group: string; parent_code?: string | null; children?: Row[]};

/** Re-exported so the board, the extraction preview and reports share one reading vocabulary. */
export {readingText} from '../pipeline';

function locate(irRunId: string, source: Row) {
  const object = source.cell_id || source.note_block_id || source.table_id || '';
  navigate(`/processing?ir=${encodeURIComponent(irRunId)}&page=${Number(source.page_number) - 1}&object=${encodeURIComponent(object)}`);
}

function valueText(row: Row) {
  const raw = row.value_raw ?? '—';
  const mark = row.comparator === 'greater_than' ? (String(raw).includes('≥') ? '' : '≥ ')
    : row.comparator === 'less_than' ? (String(raw).includes('≤') ? '' : '≤ ')
    : row.comparator === 'about' ? (String(raw).includes('约') ? '' : '约 ') : '';
  return `${mark}${raw}${row.unit_raw ? ` ${row.unit_raw}` : ''}`;
}

function statusText(conclusion: Conclusion) {
  return conclusion.child_only ? '已找到分项' : (STATUS_WORD[conclusion.status] || conclusion.status_label || conclusion.status);
}

function summaryOf(conclusion: Conclusion) {
  const members: Row[] = conclusion.members || [];
  if (members.length > 1) return members.map(m => `${m.member ?? ''} ${valueText(m)}`).join(' · ');
  if (members.length === 1) return valueText(members[0]);
  if (conclusion.statements?.length) return conclusion.statements[0].text;
  return '';
}

function packageTitle(conclusions: Conclusion[], key: string) {
  const row = conclusions.find(c => String(c.package_id || '') === key);
  return row?.group_label ? `${row.group_label} · ${key}` : key;
}

function childValue(child: Row) {
  if (child.values) return (child.values as Row[]).map(v => `${v.member ? `${v.member} ` : ''}${v.value_raw ?? '—'}${v.unit_raw ? ` ${v.unit_raw}` : ''}`).join('；');
  return `${child.value_raw ?? '—'}${child.unit_raw ? ` ${child.unit_raw}` : ''}`;
}

function Locate({irRunId, source}: {irRunId: string; source?: Row}) {
  if (!source) return null;
  return <button type="button" className="text-button" onClick={() => locate(irRunId, source)}>定位原文</button>;
}

/* ==========================================================================
   CSV
   ========================================================================== */

const CSV_COLUMNS = ['原编号', '指标', '结论', '成员', '报告原值', '单位', '期间', '页码', '来源行', '说明',
  '子项', '子项关系', '子项原值', '子项单位', '子项页码'];

function csvCell(value: unknown) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function foundationCsv(conclusions: Conclusion[]) {
  const rows: unknown[][] = [];
  for (const c of conclusions) {
    const members: Row[] = c.members || [];
    const children: Row[] = c.children || [];
    const base = (member: Row | null) => [
      c.code, c.label, c.status_label, member?.member ?? '', member?.value_raw ?? '', member?.unit_raw ?? '',
      member?.period ?? '', member?.source?.page_number ?? '', member?.source?.row_label ?? '',
      member ? (member.flag_labels || []).join('；') : '',
    ];
    if (!members.length) {
      const statement = c.statements?.[0];
      rows.push([...base(null), '', '', statement ? statement.text : '', '', '']);
    } else {
      for (const member of members) rows.push([...base(member), '', '', '', '', '']);
    }
    for (const child of children) {
      rows.push([c.code, c.label, statusText(c), '', '', '', child.period ?? '',
        child.source?.page_number ?? '', child.source?.row_label ?? '', '',
        child.code || child.label, CHILD_RELATION[String(child.relation)] || '', child.value_raw ?? '', child.unit_raw ?? '',
        child.source?.page_number ?? '']);
    }
  }
  return [CSV_COLUMNS, ...rows].map(row => row.map(csvCell).join(',')).join('\n');
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob(['﻿' + text], {type: 'text/csv;charset=utf-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/* ==========================================================================
   Detail tables
   ========================================================================== */

function ClauseDetail({conclusion, irRunId}: {conclusion: Conclusion; irRunId: string}) {
  const members: Row[] = conclusion.members || [];
  const children: Row[] = conclusion.children || [];
  const views: Row[] = conclusion.views || [];
  const statements: Row[] = conclusion.statements || [];
  const candidates: Row[] = conclusion.candidates || [];

  const sourceRows = members.flatMap((member: Row, index: number) => {
    const rows = [{...member.source, member: member.member, role: '主证据'}];
    for (const [key, caption] of SOURCE_KINDS) {
      for (const source of member[key] || []) rows.push({...source, member: member.member, role: caption});
    }
    return rows.map((row, order) => ({...row, key: `${index}-${order}`}));
  });

  return (
    <div className="f16-detail-pane">
      <header className="f16-detail-head">
        <div>
          <span className="f16-code-badge">{conclusion.code}</span>
          <h2>{conclusion.label}</h2>
          {conclusion.package_id && <small className="mono">{conclusion.package_id}{conclusion.metric_id ? ` · ${conclusion.metric_id}` : ''}</small>}
        </div>
        <span className={`f16-status ${STATUS_TONE[conclusion.status] || 'none'}`}>{statusText(conclusion)}</span>
      </header>

      {conclusion.parent_code && <p className="f16-parent">子项 · 属于 {conclusion.parent_code}</p>}
      {conclusion.definition_zh && <p className="f16-definition">{conclusion.definition_zh || conclusion.definition}</p>}

      {members.length > 0 && (
        <section className="f16-block">
          <h4>主值（{members.length}）</h4>
          <div className="table-scroll">
            <table className="f16-table">
              <thead><tr>
                <th>成员 / 口径</th><th>状态</th><th>原值</th><th>单位</th><th>期间</th><th>页码</th>
                <th>一致</th><th>冲突</th><th>其他单位</th><th>子口径</th><th>分项</th>
              </tr></thead>
              <tbody>
                {members.map((member: Row, index: number) => {
                  const tone = member.status === 'accepted' ? 'found' : (member.status || 'review');
                  return <tr key={index}>
                    <td><strong>{member.member || '合计'}</strong></td>
                    <td><span className={`f16-status ${STATUS_TONE[tone] || 'review'}`}>{STATUS_WORD[tone] || '待复核'}</span></td>
                    <td className="metric-value">{valueText(member)}</td>
                    <td>{member.unit_raw || '—'}</td>
                    <td>{member.period || '—'}</td>
                    <td>{member.source?.page_number ?? '—'}</td>
                    <td>{(member.agreeing_sources || []).length}</td>
                    <td>{(member.conflicting_sources || []).length}</td>
                    <td>{(member.alternative_unit_sources || []).length}</td>
                    <td>{(member.related_subset_sources || []).length}</td>
                    <td>{(member.component_sources || []).length}</td>
                  </tr>;
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {children.length > 0 && (
        <section className="f16-block">
          <h4>子表（{children.length}）</h4>
          <p className="muted">同一披露下的分项或相关度量，已单独保留原值与页码；它们不替代主值。</p>
          <div className="table-scroll">
            <table className="f16-table">
              <thead><tr><th>子项</th><th>关系</th><th>原值</th><th>单位</th><th>期间</th><th>页码</th><th>操作</th></tr></thead>
              <tbody>
                {children.map((child: Row, index: number) => (
                  <tr key={`${child.code || child.label}-${index}`}>
                    <td><strong>{child.code || child.label}</strong>{child.code && <small className="f16-attr">{child.label}</small>}</td>
                    <td>{CHILD_RELATION[String(child.relation)] || (child.status ? (STATUS_WORD[String(child.status)] || '子项') : '子项')}</td>
                    <td className="metric-value">{childValue(child)}</td>
                    <td>{child.unit_raw || '—'}</td>
                    <td>{child.period || (child.values?.[0]?.period) || '—'}</td>
                    <td>{child.source?.page_number ?? child.values?.[0]?.source?.page_number ?? '—'}</td>
                    <td><Locate irRunId={irRunId} source={child.source || child.values?.[0]?.source}/></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {views.length > 0 && (
        <section className="f16-block">
          <h4>同一指标的其他报告口径（{views.length}）</h4>
          <p className="muted">每个口径各自成为候选；交付后在审核中心逐条确认边界，不合并为一个观测。</p>
          <div className="table-scroll">
            <table className="f16-table">
              <thead><tr><th>口径</th><th>边界代码</th><th>状态</th><th>原值</th><th>单位</th><th>期间</th><th>页码</th><th>操作</th></tr></thead>
              <tbody>
                {views.map((view: Row, index: number) => (
                  <tr key={`${view.boundary_label}-${index}`}>
                    <td><strong>{BOUNDARY_WORD[String(view.boundary_label)] || String(view.boundary_label)}</strong></td>
                    <td className="mono">{view.boundary_label || '—'}</td>
                    <td><span className={`f16-status ${STATUS_TONE[view.status] || 'review'}`}>{STATUS_WORD[view.status] || '待复核'}</span></td>
                    <td className="metric-value">{valueText(view)}</td>
                    <td>{view.unit_raw || '—'}</td>
                    <td>{view.period || '—'}</td>
                    <td>{view.source?.page_number ?? '—'}</td>
                    <td><Locate irRunId={irRunId} source={view.source}/></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {sourceRows.length > 0 && (
        <section className="f16-block">
          <h4>来源明细（{sourceRows.length}）</h4>
          <div className="table-scroll">
            <table className="f16-table">
              <thead><tr><th>成员</th><th>角色</th><th>原值</th><th>单位</th><th>期间</th><th>页码</th><th>来源行</th><th>说明</th><th>操作</th></tr></thead>
              <tbody>
                {sourceRows.map((source: Row) => (
                  <tr key={source.key}>
                    <td>{source.member || '合计'}</td>
                    <td>{source.role}</td>
                    <td className="metric-value">{valueText(source)}</td>
                    <td>{source.unit_raw || '—'}</td>
                    <td>{source.period || '—'}</td>
                    <td>{source.page_number ?? '—'}</td>
                    <td>{source.row_label || '—'}</td>
                    <td className="f16-reason-cell">{(source.flag_labels || []).join('；')}</td>
                    <td><Locate irRunId={irRunId} source={source}/></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {statements.length > 0 && (
        <section className="f16-block">
          <h4>陈述（{statements.length}）</h4>
          <div className="table-scroll">
            <table className="f16-table">
              <thead><tr><th>页码</th><th>原文陈述</th><th>操作</th></tr></thead>
              <tbody>
                {statements.map((statement: Row) => (
                  <tr key={statement.block_id}>
                    <td>{statement.page_number ?? '—'}</td>
                    <td>{statement.text}</td>
                    <td><Locate irRunId={irRunId} source={{page_number: statement.page_number, note_block_id: statement.block_id}}/></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!members.length && !statements.length && (
        <section className="f16-block">
          <h4>未采用候选（{candidates.length}）</h4>
          <p className="muted">
            在所选表格与正文段落中没有符合本条款定义的主值。
            {conclusion.rejection_reasons?.length ? `已排除原因：${conclusion.rejection_reasons.join('；')}。` : ''}
          </p>
          {candidates.length > 0 && (
            <div className="table-scroll">
              <table className="f16-table">
                <thead><tr><th>原值</th><th>单位</th><th>期间</th><th>页码</th><th>来源行</th><th>原因</th><th>操作</th></tr></thead>
                <tbody>
                  {candidates.map((candidate: Row, index: number) => (
                    <tr key={index}>
                      <td className="metric-value">{valueText(candidate)}</td>
                      <td>{candidate.unit_raw || '—'}</td>
                      <td>{candidate.period || '—'}</td>
                      <td>{candidate.page_number ?? '—'}</td>
                      <td>{candidate.row_label || '—'}</td>
                      <td className="f16-reason-cell">{(candidate.flag_labels || []).join('；')}</td>
                      <td><Locate irRunId={irRunId} source={candidate}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/* ==========================================================================
   Board
   ========================================================================== */

export function Foundation16Board({sessionId, assetId}: {sessionId: string; assetId?: string}) {
  const resource = useResource('document', `/api/report-sessions/${encodeURIComponent(sessionId)}/foundation16`);
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const data = resource.data;
  const conclusions: Conclusion[] = data?.status === 'available' ? (data.result?.conclusions || []) : [];

  const counts = useMemo(() => conclusions.reduce<Record<string, number>>((acc, c) => {
    const bucket = bucketOf(c.status);
    acc[bucket] = (acc[bucket] || 0) + 1;
    return acc;
  }, {all: conclusions.length}), [conclusions]);

  const groups = useMemo(() => {
    const keys = [...new Set(conclusions.map(c => String(c.package_id || '')))].filter(Boolean);
    return keys.map(key => ({key, title: packageTitle(conclusions, key), rows: conclusions.filter(c => String(c.package_id || '') === key)}));
  }, [conclusions]);

  const needle = query.trim().toLowerCase();
  const visible = (row: Conclusion) =>
    (filter === 'all' || bucketOf(row.status) === filter) &&
    (!needle || `${row.code} ${row.label} ${summaryOf(row)}`.toLowerCase().includes(needle));

  // Selecting the first clause keeps the inspector useful before any click.
  useEffect(() => {
    if (open || !conclusions.length) return;
    const first = conclusions.find(c => c.status === 'review' || c.status === 'conflict') || conclusions.find(c => c.status === 'found');
    if (first) setOpen(first.code);
  }, [conclusions.length, open]);

  if (!data) return <Section title="指标成果"><ResourceState error={resource.error} loading={resource.loading}/></Section>;
  if (data.status !== 'available') {
    return (
      <Section title="指标成果">
        <Notice>{data.status === 'running' ? `正在逐表理解指标：已规划 ${data.plan?.tasks?.length ?? '…'} 张候选表格。` : '尚未开始执行。'}</Notice>
      </Section>
    );
  }

  const result = data.result;
  const period = result.reporting_period || {};
  const coverage = result.document_coverage;
  const selected = conclusions.find(c => c.code === open);

  return (
    <Section
      title="指标成果"
      action={
        <div className="f16-actions">
          <button type="button" className="secondary" onClick={() => download(`${result.document_label || 'foundation16'}-指标成果.csv`, foundationCsv(conclusions))}>导出 CSV</button>
        </div>
      }
    >
      {result.stale_engine && (
        <Notice>
          这份成果由较早的提取规则生成（{result.engine_version}，当前 {result.current_engine_version}），结论可能在成员或口径上与本版本不同。
          {assetId && <button type="button" className="text-button" onClick={() => navigate(`/extractions/new?asset=${encodeURIComponent(assetId)}`)}>按当前规则重新提取</button>}
        </Notice>
      )}

      <p className="f16-meta">
        报告期 {period.year ?? '待确认'}（依据：{period.evidence || period.basis}）
        {coverage && <> · {coverageText(coverage)}</>}
        {' '}· 模型调用 {result.model_calls} 次{result.reused_answers ? `（复用 ${result.reused_answers} 次）` : ''} · 用时 {Math.round(result.elapsed_seconds)} 秒
      </p>

      <div className="f16-summary-bar">
        <div className="f16-filters" role="group" aria-label="按结论筛选">
          {FILTERS.map(([key, title]) => (
            <button key={key} type="button" className={`f16-filter ${filter === key ? 'selected' : ''}`} aria-pressed={filter === key} onClick={() => setFilter(key)}>
              {title}<small>{counts[key] || 0}</small>
            </button>
          ))}
        </div>
        <input className="f16-search" aria-label="搜索指标" placeholder="搜索原编号或指标" value={query} onChange={e => setQuery(e.target.value)}/>
      </div>

      <div className="f16-workbench">
        <div className="f16-rail">
          {groups.map(({key, title, rows}) => {
            const shown = rows.filter(visible);
            if (!shown.length) return null;
            return (
              <section className="f16-group-block" key={key} aria-label={title}>
                <h3 className="f16-group-title">{title}<small>{shown.length} 项</small></h3>
                <div className="table-scroll">
                  <table className="f16-table f16-rail-table">
                    <thead><tr><th>条款</th><th>状态</th><th>主值</th><th>子项</th><th></th></tr></thead>
                    <tbody>
                      {shown.map(c => (
                        <tr
                          key={c.code}
                          className={`${STATUS_TONE[c.status] || 'none'} ${open === c.code ? 'selected' : ''}`}
                          aria-selected={open === c.code}
                          onClick={() => setOpen(c.code)}
                        >
                          <td><strong className="mono">{c.code}</strong><small className="f16-attr">{c.label}</small></td>
                          <td><span className={`f16-status ${STATUS_TONE[c.status] || 'none'}`}>{statusText(c)}</span></td>
                          <td className="metric-value">{summaryOf(c) || '—'}</td>
                          <td>{(c.children || []).length || '—'}</td>
                          <td><button type="button" className="text-button" onClick={event => {event.stopPropagation(); setOpen(c.code);}}>展开</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            );
          })}
          {!groups.some(g => g.rows.some(visible)) && <Notice>当前筛选下没有指标。</Notice>}
        </div>
        <div className="f16-inspector">
          {selected ? <ClauseDetail conclusion={selected} irRunId={result.ir_run_id}/> : <Notice>从左侧选择一条指标，查看原值、来源与证据位置。</Notice>}
        </div>
      </div>
    </Section>
  );
}
