import { useState } from "react";
import type {
  ReportList,
  ReportResource,
  ExtractionSession,
  ReportFolderList,
  ReportFolderSummary,
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

export function ReportsPage({ folderId }: { folderId?: string }) {
  const [search, setSearch] = useState(""),
    [company, setCompany] = useState(""),
    [industry, setIndustry] = useState(""),
    [year, setYear] = useState(""),
    [importing, setImporting] = useState(false);
  const result = useResource<ReportList>(
    "document",
    folderId ? "/api/report-resources?folder_id=" + segment(folderId) : "/api/report-resources?unfiled=true",
    10000,
  );
  const folders = useResource<ReportFolderList>("document", folderId ? null : "/api/report-resources/folders", 10000);
  const folder = useResource<ReportFolderSummary>("document", folderId ? "/api/report-resources/folders/"+segment(folderId) : null);
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
  ).sort((a,b) => (a.company?.name || "").localeCompare(b.company?.name || "") || (b.reporting_year || 0)-(a.reporting_year || 0));
  return (
    <>
      <PageHeader
        title={folderId ? folder.data?.name || "报告文件夹" : "报告"}
        back={folderId ? { href: href("reports"), label: "报告" } : undefined}
        subtitle={
          result.data ? folderId ? `${all.length} 份报告` : `${folders.data?.folders.length || 0} 个文件夹 · ${all.length} 份独立报告` : undefined
        }
        actions={<>
          {folderId && <a className="button" href={href("extractions","new")+"?folder="+segment(folderId)}>批量提取此文件夹</a>}
          <button className={folderId ? "button-secondary" : undefined} onClick={() => setImporting(true)}>导入报告</button>
        </>}
      />
      {!folderId && <>
        <ReadError error={folders.error} retry={folders.reload} />
        {!!folders.data?.folders.length && <div className="group-grid report-folders">
          {folders.data.folders.filter(f => !search || f.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(f =>
            <a className="group-card" key={f.folder_id} href={href("reports","folders",f.folder_id)}>
              <div className="folder-heading"><svg className="folder-icon" aria-hidden="true" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/><path d="M3 8h18"/></svg><h2>{f.name}</h2></div>
              <div><span>{f.report_count} 份报告</span><span aria-hidden="true">→</span></div>
            </a>)}
        </div>}
      </>}
      <ReadError error={folder.error} retry={folder.reload} />
      {!folderId && all.length > 0 && <div className="section-heading"><h2>独立报告</h2></div>}
      <div className="filters">
        <Field label={folderId ? "搜索报告" : "搜索文件夹或独立报告"}>
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
          title={all.length ? "没有符合筛选的报告" : folderId ? "文件夹中还没有报告" : "没有独立报告"}
          action={<button onClick={() => setImporting(true)}>导入报告</button>}
        >
          可以调整筛选或导入报告。
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
                      href={folderId ? href("reports","folders",folderId,report.pdf_id) : href("reports", report.pdf_id)}
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
          onImported={(resource, importedFolder) => {
            result.reload();
            folders.reload();
            if (importedFolder) navigate(href("reports","folders",importedFolder));
            else if (resource) navigate(href("reports",resource.pdf_id));
          }}
        />
      )}
    </>
  );
}

export function ReportPage({ pdfId, folderId }: { pdfId: string; folderId?: string }) {
  const resource = useResource<ReportResource>(
    "document",
    "/api/report-resources/" + segment(pdfId),
  );
  const [editing, setEditing] = useState(false);
  const report = resource.data;
  const period = report?.extensions?.reporting_period as { start?: string; end?: string } | undefined;
  const periodText = period ? [period.start,period.end].filter(Boolean).join(" — ") : null;
  const sourceUrl = report?.extensions?.source_url;
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
        back={{ href: folderId ? href("reports","folders",folderId) : href("reports"), label: folderId ? "报告文件夹" : "报告" }}
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
              {periodText && <div><span>报告期间</span><strong>{periodText}</strong></div>}
              <a
                className="text-link"
                href={pdfUrl(report.asset_id)}
                target="_blank"
                rel="noreferrer"
              >
                打开 PDF ↗
              </a>
              {typeof sourceUrl === "string" && /^https?:\/\//.test(sourceUrl) && <a className="text-link" href={sourceUrl} target="_blank" rel="noreferrer">原始来源 ↗</a>}
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
