import {useState} from 'react';
import {navigate, type Row} from '../api';
import {useResource} from '../state';
import {Notice, ResourceState, Section} from './ui';
import '../foundation.css';

const STATUS_TONE: Record<string, string> = {
  found: 'ok',
  review: 'review',
  conflict: 'conflict',
  not_found_in_selected_sources: 'none',
  only_rejected_or_other_period: 'none',
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

/** How the covered pages were read: PDF text layer, on-device OCR of title regions, visual OCR. */
export function readingText(reading: Row): string {
  const parts = [`文字层直读 ${reading.text_layer_pages} 页`];
  if (reading.on_device_ocr_pages) parts.push(`其中 ${reading.on_device_ocr_pages} 页标题经本机识别`);
  parts.push(`视觉识别 ${reading.visual_ocr_pages} 页`);
  return parts.join('，');
}

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
          (member[key] || []).map((source: Row, i: number) => <SourceLine key={`${key}${i}`} source={source} irRunId={irRunId} caption={caption} />))}
      </ul>
      {member.flag_labels?.length > 0 && <div className="f16-flags">{member.flag_labels.map((f: string) => <span key={f}>{f}</span>)}</div>}
    </div>
  );
}

function summaryOf(conclusion: Conclusion) {
  const members: Row[] = conclusion.members || [];
  if (conclusion.statements?.length) return conclusion.statements[0].text;
  if (members.length > 1) return members.map(m => `${m.member ?? ''} ${valueText(m)}`).join(' · ');
  return members[0] ? valueText(members[0]) : '';
}

function ConclusionRow({conclusion, irRunId}: {conclusion: Conclusion; irRunId: string}) {
  const [open, setOpen] = useState(false);
  const members: Row[] = conclusion.members || [];
  const tone = STATUS_TONE[conclusion.status] || 'none';
  const primary = members[0];
  const summary = summaryOf(conclusion);
  const page = primary?.source?.page_number ?? conclusion.statements?.[0]?.page_number;
  return (
    <>
      <tr className={`f16-row ${tone}`} onClick={() => setOpen(!open)}>
        <td className="f16-code">{conclusion.code}</td>
        <td>{conclusion.label}</td>
        <td><span className={`f16-status ${tone}`}>{conclusion.status_label}</span></td>
        <td className="f16-summary">{summary || <span className="muted">—</span>}</td>
        <td>{primary?.period || ''}</td>
        <td>{page ? `第 ${page} 页` : ''}</td>
        <td><button type="button" className="ghost" aria-expanded={open}>{open ? '收起' : '详情'}</button></td>
      </tr>
      {open && (
        <tr className="f16-expanded">
          <td colSpan={7}>
            <p className="f16-definition">{conclusion.definition_zh || conclusion.definition}</p>
            {members.map((member, i) => (
              <div key={i} className="f16-member">
                {members.length > 1 && <h4>{member.member}</h4>}
                <MemberDetail member={member} irRunId={irRunId} />
              </div>
            ))}
            {conclusion.statements?.map((s: Row) => (
              <blockquote key={s.block_id}>{s.text}<footer>第 {s.page_number} 页 · <button type="button" className="text-button" onClick={() => locate(irRunId, {page_number: s.page_number, note_block_id: s.block_id})}>定位原文</button></footer></blockquote>
            ))}
            {!members.length && !conclusion.statements?.length && (
              <>
                <Notice>在所选表格与正文段落中没有符合本条款定义的披露。{conclusion.candidates?.length ? '以下近似披露因概念或口径不符被排除：' : ''}</Notice>
                <ul className="f16-sources">
                  {conclusion.candidates?.map((s: Row, i: number) => <SourceLine key={i} source={s} irRunId={irRunId} caption="已排除" />)}
                </ul>
              </>
            )}
          </td>
        </tr>
      )}
    </>
  );
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
  const data = resource.data;
  if (!data) return <Section title="16 项基础指标"><ResourceState error={resource.error} loading={resource.loading} /></Section>;
  if (data.status !== 'available') {
    return (
      <Section title="16 项基础指标">
        <Notice>{data.status === 'running' ? `正在逐表理解基础指标：已规划 ${data.plan?.tasks?.length ?? '…'} 张候选表格。` : '尚未开始执行。'}</Notice>
      </Section>
    );
  }
  const result = data.result;
  const conclusions: Conclusion[] = result.conclusions;
  const counts = conclusions.reduce<Record<string, number>>((acc, c) => {
    const tone = STATUS_TONE[c.status] || 'none';
    acc[tone] = (acc[tone] || 0) + 1;
    return acc;
  }, {});
  const groups = GROUP_ORDER.map(group => ({group, rows: conclusions.filter(c => c.group === group)})).filter(g => g.rows.length);
  const period = result.reporting_period || {};
  const coverage = result.document_coverage;
  return (
    <Section title="16 项基础指标">
      <div className="f16-header">
        <div>
          <strong>{result.document_label}</strong>
          <p className="muted">
            报告期 {period.year ?? '待确认'}（依据：{period.evidence || period.basis}）
            {coverage && <> · {coverage.mode === 'targeted' ? `定向识别 ${coverage.covered_pages}/${coverage.source_pages} 页` : `全文 ${coverage.covered_pages} 页`}</>}
            {coverage?.reading && <>（{readingText(coverage.reading)}）</>}
            {' '}· 模型调用 {result.model_calls} 次{result.reused_answers ? `（复用已有回答 ${result.reused_answers} 次）` : ''} · 输入 {result.input_tokens?.toLocaleString()} / 输出 {result.output_tokens?.toLocaleString()} token · 用时 {Math.round(result.elapsed_seconds)} 秒
          </p>
        </div>
        <div className="f16-actions">
          <div className="f16-counts">
            <span className="f16-status ok">已找到 {counts.ok || 0}</span>
            <span className="f16-status review">待复核 {counts.review || 0}</span>
            <span className="f16-status conflict">冲突 {counts.conflict || 0}</span>
            <span className="f16-status none">未找到 {counts.none || 0}</span>
          </div>
          <button type="button" className="secondary" onClick={() => download(`${result.document_label || 'foundation16'}-16项基础指标.csv`, foundationCsv(conclusions))}>导出 CSV</button>
        </div>
      </div>
      <Notice>每项均附原文页码和来源单元格；“待复核”“冲突”与“未找到”如实保留，不用相近概念填空。正式发布前仍须在审核中确认事实、映射与口径。</Notice>
      <div className="table-scroll">
        <table className="f16-table">
          <thead><tr><th>原编号</th><th>指标</th><th>结论</th><th>报告原值</th><th>期间</th><th>来源</th><th /></tr></thead>
          {groups.map(({group, rows}) => (
            <tbody key={group}>
              <tr className="f16-group"><td colSpan={7}>{rows[0].group_label}</td></tr>
              {rows.map(c => <ConclusionRow key={c.code} conclusion={c} irRunId={result.ir_run_id} />)}
            </tbody>
          ))}
        </table>
      </div>
    </Section>
  );
}
