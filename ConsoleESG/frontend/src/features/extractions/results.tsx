import { useState } from "react";
import type {
  SourceFact,
  FactCollection,
  OrganizedDataset,
  OrganizedDataset_OrganizedTable,
  OrganizedDataset_OrganizedRow,
} from "../../shared/contracts";
import { pdfUrl } from "../../shared/api";
import { Badge, Empty, Modal } from "../../shared/ui";
import { label, text, tone } from "../../shared/format";
import { href } from "../../app/routes";
export type FactBundle = FactCollection;
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
                    table: "表格上下文",
                    footnote: "脚注",
                    report: "报告身份",
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
const resultClass = (row: ResultRow) => row.result_class || ({ match: "target", different: "other" } as Record<string, string>)[row.match] || "unresolved";
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
      const value = cell?.state === "available" ? text(cell.classification?.label || cell.value) : label(cell?.state || "not_read");
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
  const [partition, setPartition] = useState("target");
  const [query, setQuery] = useState("");
  const [period, setPeriod] = useState("");
  const [unit, setUnit] = useState("");
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState("source");
  const [evidence, setEvidence] = useState<SourceFact>();
  const populated = table.definition.fields.filter(f => rows.some(r => r.cells[f.key]?.state === "available"));
  const defaults = populated.filter(f => ["raw_value", "unit", "reported_dimension"].includes(f.role) ||
    (f.binding.source === "fact" && f.binding.key === "source_row_label") || f.binding.key === "reporting_period").slice(0, 8).map(f => f.key);
  const [columns, setColumns] = useState(defaults);
  const counts = Object.fromEntries(["target", "other", "unresolved"].map(key => [key, rows.filter(row => resultClass(row) === key).length]));
  const periodField = table.definition.fields.find(f => f.binding.source === "identity" && f.binding.key === "reporting_period")?.key;
  const unitField = table.definition.fields.find(f => f.role === "unit")?.key;
  const values = (key?: string) => key ? [...new Set(rows.map(row => row.cells[key]?.value).filter(v => v != null).map(text))] : [];
  const filtered = rows.filter(row => (partition === "all" || resultClass(row) === partition) &&
    (!query || Object.values(row.cells).some(c => (text(c.value) + " " + (c.raw_text || "")).toLocaleLowerCase().includes(query.toLocaleLowerCase()))) &&
    (!period || (periodField && text(row.cells[periodField]?.value) === period)) &&
    (!unit || (unitField && text(row.cells[unitField]?.value) === unit)));
  const labelField = table.definition.fields.find(f => f.role === "source_label")?.key;
  if (sort === "label") filtered.sort((a,b) => text(labelField ? a.cells[labelField]?.value : a.source_fact_id).localeCompare(text(labelField ? b.cells[labelField]?.value : b.source_fact_id), "zh-CN", {numeric:true}));
  const size = 50, pages = Math.max(1, Math.ceil(filtered.length / size)), current = Math.min(page, pages - 1);
  const visibleIds = new Set(filtered.slice(current * size, (current + 1) * size).map(r => r.row_id));
  const rawField = table.definition.fields.find(f => f.role === "raw_value")?.key;
  const matrix = table.matrix;
  function showEvidence(row: ResultRow) {
    const fact = facts.find(f => f.source_fact_id === row.source_fact_id);
    if (fact) setEvidence(fact);
    else location.hash = href("extractions", sessionId, "facts", row.source_fact_id) + "?clause=" + encodeURIComponent(table.clause_key);
  }
  function changeFilter(fn: () => void) { fn(); setPage(0); }
  return <>
    <div className="result-toolbar">
      <div className="segmented" aria-label="结果分类">{[["target", "目标数据"], ["other", "其他目标"], ["unresolved", "关系未决"], ["all", "全部"]].map(([key,name]) =>
        <button key={key} aria-pressed={partition === key} onClick={() => changeFilter(() => setPartition(key))}>{name} <span>{key === "all" ? rows.length : counts[key]}</span></button>)}</div>
      <div className="segmented" aria-label="展示方式"><button aria-pressed={view === "rows"} onClick={() => setView("rows")}>明细</button>{matrix && <button aria-pressed={view === "matrix"} onClick={() => setView("matrix")}>矩阵</button>}</div>
    </div>
    <div className="result-filters">
      <input aria-label="搜索结果" placeholder="搜索项目或原文" value={query} onChange={e => changeFilter(() => setQuery(e.target.value))}/>
      {periodField && <select aria-label="期间筛选" value={period} onChange={e => changeFilter(() => setPeriod(e.target.value))}><option value="">全部期间</option>{values(periodField).map(v => <option key={v}>{v}</option>)}</select>}
      {unitField && <select aria-label="单位筛选" value={unit} onChange={e => changeFilter(() => setUnit(e.target.value))}><option value="">全部单位</option>{values(unitField).map(v => <option key={v}>{v}</option>)}</select>}
      <select aria-label="排序" value={sort} onChange={e => setSort(e.target.value)}><option value="source">默认顺序</option><option value="label">项目名称</option></select>
      <details className="column-picker"><summary>显示列</summary><div>{table.definition.fields.map(field => <label key={field.key}><input type="checkbox" checked={columns.includes(field.key)} onChange={e => setColumns(e.target.checked ? [...columns,field.key] : columns.filter(key => key !== field.key))}/>{field.label}</label>)}</div></details>
    </div>
    {!filtered.length ? <Empty title="此分类暂无数据"/> : view === "matrix" && matrix ? <div className="table-scroll result-scroll"><table className="data-table result-table"><thead><tr><th>项目</th>
      {matrix.columns.filter(c => matrix.cells.some(cell => cell.column_coordinate_id === c.coordinate_id && cell.row_ids.some(id => visibleIds.has(id)))).map(c => <th key={c.coordinate_id}>{Object.values(c.keys).map(v => text(v.value)).join(" · ")}</th>)}
      </tr></thead><tbody>{matrix.rows.filter(r => matrix.cells.some(cell => cell.row_coordinate_id === r.coordinate_id && cell.row_ids.some(id => visibleIds.has(id)))).map(r => <tr key={r.coordinate_id}><th>{Object.values(r.keys).map(v => text(v.value)).join(" · ")}</th>{matrix.columns.filter(c => matrix.cells.some(cell => cell.column_coordinate_id === c.coordinate_id && cell.row_ids.some(id => visibleIds.has(id)))).map(c => <td key={c.coordinate_id} className="numeric-cell">{matrix.cells.find(cell => cell.row_coordinate_id === r.coordinate_id && cell.column_coordinate_id === c.coordinate_id)?.row_ids.filter(id => visibleIds.has(id)).map(id => {
        const row = rows.find(row => row.row_id === id)!;
        return <div key={id}><button className="text-button" onClick={() => showEvidence(row)}>{rawField ? text(row.cells[rawField]?.value) : "查看原值"}{unitField && row.cells[unitField]?.value != null && rawField && !text(row.cells[rawField]?.value).toLowerCase().includes(text(row.cells[unitField]?.value).toLowerCase()) ? " " + text(row.cells[unitField]?.value) : ""}</button></div>;
      }) || "—"}</td>)}</tr>)}</tbody></table></div> : <RowTable table={table} rows={filtered.slice(current * size, (current + 1) * size)} columns={columns} sessionId={sessionId} onEvidence={showEvidence}/>}
    <div className="result-footer"><span>{filtered.length} 条原始记录</span>{pages > 1 && <div><button className="button-secondary" disabled={!current} onClick={() => setPage(current - 1)}>上一页</button><span> {current + 1} / {pages} </span><button className="button-secondary" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>下一页</button></div>}</div>
    {evidence && <FactEvidence fact={evidence} onClose={() => setEvidence(undefined)}/>}
  </>;
}
export function OrganizedList({ dataset, sessionId }: {dataset: OrganizedDataset; sessionId: string}) {
  return dataset.tables?.length ? <div className="object-list">{dataset.tables.map(t => <a className="object-row" key={t.table_id} href={href("extractions",sessionId,"tables",t.clause_key)}>
    <div><strong>{t.definition.title}</strong><small>{t.clause_key} · {(t.rows || []).filter(row => resultClass(row) === "target").length} 条目标数据</small></div><Badge tone={tone(t.status)}>{label(t.status)}</Badge><span aria-hidden="true">→</span>
  </a>)}</div> : <Empty title="尚无条款结果"/>;
}
