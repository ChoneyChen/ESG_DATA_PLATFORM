import {useMemo, useState} from 'react';
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
  only_rejected_or_other_period: '仅其他期间',
};

const GROUP_ORDER = ['energy', 'ghg', 'air', 'water_pollutants', 'pollution_method', 'substances', 'water'];

const SOURCE_KINDS: [string, string][] = [
  ['agreeing_sources', '一致来源'],
  ['conflicting_sources', '冲突来源'],
  ['alternative_unit_sources', '其他单位'],
  ['related_subset_sources', '相关子口径'],
  ['component_sources', '分项构成'],
];

type Conclusion = Row & {code: string; status: string; group: string};

/** Re-exported so the board, the extraction preview and reports share one reading vocabulary. */
export {readingText} from '../pipeline';

function locate(irRunId: string, source: Row) {
  const object = source.cell_id || source.note_block_id || source.table_id || '';
  navigate(`/processing?ir=${encodeURIComponent(irRunId)}&page=${Number(source.page_number) - 1}&object=${encodeURIComponent(object)}`);
}

function valueText(row: Row) {
  return `${row.value_raw ?? '—'}${row.unit_raw ? ` ${row.unit_raw}` : ''}`;
}

function SourceLine({source, irRunId, caption, reasons = true}: {source: Row; irRunId: string; caption: string; reasons?: boolean}) {
  return (
    <li>
      <span className="f16-caption">{caption}</span>
      <span className="f16-value">{valueText(source)}</span>
      <span className="f16-source"> · 第 {source.page_number} 页 · {source.row_label}</span>
      {source.period && <span className="f16-source"> · {source.period}</span>}
      <button type="button" className="text-button" onClick={() => locate(irRunId, source)}>定位原文</button>
      {reasons && source.flag_labels?.length > 0 && <span className="f16-reasons">{source.flag_labels.join('；')}</span>}
    </li>
  );
}

function MemberDetail({member, irRunId}: {member: Row; irRunId: string}) {
  return (
    <div className="f16-detail">
      <ul className="f16-sources">
        <SourceLine source={member.source} irRunId={irRunId} caption="主证据" reasons={false} />
        {SOURCE_KINDS.flatMap(([key, caption]) =>
          (member[key] || []).map((source: Row, i: number) => <SourceLine key={`${key}${i}`} source={source} irRunId={irRunId} caption={caption}/>))}
      </ul>
      {member.flag_labels?.length > 0 && <div className="f16-flags">{member.flag_labels.map((f: string) => <span key={f}>{f}</span>)}</div>}
    </div>
  );
}

/**
 * Columns the operator can choose. The default set is what an analyst reads
 * first; the rest exist because a clause's evidence carries far more than a
 * value, and hiding it behind "详情" wastes the source work behind it.
 */
type ColumnKey = 'code' | 'label' | 'status' | 'value' | 'unit' | 'period' | 'page' | 'row' | 'flags' | 'detail';
const COLUMNS: {key: ColumnKey; title: string; always?: boolean}[] = [
  {key: 'code', title: '原编号', always: true},
  {key: 'label', title: '指标'},
  {key: 'status', title: '状态'},
  {key: 'value', title: '原值'},
  {key: 'unit', title: '单位'},
  {key: 'period', title: '期间'},
  {key: 'page', title: '来源'},
  {key: 'row', title: '来源行'},
  {key: 'flags', title: '口径标记'},
  {key: 'detail', title: '', always: true},
];

function cellOf(conclusion: Conclusion, column: ColumnKey): {text: string; title?: string} {
  const members: Row[] = conclusion.members || [];
  const primary = members[0];
  switch (column) {
    case 'code': return {text: conclusion.code};
    case 'label': return {text: conclusion.label};
    case 'status': return {text: STATUS_WORD[conclusion.status] || conclusion.status_label || conclusion.status};
    case 'value': return {text: members.length > 1 ? `${members.length} 项` : primary ? (primary.value_raw ?? primary.source?.value_raw ?? '—') : '—'};
    case 'unit': return {text: members.length > 1 ? '' : (primary?.unit_raw ?? '—')};
    case 'period': return {text: primary?.period ?? conclusion.statements?.[0]?.period ?? '—'};
    case 'page': return {text: primary?.source?.page_number ? `第 ${primary.source.page_number} 页` : conclusion.statements?.[0]?.page_number ? `第 ${conclusion.statements[0].page_number} 页` : '—'};
    case 'row': return {text: primary?.source?.row_label || '—', title: primary?.source?.row_label};
    case 'flags': return {text: (primary?.flag_labels || []).join('；') || '—'};
    default: return {text: ''};
  }
}

