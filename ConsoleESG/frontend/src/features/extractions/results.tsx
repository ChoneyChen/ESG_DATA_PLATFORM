import { useState } from "react";
import type {
  SourceFact,
  ResultOverview,
  OrganizedDataset,
  OrganizedDataset_OrganizedTable,
  OrganizedDataset_OrganizedRow,
} from "../../shared/contracts";
import { pdfUrl } from "../../shared/api";
import { Badge, Empty, Modal } from "../../shared/ui";
import { label, text, tone, periodText } from "../../shared/format";
import { href } from "../../app/routes";
export function FactEvidence({
  fact,
  onClose,
}: {
  fact: SourceFact;
  onClose: () => void;
}) {
  const resource = fact.report_resource;
  const sources = [
    ...fact.evidence,
    ...Object.entries(fact.identity || {}).flatMap(([key, value]) =>
      key === "dimensions"
        ? Object.values(
            value as Record<string, { evidence?: SourceFact["evidence"] }>,
          ).flatMap((v) => v.evidence || [])
        : (value as { evidence?: SourceFact["evidence"] })?.evidence || [],
    ),
  ];
  const unique = [
    ...new Map(sources.map((e) => [JSON.stringify(e), e])).values(),
  ];
  return (
    <Modal title="原文证据" onClose={onClose} wide>
      <p className="lead">{fact.source_row_label || "原始事实"}</p>
      <div className="source-value">
        {"value_raw" in fact.value
          ? fact.value.value_raw
          : fact.value.statement_raw}
      </div>
      <Badge tone={tone(fact.quality)}>{label(fact.quality)}</Badge>
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
type ResultTable = OrganizedDataset_OrganizedTable;
type ResultRow = OrganizedDataset_OrganizedRow;
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
    <td className="result-source"><button className="text-button" onClick={() => onEvidence(row)}>查看原文</button>
      <a className="sr-only" href={href("extractions", sessionId, "facts", row.source_fact_id) + "?clause=" + encodeURIComponent(table.clause_key)}>事实详情</a>
    </td>
  </tr>)}</tbody></table></div>;
}
export function OrganizedTable({ table, sessionId, facts = [] }: {
  table: ResultTable; sessionId: string; facts?: SourceFact[];
}) {
  const rows = table.rows || [];
  const [view, setView] = useState("rows");
  const [evidence, setEvidence] = useState<SourceFact>();
  const defaults = table.definition.fields.filter(f => ["raw_value", "unit", "reported_dimension"].includes(f.role) ||
    (f.binding.source === "fact" && f.binding.key === "source_row_label") || f.binding.key === "reporting_period").slice(0, 8).map(f => f.key);
  const [columns, setColumns] = useState(defaults);
  const rawField = table.definition.fields.find(f => f.role === "raw_value")?.key;
  const unitField = table.definition.fields.find(f => f.role === "unit")?.key;
  const periodField = table.definition.fields.find(f => f.binding.source === "identity" && f.binding.key === "reporting_period")?.key;
  const display = (key: string, value: unknown) => key === periodField ? periodText(value) : text(value);
  const matrix = table.matrix;
  function showEvidence(row: ResultRow) {
    const fact = facts.find(f => f.source_fact_id === row.source_fact_id);
    if (fact) setEvidence(fact);
    else location.hash = href("extractions", sessionId, "facts", row.source_fact_id) + "?clause=" + encodeURIComponent(table.clause_key);
  }
  return <>
    <div className="result-toolbar">
      <div className="segmented" aria-label="展示方式"><button aria-pressed={view === "rows"} onClick={() => setView("rows")}>明细</button>{matrix && <button aria-pressed={view === "matrix"} onClick={() => setView("matrix")}>本页矩阵</button>}</div>
      <details className="column-picker"><summary>显示列</summary><div>{table.definition.fields.map(field => <label key={field.key}><input type="checkbox" checked={columns.includes(field.key)} onChange={e => setColumns(e.target.checked ? [...columns,field.key] : columns.filter(key => key !== field.key))}/>{field.label}</label>)}</div></details>
    </div>
    {!rows.length ? <Empty title="此分类暂无数据"/> : view === "matrix" && matrix ? <div className="table-scroll result-scroll"><table className="data-table result-table"><thead><tr><th>项目</th>
      {matrix.columns.map(c => <th key={c.coordinate_id}>{Object.entries(c.keys).map(([key, v]) => display(key, v.value)).join(" · ")}</th>)}
      </tr></thead><tbody>{matrix.rows.map(r => <tr key={r.coordinate_id}><th>{Object.entries(r.keys).map(([key, v]) => display(key, v.value)).join(" · ")}</th>{matrix.columns.map(c => <td key={c.coordinate_id} className="numeric-cell">{matrix.cells.find(cell => cell.row_coordinate_id === r.coordinate_id && cell.column_coordinate_id === c.coordinate_id)?.row_ids.map(id => {
        const row = rows.find(row => row.row_id === id)!;
        return <div key={id}><button className="text-button" onClick={() => showEvidence(row)}>{rawField ? text(row.cells[rawField]?.value) : "查看原值"}{unitField && row.cells[unitField]?.value != null && rawField && !text(row.cells[rawField]?.value).toLowerCase().includes(text(row.cells[unitField]?.value).toLowerCase()) ? " " + text(row.cells[unitField]?.value) : ""}</button></div>;
      }) || "—"}</td>)}</tr>)}</tbody></table></div> : <RowTable table={table} rows={rows} columns={columns} sessionId={sessionId} onEvidence={showEvidence}/>}
    {evidence && <FactEvidence fact={evidence} onClose={() => setEvidence(undefined)}/>}
  </>;
}
export function OrganizedList({ overview, sessionId }: {overview: ResultOverview; sessionId: string}) {
  return overview.clauses.length ? <div className="object-list">{overview.clauses.map(t => <a className="object-row" key={t.clause_key} href={href("extractions",sessionId,"tables",t.clause_key)}>
    <div><strong>{t.title}</strong><small>{t.clause_key} · {t.relations.match || 0} 条目标数据 · {t.facts} 条原值</small></div><span aria-hidden="true">→</span>
  </a>)}</div> : <Empty title="尚无条款结果"/>;
}
