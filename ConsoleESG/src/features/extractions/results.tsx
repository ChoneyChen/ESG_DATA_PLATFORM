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
                  ? f.value.unit_raw || "未披露"
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
function RowTable({
  table,
  rows,
  sessionId,
}: {
  table: OrganizedDataset_OrganizedTable;
  rows: OrganizedDataset_OrganizedRow[];
  sessionId: string;
}) {
  const columns = table.definition.presentation?.columns?.length
    ? table.definition.presentation.columns
    : table.definition.fields.map((f) => f.key);
  const fields = columns
    .map((key) => table.definition.fields.find((f) => f.key === key))
    .filter((f) => !!f);
  return (
    <div className="table-scroll">
      <table className="data-table result-table">
        <thead>
          <tr>
            {fields.map((f) => (
              <th key={f.key}>{f.label}</th>
            ))}
            <th>匹配 / 来源</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.row_id}>
              {fields.map((field) => {
                const cell = row.cells[field.key];
                return (
                  <td
                    key={field.key}
                    className={field.role === "raw_value" ? "raw-cell" : ""}
                  >
                    {cell?.state === "available"
                      ? field.role === "match_state"
                        ? label(String(cell.value))
                        : text(cell.value)
                      : label(cell?.state || "not_read")}
                    {cell?.raw_text && cell.raw_text !== text(cell.value) && (
                      <small>原文：{cell.raw_text}</small>
                    )}
                    {cell?.classification && (
                      <small>
                        {label(cell.classification.state)}
                        {cell.classification.label
                          ? " · " + cell.classification.label
                          : ""}
                      </small>
                    )}
                    {cell?.reported_state && (
                      <small>{label(cell.reported_state)}</small>
                    )}
                  </td>
                );
              })}
              <td>
                <Badge tone={tone(row.match)}>{label(row.match)}</Badge>
                <a
                  className="text-link"
                  href={
                    href(
                      "extractions",
                      sessionId,
                      "facts",
                      row.source_fact_id,
                    ) +
                    "?clause=" +
                    encodeURIComponent(table.clause_key)
                  }
                >
                  事实与证据 →
                </a>
                {(row.flags?.length || 0) > 0 && (
                  <small>{row.flags?.length} 项待核对</small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function OrganizedTable({
  table,
  sessionId,
}: {
  table: OrganizedDataset_OrganizedTable;
  sessionId: string;
}) {
  const [view, setView] = useState(
    table.definition.presentation?.view === "matrix" ? "matrix" : "rows",
  );
  const matrix = table.matrix;
  return (
    <>
      <div className="section-heading">
        <div>
          <Badge tone={tone(table.status)}>{label(table.status)}</Badge>
          <span className="muted">
            {" "}
            {table.rows?.length || 0} 行 · {label(table.data_kind)}
          </span>
        </div>
        {matrix && (
          <div className="segmented">
            <button
              aria-pressed={view === "rows"}
              onClick={() => setView("rows")}
            >
              明细
            </button>
            <button
              aria-pressed={view === "matrix"}
              onClick={() => setView("matrix")}
            >
              矩阵
            </button>
          </div>
        )}
      </div>
      {!table.rows?.length ? (
        <Empty title="此条款尚无组织结果" />
      ) : view === "matrix" && matrix ? (
        <>
          <p className="muted">每格显示来源原值；多项并列保留，不自动求和。</p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>
                    {matrix.rows.length} 行轴 / {matrix.columns.length} 列轴
                  </th>
                  {matrix.columns.map((c) => (
                    <th key={c.coordinate_id}>
                      {Object.values(c.keys)
                        .map((v) => text(v.value))
                        .join(" / ")}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.rows.map((r) => (
                  <tr key={r.coordinate_id}>
                    <th>
                      {Object.values(r.keys)
                        .map((v) => text(v.value))
                        .join(" / ")}
                    </th>
                    {matrix.columns.map((c) => {
                      const cell = matrix.cells.find(
                        (x) =>
                          x.row_coordinate_id === r.coordinate_id &&
                          x.column_coordinate_id === c.coordinate_id,
                      );
                      return (
                        <td key={c.coordinate_id}>
                          {cell
                            ? cell.row_ids.map((id) => {
                                const row = table.rows?.find(
                                  (x) => x.row_id === id,
                                );
                                return row ? (
                                  <div key={id}>
                                    <a
                                      href={
                                        href(
                                          "extractions",
                                          sessionId,
                                          "facts",
                                          row.source_fact_id,
                                        ) +
                                        "?clause=" +
                                        encodeURIComponent(table.clause_key)
                                      }
                                    >
                                      {matrix.value_fields
                                        .map((f) => text(row.cells[f]?.value))
                                        .join(" / ")}
                                    </a>
                                  </div>
                                ) : null;
                              })
                            : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : table.groups?.length ? (
        <div className="grouped-results">
          {table.groups.map((g) => (
            <section key={g.group_id}>
              <h3>
                {Object.entries(g.keys)
                  .map(
                    ([key, c]) =>
                      (table.definition.fields.find((f) => f.key === key)
                        ?.label || key) +
                      "：" +
                      text(c.value),
                  )
                  .join(" · ")}
              </h3>
              <RowTable
                table={table}
                rows={table.rows!.filter((r) => g.row_ids.includes(r.row_id))}
                sessionId={sessionId}
              />
            </section>
          ))}
        </div>
      ) : (
        <RowTable table={table} rows={table.rows} sessionId={sessionId} />
      )}
    </>
  );
}
export function OrganizedList({
  dataset,
  sessionId,
}: {
  dataset: OrganizedDataset;
  sessionId: string;
}) {
  return dataset.tables?.length ? (
    <div className="object-list">
      {dataset.tables.map((t) => (
        <a
          className="object-row"
          key={t.table_id}
          href={href("extractions", sessionId, "tables", t.clause_key)}
        >
          <div>
            <strong>{t.definition.title}</strong>
            <small>
              {t.clause_key} · {t.rows?.length || 0} 行
            </small>
          </div>
          <Badge tone={tone(t.status)}>{label(t.status)}</Badge>
          <span aria-hidden="true">→</span>
        </a>
      ))}
    </div>
  ) : (
    <Empty title="尚无条款组织结果">任务执行后会按冻结条款分别组织。</Empty>
  );
}
