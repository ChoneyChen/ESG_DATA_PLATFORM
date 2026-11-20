import { useState } from "react";
import type {
  SourceFact,
  SourceFact_ReportedField,
  ResultOverview,
  OrganizedDataset_OrganizedTable,
  OrganizedDataset_OrganizedRow,
} from "../../shared/contracts";
import { pdfPageImageUrl, pdfUrl } from "../../shared/api";
import { Badge, Empty, Modal } from "../../shared/ui";
import { label, text, tone, periodText } from "../../shared/format";
import { href } from "../../app/routes";

type ResultTable = OrganizedDataset_OrganizedTable;
type ResultRow = OrganizedDataset_OrganizedRow;
type OrganizationFields = ResultTable["definition"]["fields"];

export const identityNames: Record<string, string> = {
  reporting_period: "实际测量期间",
  reporting_entity: "实际统计主体",
  entity_aggregation_role: "主体汇总方式",
  reporting_boundary: "实际范围",
  measurement_method: "测量方法",
  value_role: "数值角色",
  aggregation_role: "汇总角色",
  denominator: "分母",
};

function factFields(fact: SourceFact) {
  const { dimensions, ...identity } = fact.identity || {};
  return [
    ...Object.entries(identity).map(([key, field]) => ({ source: "identity", key, field })),
    ...Object.entries(dimensions || {}).map(([key, field]) => ({ source: "dimension", key, field })),
  ].filter(({ field }) => !!field) as { source: string; key: string; field: SourceFact_ReportedField }[];
}

export function FactValue({ fact }: { fact: SourceFact }) {
  const value = fact.value;
  return (
    <dl className="fact-value">
      <div>
        <dt>{value.kind === "quantitative" ? "原值" : "原文陈述"}</dt>
        <dd className="source-value">{"value_raw" in value ? value.value_raw : value.statement_raw}</dd>
      </div>
      {value.kind === "quantitative" && (
        <div><dt>原单位</dt><dd>{value.unit_raw || "未取得单位"}</dd></div>
      )}
      {value.kind === "assertion" && value.structure && [
        ["主题", value.structure.topic],
        ["对象", value.structure.subject],
        ["动作", value.structure.action],
      ].map(([title, content]) => content && (
        <div key={title}><dt>{title}</dt><dd>{content}</dd></div>
      ))}
    </dl>
  );
}

