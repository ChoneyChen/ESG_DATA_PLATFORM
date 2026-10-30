import { useState } from "react";
import type {
  ReportList,
  ReportResource,
  ExtractionSession,
} from "../../shared/contracts";
import { useResource } from "../../shared/resource";
import { pdfUrl, segment } from "../../shared/api";
import {
  Badge,
  Empty,
  Field,
  Loading,
  PageHeader,
  ReadError,
} from "../../shared/ui";
import { href, navigate } from "../../app/routes";
import { SessionRows } from "../extractions/list";
import { EditReportDialog, ImportReportDialog } from "./dialogs";

export function ReportsPage() {
  const [search, setSearch] = useState(""),
    [company, setCompany] = useState(""),
    [industry, setIndustry] = useState(""),
    [year, setYear] = useState(""),
    [importing, setImporting] = useState(false);
  const result = useResource<ReportList>(
    "document",
    "/api/report-resources",
    10000,
  );
  const all = result.data?.reports || [];
  const contains = (values: (string | null | undefined)[], query: string) =>
    !query ||
    values.some((value) =>
      value?.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
    );
  const rows = all.filter(
    (r) =>
      contains(
        [r.title, r.original_filename, r.pdf_id, r.company?.name],
        search,
      ) &&
      contains([r.company?.name, r.company?.company_id], company) &&
      contains([r.industry?.name, r.industry?.code], industry) &&
      (!year || String(r.publication_year) === year),
  );
  return (
    <>
      <PageHeader
        title="报告"
        subtitle={
          result.data ? `${all.length} 份 PDF` : "管理报告身份与提取入口"
        }
        actions={<button onClick={() => setImporting(true)}>导入报告</button>}
      />
      <div className="filters">
        <Field label="搜索报告">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="名称、企业或 PDF 编号"
          />
        </Field>
        <Field label="企业">
          <input
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            placeholder="企业名称或编号"
          />
        </Field>
        <Field label="行业">
          <input
            value={industry}
            onChange={(e) => setIndustry(e.target.value)}
            placeholder="行业名称或代码"
          />
        </Field>
        <Field label="发表年份">
          <input
            type="number"
            min="1900"
            max="2200"
            value={year}
            onChange={(e) => setYear(e.target.value)}
          />
        </Field>
      </div>
      <ReadError error={result.error} retry={result.reload} />
      {result.loading && !result.data ? (
        <Loading />
      ) : result.data && rows.length === 0 ? (
        <Empty
          title={all.length ? "没有符合筛选的报告" : "报告库为空"}
          action={<button onClick={() => setImporting(true)}>导入报告</button>}
        >
          可以调整筛选，或导入 PDF 与报告身份。
        </Empty>
      ) : (
        <div className="table-scroll reports-table">
          <table className="data-table">
            <thead>
              <tr>
                <th>报告</th>
                <th>企业</th>
                <th>发表年 / 覆盖年度</th>
                <th>行业</th>
                <th>身份</th>
                <th>
                  <span className="sr-only">操作</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((report) => (
                <tr key={report.pdf_id}>
                  <td>
                    <a
                      className="row-title"
                      href={href("reports", report.pdf_id)}
                    >
                      {report.title || report.original_filename}
                    </a>
                    <small>{report.pdf_id}</small>
                  </td>
                  <td>{report.company?.name || "未提供"}</td>
                  <td>
                    {report.publication_year || "—"} /{" "}
                    {report.reporting_year || "—"}
                  </td>
                  <td>{report.industry?.name || "未提供"}</td>
                  <td>
                    <Badge
                      tone={
                        report.missing_metadata.length ? "warning" : "success"
                      }
                    >
                      {report.missing_metadata.length ? "待补充" : "完整"}
                    </Badge>
                  </td>
                  <td>
                    <a
                      className="button button-secondary"
                      href={
                        href("extractions", "new") +
                        "?report=" +
                        segment(report.pdf_id)
                      }
                    >
                      开始提取
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {importing && (
        <ImportReportDialog
          onClose={() => setImporting(false)}
          onImported={(resource) => {
            result.reload();
            if (resource) navigate("/reports/" + segment(resource.pdf_id));
          }}
        />
      )}
    </>
  );
}

export function ReportPage({ pdfId }: { pdfId: string }) {
  const resource = useResource<ReportResource>(
    "document",
    "/api/report-resources/" + segment(pdfId),
  );
  const [editing, setEditing] = useState(false);
  const report = resource.data;
  const sessions = useResource<ExtractionSession[]>(
    "document",
    "/api/report-sessions",
    2500,
  );
  const runs = (sessions.data || []).filter(
    (r) =>
      r.request.input.asset_id === report?.asset_id ||
      r.run?.document?.report_resource?.pdf_id === pdfId,
  );
  return (
    <>
      <PageHeader
        title={report?.title || report?.original_filename || "报告详情"}
        back={{ href: href("reports"), label: "报告" }}
        actions={
          report && (
            <>
              <button
                className="button-secondary"
                onClick={() => setEditing(true)}
              >
                编辑身份
              </button>
              <a
                className="button"
                href={href("extractions", "new") + "?report=" + segment(pdfId)}
              >
                开始提取
              </a>
            </>
          )
        }
      />
      <ReadError error={resource.error} retry={resource.reload} />
      {!report && !resource.error ? (
        <Loading />
      ) : (
        report && (
          <>
            <section className="identity-panel">
              <div>
                <span>PDF 编号</span>
                <strong>{report.pdf_id}</strong>
              </div>
              <div>
                <span>企业</span>
                <strong>{report.company?.name || "未提供"}</strong>
              </div>
              <div>
                <span>发表年份</span>
                <strong>{report.publication_year || "未提供"}</strong>
              </div>
              <div>
                <span>覆盖年度</span>
                <strong>{report.reporting_year || "未提供"}</strong>
              </div>
              <div>
                <span>所属行业</span>
                <strong>{report.industry?.name || "未提供"}</strong>
              </div>
              <a
                className="text-link"
                href={pdfUrl(report.asset_id)}
                target="_blank"
                rel="noreferrer"
              >
                打开 PDF ↗
              </a>
            </section>
            <section className="section">
              <div className="section-heading">
                <h2>提取记录</h2>
                <a href={href("extractions") + "?report=" + segment(pdfId)}>
                  查看任务 →
                </a>
              </div>
              <ReadError error={sessions.error} retry={sessions.reload} />
              {!sessions.data && !sessions.error ? (
                <Loading />
              ) : runs.length ? (
                <SessionRows runs={runs} />
              ) : (
                <Empty
                  title="还没有提取记录"
                  action={
                    <a
                      className="button"
                      href={
                        href("extractions", "new") + "?report=" + segment(pdfId)
                      }
                    >
                      开始提取
                    </a>
                  }
                />
              )}
            </section>
            {editing && (
              <EditReportDialog
                resource={report}
                onClose={() => setEditing(false)}
                onSaved={resource.reload}
              />
            )}
          </>
        )
      )}
    </>
  );
}
