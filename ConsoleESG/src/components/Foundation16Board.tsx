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

const STATUS_WORD: Record<string, string> = {
  found: '已找到',
  review: '待复核',
  conflict: '冲突',
  not_found_in_selected_sources: '未披露',
  only_rejected_or_other_period: '仅有被拒候选',
};

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

const SOURCE_KINDS: [string, string][] = [
  ['agreeing_sources', '一致来源'],
  ['conflicting_sources', '冲突来源'],
  ['alternative_unit_sources', '其他单位'],
  ['related_subset_sources', '相关子口径'],
  ['component_sources', '分项构成'],
];

export type Conclusion = Row & {code: string; status: string; group: string; parent_code?: string | null; children?: Row[]};

/** Re-exported so the board, the extraction preview and reports share one reading vocabulary. */
export {readingText} from '../pipeline';

function locate(irRunId: string, source: Row) {
  const object = source.cell_id || source.note_block_id || source.table_id || '';
  navigate(`/processing?ir=${encodeURIComponent(irRunId)}&page=${Number(source.page_number) - 1}&object=${encodeURIComponent(object)}`);
}

function valueText(row: Row) {
  return `${row.value_raw ?? '—'}${row.unit_raw ? ` ${row.unit_raw}` : ''}`;
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

/* ==========================================================================
   CSV
   ========================================================================== */

const CSV_COLUMNS = ['原编号', '指标', '结论', '成员', '报告原值', '单位', '期间', '页码', '来源行', '说明'];

function csvCell(value: unknown) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function foundationCsv(conclusions: Conclusion[]) {
  const rows: unknown[][] = [];
  for (const c of conclusions) {
    const members: Row[] = c.members || [];
    if (!members.length) {
      const statement = c.statements?.[0];
      rows.push([c.code, c.label, c.status_label, '', statement ? statement.text : '', '', '', statement?.page_number ?? '', '', '']);
      continue;
    }
    for (const m of members) {
      rows.push([c.code, c.label, c.status_label, m.member ?? '', m.value_raw, m.unit_raw ?? '', m.period ?? '',
        m.source?.page_number ?? '', m.source?.row_label ?? '', (m.flag_labels || []).join('；')]);
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
   Pieces
   ========================================================================== */

function SourceLine({source, irRunId, caption, reasons = true}: {source: Row; irRunId: string; caption: string; reasons?: boolean}) {
  return (
    <li className="f16-source-line">
      <span className="f16-caption">{caption}</span>
      <span className="f16-value">{valueText(source)}</span>
      <span className="f16-source">第 {source.page_number} 页 · {source.row_label}</span>
      {source.period && <span className="f16-source">{source.period}</span>}
      <button type="button" className="text-button" onClick={() => locate(irRunId, source)}>定位原文</button>
      {reasons && source.flag_labels?.length > 0 && <span className="f16-reasons">{source.flag_labels.join('；')}</span>}
    </li>
  );
}

function ClauseDetail({conclusion, irRunId}: {conclusion: Conclusion; irRunId: string}) {
  const members: Row[] = conclusion.members || [];
  return (
    <div className="f16-detail-pane">
      <header className="f16-detail-head">
        <div>
          <span className="f16-code-badge">{conclusion.code}</span>
          <h2>{conclusion.label}</h2>
          {conclusion.package_id && <small className="mono">{conclusion.package_id}{conclusion.metric_id ? ` · ${conclusion.metric_id}` : ''}</small>}
        </div>
        <span className={`f16-status ${STATUS_TONE[conclusion.status] || 'none'}`}>{STATUS_WORD[conclusion.status] || conclusion.status_label}</span>
      </header>

      {conclusion.parent_code && <p className="f16-parent">子项 · 属于 {conclusion.parent_code}</p>}

      {members.length > 0 && (
        <div className="f16-values">
          {members.map((member, i) => (
            <div className="f16-value-card" key={i}>
              {members.length > 1 && <small>{member.member || '合计'}</small>}
              <strong>{member.value_raw ?? '—'}</strong>
              <span>{member.unit_raw ?? ''}</span>
              <em>{member.period ?? ''}</em>
            </div>
          ))}
        </div>
      )}

      {conclusion.definition_zh && <p className="f16-definition">{conclusion.definition_zh || conclusion.definition}</p>}

      {(conclusion.children || []).length > 0 && (
        <div className="f16-sublist">
          <h4>本条包含的子项（{conclusion.children!.length}）</h4>
          <table className="f16-members">
            <thead><tr><th>子项</th><th>状态</th><th>值</th></tr></thead>
            <tbody>
              {conclusion.children!.map((child: Row) => (
                <tr key={String(child.code)}>
                  <td><strong>{child.code}</strong><small className="f16-attr">{child.label}</small></td>
                  <td><span className={`f16-status ${STATUS_TONE[String(child.status)] || 'none'}`}>{STATUS_WORD[String(child.status)] || String(child.status)}</span></td>
                  <td className="metric-value">{(child.values || []).map((v: Row, i: number) => <div key={i}>{v.member ? `${v.member} ` : ''}{v.value_raw ?? '—'}{v.unit_raw ? ` ${v.unit_raw}` : ''}</div>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {members.length > 0 && (
        <>
          <h4>每个成员的来源</h4>
          {members.map((member, i) => (
            <div className="f16-member" key={`src${i}`}>
              {members.length > 1 && <h5>{member.member}</h5>}
              <div className="f16-row-meta">
                <span>{member.source?.row_label || '—'}</span>
                {member.source?.attributes?.column_header && <small>列：{member.source.attributes.column_header}</small>}
                {member.source?.attributes?.table_title && <small>表：{member.source.attributes.table_title}</small>}
                {member.source?.attributes?.section && <small>分区：{member.source.attributes.section}</small>}
              </div>
              <ul className="f16-sources">
                <SourceLine source={member.source} irRunId={irRunId} caption="主证据" reasons={false}/>
                {SOURCE_KINDS.flatMap(([key, caption]) =>
                  (member[key] || []).map((source: Row, j: number) => <SourceLine key={`${key}${j}`} source={source} irRunId={irRunId} caption={caption}/>))}
              </ul>
              {(member.flag_labels?.length > 0 || (member as Row).source?.flag_labels?.length > 0) && (
                <div className="f16-flags">
                  {(member.flag_labels || []).map((f: string) => <span key={f}>{f}</span>)}
                  {(member as Row).source?.flag_labels?.map((f: string) => <span key={f}>{f}</span>)}
                </div>
              )}
              {member.value_raw != null && <button className="text-button" onClick={() => locate(irRunId, member.source || {})}>在第 {member.source?.page_number} 页核对这一行</button>}
            </div>
          ))}
        </>
      )}

      {conclusion.statements?.map((s: Row) => (
        <blockquote key={s.block_id}>
          {s.text}
          <footer>第 {s.page_number} 页 · <button type="button" className="text-button" onClick={() => locate(irRunId, {page_number: s.page_number, note_block_id: s.block_id})}>定位原文</button></footer>
        </blockquote>
      ))}

      {!members.length && !conclusion.statements?.length && (
        <>
          <Notice>
            在所选表格与正文段落中没有符合本条款定义的披露。
            {conclusion.rejection_reasons?.length ? `已排除的近似披露原因：${conclusion.rejection_reasons.join('；')}。` : ''}
            {conclusion.candidates?.length ? '以下近似披露因概念或口径不符被排除：' : ''}
          </Notice>
          <ul className="f16-sources">
            {conclusion.candidates?.map((s: Row, i: number) => <SourceLine key={i} source={s} irRunId={irRunId} caption="已排除"/>)}
          </ul>
        </>
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
        <div className="f16-rail" role="list">
          {groups.map(({key, title, rows}) => {
            const shown = rows.filter(visible);
            if (!shown.length) return null;
            return (
              <section className="f16-group-block" key={key} aria-label={title}>
                <h3 className="f16-group-title">{title}<small>{shown.length} 项</small></h3>
                {shown.map(c => (
                  <button
                    key={c.code}
                    role="listitem"
                    type="button"
                    className={`f16-item ${STATUS_TONE[c.status] || 'none'} ${open === c.code ? 'selected' : ''}`}
                    onClick={() => setOpen(c.code)}
                    aria-current={open === c.code ? 'true' : undefined}
                  >
                    <span className="f16-item-dot" aria-hidden="true"/>
                    <span className="f16-item-body">
                      <span className="f16-item-label">{c.label}</span>
                      <span className="f16-item-meta">{c.code} · {STATUS_WORD[c.status] || c.status_label}</span>
                    </span>
                    <span className="f16-item-value">{summaryOf(c) || '—'}</span>
                  </button>
                ))}
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