function ClauseDetail({conclusion, irRunId}: {conclusion: Conclusion; irRunId: string}) {
  const members: Row[] = conclusion.members || [];
  return (
    <div className="f16-drawer">
      <div className="f16-drawer-head">
        <div>
          <strong>{conclusion.code} · {conclusion.label}</strong>
          {conclusion.package_id && <small className="mono">{conclusion.package_id}{conclusion.metric_id ? ` · ${conclusion.metric_id}` : ''}</small>}
        </div>
        <span className={`f16-status ${STATUS_TONE[conclusion.status] || 'none'}`}>{STATUS_WORD[conclusion.status] || conclusion.status_label}</span>
      </div>
      {conclusion.definition_zh && <p className="f16-definition">{conclusion.definition_zh || conclusion.definition}</p>}
      {members.length > 0 && (
        <table className="f16-members">
          <thead><tr><th>成员</th><th>原值</th><th>单位</th><th>期间</th><th>来源行</th><th>口径标记</th></tr></thead>
          <tbody>
            {members.map((member, i) => (
              <tr key={i}>
                <td>{member.member || '合计'}</td>
                <td className="metric-value">{member.value_raw ?? '—'}</td>
                <td>{member.unit_raw ?? '—'}</td>
                <td>{member.period ?? '—'}</td>
                <td>
                  {member.source?.row_label || '—'}
                  {member.source?.attributes?.column_header && <small className="f16-attr">列：{member.source.attributes.column_header}</small>}
                  {member.source?.attributes?.table_title && <small className="f16-attr">表：{member.source.attributes.table_title}</small>}
                  {member.source?.attributes?.section && <small className="f16-attr">分区：{member.source.attributes.section}</small>}
                </td>
                <td className="f16-flagcell">{(member.flag_labels || []).map((f: string) => <span key={f}>{f}</span>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {members.map((member, i) => (
        <div key={`src${i}`} className="f16-member">
          {members.length > 1 && <h4>{member.member}</h4>}
          <MemberDetail member={member} irRunId={irRunId}/>
        </div>
      ))}
      {conclusion.statements?.map((s: Row) => (
        <blockquote key={s.block_id}>{s.text}<footer>第 {s.page_number} 页 · <button type="button" className="text-button" onClick={() => locate(irRunId, {page_number: s.page_number, note_block_id: s.block_id})}>定位原文</button></footer></blockquote>
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

function summaryOf(conclusion: Conclusion) {
  const members: Row[] = conclusion.members || [];
  if (conclusion.statements?.length) return conclusion.statements[0].text;
  if (members.length > 1) return members.map(m => `${m.member ?? ''} ${valueText(m)}`).join(' · ');
  return members[0] ? valueText(members[0]) : '';
}

function packageTitle(conclusions: Conclusion[], key: string) {
  const row = conclusions.find(c => String(c.package_id || '') === key);
  return row?.group_label ? `${row.group_label} · ${key}` : key;
}

function groupStatus(rows: Conclusion[]) {
  const found = rows.filter(r => r.status === 'found').length;
  const review = rows.filter(r => ['review', 'conflict'].includes(r.status)).length;
  return `${found} 已找到${review ? ` · ${review} 待复核` : ''}`;
}

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

export function Foundation16Board({sessionId}: {sessionId: string}) {
  const resource = useResource('document', `/api/report-sessions/${encodeURIComponent(sessionId)}/foundation16`);
  const [visible, setVisible] = useState<ColumnKey[]>(['code', 'label', 'status', 'value', 'unit', 'period', 'page', 'flags', 'detail']);
  const [open, setOpen] = useState<string | null>(null);
  const [group, setGroup] = useState<string>('all');
  const [grouping, setGrouping] = useState<'business' | 'package'>('package');
  const data = resource.data;
  const conclusions: Conclusion[] = data?.status === 'available' ? (data.result?.conclusions || []) : [];
  // A clause package is the unit the backend extracts and reviews, so it is
  // the default grouping; the business group stays available for a quick scan.
  // Declared before any early return so the hook order never changes.
  const groups = useMemo(() => grouping === 'package'
    ? [...new Set(conclusions.map(c => String(c.package_id || '')))]
        .filter(Boolean)
        .map(key => ({key, title: packageTitle(conclusions, key), rows: conclusions.filter(c => String(c.package_id || '') === key)}))
    : GROUP_ORDER
        .map(key => ({key, title: conclusions.find(c => c.group === key)?.group_label || key, rows: conclusions.filter(c => c.group === key)}))
        .filter(g => g.rows.length), [conclusions, grouping]);
  if (!data) return <Section title="16 项基础指标"><ResourceState error={resource.error} loading={resource.loading}/></Section>;
  if (data.status !== 'available') {
    return (
      <Section title="16 项基础指标">
        <Notice>{data.status === 'running' ? `正在逐表理解基础指标：已规划 ${data.plan?.tasks?.length ?? '…'} 张候选表格。` : '尚未开始执行。'}</Notice>
      </Section>
    );
  }
  const result = data.result;
  const counts = conclusions.reduce<Record<string, number>>((acc, c) => {
    const tone = STATUS_TONE[c.status] || 'none';
    acc[tone] = (acc[tone] || 0) + 1;
    return acc;
  }, {});
  const shown = group === 'all' ? groups : groups.filter(g => g.key === group);
  const period = result.reporting_period || {};
  const coverage = result.document_coverage;
  const columnList = COLUMNS.filter(c => visible.includes(c.key));
  return (
    <Section title="16 项基础指标" action={
      <div className="f16-actions">
        <div className="f16-counts">
          <span className="f16-status ok">已找到 {counts.ok || 0}</span>
          <span className="f16-status review">待复核 {counts.review || 0}</span>
          <span className="f16-status conflict">冲突 {counts.conflict || 0}</span>
          <span className="f16-status none">未披露 {counts.none || 0}</span>
        </div>
        <button type="button" className="secondary" onClick={() => download(`${result.document_label || 'foundation16'}-16项基础指标.csv`, foundationCsv(conclusions))}>导出 CSV</button>
      </div>
    }>
      <div className="f16-header">
        <div>
          <p className="muted">
            报告期 {period.year ?? '待确认'}（依据：{period.evidence || period.basis}）
            {coverage && <> · {coverageText(coverage)}</>}
            {' '}· 模型调用 {result.model_calls} 次{result.reused_answers ? `（复用 ${result.reused_answers} 次）` : ''} · 用时 {Math.round(result.elapsed_seconds)} 秒
          </p>
        </div>
      </div>
      <div className="f16-toolbar">
        <div className="f16-grouping" role="group" aria-label="分组方式">
          <button type="button" className={grouping === 'package' ? 'selected' : ''} aria-pressed={grouping === 'package'} onClick={() => { setGrouping('package'); setGroup('all'); }}>按条款包</button>
          <button type="button" className={grouping === 'business' ? 'selected' : ''} aria-pressed={grouping === 'business'} onClick={() => { setGrouping('business'); setGroup('all'); }}>按业务领域</button>
        </div>
        <div className="f16-groups" role="tablist" aria-label="条款分组">
          <button role="tab" aria-selected={group === 'all'} onClick={() => setGroup('all')}>全部 <small>{conclusions.length}</small></button>
          {groups.map(g => <button role="tab" aria-selected={group === g.key} key={g.key} onClick={() => setGroup(g.key)}>{g.title} <small>{g.rows.length}</small></button>)}
        </div>
        <details className="f16-columns"><summary>显示列</summary>
          {COLUMNS.filter(c => !c.always).map(c => (
            <label className="check" key={c.key}>
              <input type="checkbox" checked={visible.includes(c.key)} onChange={e => setVisible(e.target.checked ? [...visible, c.key] : visible.filter(k => k !== c.key))}/>
              {c.title}
            </label>
          ))}
        </details>
      </div>
      <Notice>每项均附原文页码和来源单元格；“待复核”“冲突”与“未披露”如实保留，不用相近概念填空。展开任一行可看到全部成员、口径标记与逐条证据。</Notice>
      <div className="f16-groups-body">
        {shown.map(({key, rows}) => (
          <section className="f16-group-block" key={key} aria-label={rows[0].group_label || key}>
            <h3 className="f16-group-title">{groups.find(g => g.key === key)?.title || key}<small>{rows.length} 项</small>
              <span className="f16-group-meta">{groupStatus(rows)}</span>
            </h3>
            <div className="table-scroll">
              <table className="f16-table">
                <thead><tr>{columnList.map(c => <th key={c.key}>{c.title}</th>)}</tr></thead>
                <tbody>
                  {rows.map(c => {
                    const cell = (column: ColumnKey) => cellOf(c, column);
                    return (
                      <tr key={c.code} className={`f16-row ${STATUS_TONE[c.status] || 'none'} ${open === c.code ? 'open' : ''}`} onClick={() => setOpen(open === c.code ? null : c.code)}>
                        {columnList.map(column => column.key === 'detail'
                          ? <td key={column.key}><button type="button" className="ghost" aria-expanded={open === c.code}>{open === c.code ? '收起' : '展开'}</button></td>
                          : <td key={column.key} className={`f16-${column.key}`} title={cell(column.key).title}>
                              {column.key === 'status'
                                ? <span className={`f16-status ${STATUS_TONE[c.status] || 'none'}`}>{cell(column.key).text}</span>
                                : column.key === 'value'
                                  ? <span className="metric-value">{cell(column.key).text}</span>
                                  : cell(column.key).text}
                            </td>)}
                      </tr>
                    );
                  })}
                  {rows.filter(c => open === c.code).map(c => (
                    <tr key={`${c.code}-detail`} className="f16-expanded"><td colSpan={columnList.length}><ClauseDetail conclusion={c} irRunId={result.ir_run_id}/></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))}
      </div>
    </Section>
  );
}