export function FactAttributes({ fact, fields, row }: {
  fact: SourceFact;
  fields?: OrganizationFields;
  row?: ResultRow;
}) {
  const values = factFields(fact).filter(({ field }) =>
    field.raw_text != null || field.value != null || field.state === "conflict");
  if (!values.length) return null;
  return (
    <dl className="fact-attributes">
      {values.map(({ source, key, field }) => {
        const definition = fields?.find(({ binding }) => binding.source === source && binding.key === key && !binding.attribute);
        const classification = definition && row?.cells[definition.key]?.classification;
        const display = field.value == null ? undefined
          : key === "reporting_period" ? periodText(field.value) : text(field.value);
        const recognized = classification?.state === "recognized" && classification.code;
        return (
          <div key={source + ":" + key}>
            <dt>{identityNames[key] || definition?.label || key}</dt>
            <dd>
              <span>{field.raw_text || display || label(field.state)}</span>
              {recognized ? (
                <small>分类 {classification.label || classification.code} · <code>{classification.code}</code></small>
              ) : display && field.raw_text && display !== field.raw_text && (
                <small>记录值 {display}</small>
              )}
              {field.state === "inherited" && <small>{label(field.state)}</small>}
              {field.state === "conflict" && <Badge tone="error">{label(field.state)}</Badge>}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function OriginalPage({ assetId, pageIndex }: { assetId: string; pageIndex: number }) {
  const [failed, setFailed] = useState(false);
  const [zoom, setZoom] = useState(false);
  return (
    <>
      {!failed && <button className="text-button" aria-pressed={zoom} onClick={() => setZoom(!zoom)}>{zoom ? "适合宽度" : "放大页面"}</button>}
      {failed
        ? <p role="status">原页预览暂不可用，可直接打开 PDF。</p>
        : <div className={zoom ? "original-page original-page-zoom" : "original-page"}>
          <img src={pdfPageImageUrl(assetId, pageIndex)} alt={`报告原页，第 ${pageIndex + 1} 页`} onError={() => setFailed(true)}/>
        </div>}
    </>
  );
}

export function FactEvidence({
  fact,
  fields,
  row,
  onClose,
}: {
  fact: SourceFact;
  fields?: OrganizationFields;
  row?: ResultRow;
  onClose: () => void;
}) {
  const resource = fact.report_resource;
  const sources = [
    ...fact.evidence,
    ...factFields(fact).flatMap(({ field }) => field.evidence || []),
  ];
  const unique = [
    ...new Map(sources.map((e) => [JSON.stringify(e), e])).values(),
  ];
  const pages = [...new Set(unique.flatMap(({ page_index }) => page_index == null ? [] : [page_index]))];
  const [selectedPage, selectPage] = useState<number>();
  const page = selectedPage ?? pages[0];
  return (
    <Modal title="核对报告原页" onClose={onClose} wide>
      <div className="fact-review">
        <section aria-label="提取内容">
          <h3>{fact.source_row_label || "原始事实"}</h3>
          <FactValue fact={fact}/>
          <FactAttributes fact={fact} fields={fields} row={row}/>
        </section>
        <section aria-label="报告原页">
          <div className="original-page-tools">
            {pages.length > 1 ? (
              <select aria-label="报告原页" value={page} onChange={e => selectPage(Number(e.target.value))}>
                {pages.map(index => <option key={index} value={index}>PDF 第 {index + 1} 页</option>)}
              </select>
            ) : page != null && <strong>PDF 第 {page + 1} 页</strong>}
            {resource && (
              <a className="text-link" href={pdfUrl(resource.asset_id, page == null ? undefined : page + 1)} target="_blank" rel="noreferrer">
                打开 PDF ↗
              </a>
            )}
          </div>
          {resource && page != null
            ? <OriginalPage key={resource.asset_id + ":" + page} assetId={resource.asset_id} pageIndex={page}/>
            : <p className="muted">{page == null ? "此条结果没有页码记录。" : "原报告暂不可用。"}</p>}
        </section>
      </div>
      {unique.length > 0 && <details className="fact-locations">
        <summary>来源摘录 · {unique.length} 处</summary>
        <ol className="evidence-list">
        {unique.map((e, i) => (
          <li key={i}>
            <div>
              <Badge>
                {(
                  {
                    value: "原值",
                    statement: "陈述",
                    row: "行标题",
                    column_header: "列标题",
                    unit: "单位",
                    table: "上下文",
                    footnote: "脚注",
                    report: "报告级说明",
                  } as Record<string, string>
                )[e.role] || e.role}
              </Badge>{" "}
              {e.page_index == null
                ? "报告级来源"
                : `PDF 第 ${e.page_index + 1} 页`}
              {resource && e.page_index != null && (
                <a
                  className="text-link"
                  href={pdfUrl(resource.asset_id, e.page_index + 1)}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开 PDF ↗
                </a>
              )}
            </div>
            <blockquote>{e.excerpt}</blockquote>
            <small>
              {e.object_type} · {e.object_id}
              {e.row_id ? " · 行 " + e.row_id : ""}
              {e.column_id ? " · 列 " + e.column_id : ""}
            </small>
          </li>
        ))}
        </ol>
      </details>}
    </Modal>
  );
}
export function FactsTable({
  facts,
  sessionId,
}: {
  facts: SourceFact[];
  sessionId: string;
}) {
  return facts.length ? (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>报告行名</th>
            <th>原值 / 陈述</th>
            <th>单位</th>
            <th>期间</th>
            <th>证据</th>
          </tr>
        </thead>
        <tbody>
          {facts.map((f) => (
            <tr key={f.source_fact_id}>
              <td>
                <a
                  href={href(
                    "extractions",
                    sessionId,
                    "facts",
                    f.source_fact_id,
                  )}
                  className="row-title"
                >
                  {f.source_row_label || "查看事实"}
                </a>
                <small>{label(f.value.kind)}</small>
              </td>
              <td className="raw-cell">
                {"value_raw" in f.value
                  ? f.value.value_raw
                  : f.value.statement_raw}
              </td>
              <td>
                {f.value.kind === "quantitative"
                  ? f.value.unit_raw || "未取得单位"
                  : "—"}
              </td>
              <td>{periodText(f.identity?.reporting_period?.value)}</td>
              <td>
                <Badge tone={tone(f.quality)}>{label(f.quality)}</Badge>
                <small>{f.evidence.length} 处引用</small>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="尚无原始事实" />
  );
}
function RowTable({ table, rows, columns, sessionId, onEvidence }: {
  table: ResultTable; rows: ResultRow[]; columns: string[]; sessionId: string;
  onEvidence: (row: ResultRow) => void;
}) {
  const fields = columns.map(key => table.definition.fields.find(f => f.key === key)).filter(f => !!f);
  return <div className="table-scroll result-scroll"><table className="data-table result-table"><thead><tr>
    {fields.map(f => <th key={f.key} className={f.role === "raw_value" ? "numeric-cell" : ""}>{f.label}</th>)}<th>来源</th>
  </tr></thead><tbody>{rows.map(row => <tr key={row.row_id}>
    {fields.map(field => {
      const cell = row.cells[field.key];
      const value = cell?.state === "available" ? (field.binding.key === "reporting_period" ? periodText(cell.value) : text(cell.classification?.label || cell.value)) : label(cell?.state || "not_read");
      return <td key={field.key} className={field.role === "raw_value" && table.data_kind === "quantitative" ? "numeric-cell" : "result-label"} title={cell?.raw_text || value}>
        <span>{field.role === "match_state" ? label(value) : value}</span>
      </td>;
    })}
    <td className="result-source"><button className="text-button" onClick={() => onEvidence(row)}>核对原页</button>
      <a className="text-link" href={factHref(sessionId, table.clause_key, row.source_fact_id)}>事实详情</a>
    </td>
  </tr>)}</tbody></table></div>;
}
const factHref = (sessionId: string, clauseKey: string, factId: string) =>
  href("extractions", sessionId, "facts", factId) + "?clause=" + encodeURIComponent(clauseKey);

export type TableView = { mode: "rows" | "matrix"; columns?: string[] };
export function OrganizedTable({ table, sessionId, facts, view, onViewChange }: {
  table: ResultTable; sessionId: string; facts: SourceFact[];
  view: TableView; onViewChange: (view: TableView) => void;
}) {
  const rows = table.rows || [];
  const [evidence, setEvidence] = useState<{ fact: SourceFact; row: ResultRow }>();
  const fields = table.definition.fields;
  const primary = fields.filter(f => ["source_label", "raw_value", "unit"].includes(f.role) || f.binding.key === "reporting_period");
  const defaults = [...primary, ...fields.filter(f => f.role === "reported_dimension")].slice(0, 8).map(f => f.key);
  const columns = view.columns ?? defaults;
  const rawField = table.definition.fields.find(f => f.role === "raw_value")?.key;
  const unitField = table.definition.fields.find(f => f.role === "unit")?.key;
  const periodField = table.definition.fields.find(f => f.binding.source === "identity" && f.binding.key === "reporting_period")?.key;
  const display = (key: string, value: unknown) => key === periodField ? periodText(value) : text(value);
  const matrix = table.matrix;
  function showEvidence(row: ResultRow) {
    const fact = facts.find(f => f.source_fact_id === row.source_fact_id);
    if (fact) setEvidence({ fact, row });
    else location.hash = factHref(sessionId, table.clause_key, row.source_fact_id);
  }
  return <>
    <div className="result-toolbar">
      <div className="segmented" aria-label="展示方式"><button aria-pressed={view.mode === "rows" || !matrix} onClick={() => onViewChange({ ...view, mode: "rows" })}>明细</button>{matrix && <button aria-pressed={view.mode === "matrix"} onClick={() => onViewChange({ ...view, mode: "matrix" })}>本页矩阵</button>}</div>
      <details className="column-picker"><summary>显示列</summary><div>{fields.map(field => <label key={field.key}><input type="checkbox" checked={columns.includes(field.key)} onChange={e => onViewChange({ ...view, columns: e.target.checked ? [...columns,field.key] : columns.filter(key => key !== field.key) })}/>{field.label}</label>)}</div></details>
    </div>
    {!rows.length ? <Empty title="此分类暂无数据"/> : view.mode === "matrix" && matrix ? <div className="table-scroll result-scroll"><table className="data-table result-table"><thead><tr><th>项目</th>
      {matrix.columns.map(c => <th key={c.coordinate_id}>{Object.entries(c.keys).map(([key, v]) => display(key, v.value)).join(" · ")}</th>)}
      </tr></thead><tbody>{matrix.rows.map(r => <tr key={r.coordinate_id}><th>{Object.entries(r.keys).map(([key, v]) => display(key, v.value)).join(" · ")}</th>{matrix.columns.map(c => <td key={c.coordinate_id} className="numeric-cell">{matrix.cells.find(cell => cell.row_coordinate_id === r.coordinate_id && cell.column_coordinate_id === c.coordinate_id)?.row_ids.map(id => {
        const row = rows.find(row => row.row_id === id)!;
        return <div key={id}><button className="text-button" onClick={() => showEvidence(row)}>{rawField ? text(row.cells[rawField]?.value) : "查看原值"}{unitField && row.cells[unitField]?.value != null && rawField && !text(row.cells[rawField]?.value).toLowerCase().includes(text(row.cells[unitField]?.value).toLowerCase()) ? " " + text(row.cells[unitField]?.value) : ""}</button></div>;
      }) || "—"}</td>)}</tr>)}</tbody></table></div> : <RowTable table={table} rows={rows} columns={columns} sessionId={sessionId} onEvidence={showEvidence}/>}
    {evidence && <FactEvidence key={evidence.fact.source_fact_id} fact={evidence.fact} row={evidence.row} fields={fields} onClose={() => setEvidence(undefined)}/>}
  </>;
}
export function OrganizedList({ overview, sessionId }: {overview: ResultOverview; sessionId: string}) {
  const [query, setQuery] = useState("");
  const [target, setTarget] = useState("");
  const term = query.trim().toLowerCase();
  const clauses = overview.clauses.filter(clause =>
    (!term || [clause.title, clause.clause_key].some(value => value.toLowerCase().includes(term))) &&
    (!target || (Boolean(clause.relations.match) === (target === "found"))));
  if (!overview.clauses.length) return <Empty title="尚无条款结果"/>;
  return <>
    <div className="result-filters">
      <input type="search" aria-label="搜索条款" placeholder="按名称或编号查找条款" value={query} onChange={e => setQuery(e.target.value)}/>
      <select aria-label="目标数据筛选" value={target} onChange={e => setTarget(e.target.value)}>
        <option value="">全部条款</option>
        <option value="found">有目标数据</option>
        <option value="missing">尚无目标数据</option>
      </select>
      <small>显示 {clauses.length} / {overview.clauses.length} 条款</small>
    </div>
    {clauses.length ? <div className="object-list">
      {clauses.map(clause => <a className="object-row" key={clause.clause_key} href={href("extractions", sessionId, "tables", clause.clause_key)}>
        <div><strong>{clause.title}</strong><small>{clause.clause_key} · {clause.relations.match || 0} 条目标数据 · {clause.facts} 条原值</small></div><span aria-hidden="true">→</span>
      </a>)}
    </div> : <Empty title="没有符合筛选的条款"/>}
  </>;
}
