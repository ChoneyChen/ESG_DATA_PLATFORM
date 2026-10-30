import { useState, type FormEvent } from "react";
import { request, write, segment } from "../../shared/api";
import { invalidate } from "../../shared/resource";
import type {
  ReportResource,
  ReportImportResult,
  ReportUploadResult,
} from "../../shared/contracts";
import { Field, Modal, Notice } from "../../shared/ui";

type Metadata = {
  company: string;
  companyId: string;
  industry: string;
  industryCode: string;
  publication: string;
  period: string;
  title: string;
};
const initial = (resource?: ReportResource): Metadata => ({
  company: resource?.company?.name || "",
  companyId: resource?.company?.company_id || "",
  industry: resource?.industry?.name || "",
  industryCode: resource?.industry?.code || "",
  publication: resource?.publication_year?.toString() || "",
  period: resource?.reporting_year?.toString() || "",
  title: resource?.title || "",
});
function MetadataFields({
  value,
  set,
}: {
  value: Metadata;
  set: (value: Metadata) => void;
}) {
  const update = (key: keyof Metadata, text: string) =>
    set({ ...value, [key]: text });
  return (
    <div className="form-grid">
      <Field label="企业（选填）">
        <input
          value={value.company}
          onChange={(e) => update("company", e.target.value)}
        />
      </Field>
      <Field label="企业编号（选填）">
        <input
          value={value.companyId}
          onChange={(e) => update("companyId", e.target.value)}
        />
      </Field>
      <Field label="发表年份（选填）">
        <input
          type="number"
          min="1900"
          max="2200"
          value={value.publication}
          onChange={(e) => update("publication", e.target.value)}
        />
      </Field>
      <Field label="报告覆盖年度（选填）">
        <input
          type="number"
          min="1900"
          max="2200"
          value={value.period}
          onChange={(e) => update("period", e.target.value)}
        />
      </Field>
      <Field label="所属行业（选填）">
        <input
          value={value.industry}
          onChange={(e) => update("industry", e.target.value)}
        />
      </Field>
      <Field label="行业代码（选填）">
        <input
          value={value.industryCode}
          onChange={(e) => update("industryCode", e.target.value)}
        />
      </Field>
      <Field label="报告名称（选填）">
        <input
          value={value.title}
          onChange={(e) => update("title", e.target.value)}
        />
      </Field>
    </div>
  );
}
function metadata(value: Metadata, original?: ReportResource) {
  return {
    company: value.company.trim()
      ? {
          name: value.company.trim(),
          company_id: value.companyId.trim() || null,
        }
      : null,
    industry: value.industry.trim()
      ? {
          name: value.industry.trim(),
          code: value.industryCode.trim() || null,
          taxonomy: original?.industry?.taxonomy || null,
        }
      : null,
    publication_year: value.publication ? Number(value.publication) : null,
    reporting_year: value.period ? Number(value.period) : null,
    title: value.title.trim() || null,
  };
}

export function EditReportDialog({
  resource,
  onClose,
  onSaved,
}: {
  resource: ReportResource;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [value, setValue] = useState(() => initial(resource)),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (!resource.revision) throw new Error("报告修订信息缺失，请重新载入。");
      const changes = metadata(value, resource);
      const sources = { ...resource.metadata_sources };
      for (const key of Object.keys(changes) as Array<keyof typeof changes>)
        if (JSON.stringify(changes[key]) !== JSON.stringify(resource[key]))
          sources[key] = "console:manual";
      const next: ReportResource = {
        schema_version: resource.schema_version,
        pdf_id: resource.pdf_id,
        asset_id: resource.asset_id,
        sha256: resource.sha256,
        original_filename: resource.original_filename,
        ...changes,
        metadata_sources: sources,
        extensions: resource.extensions,
        revision: resource.revision,
      };
      await write(
        "document",
        "/api/report-resources/" + segment(resource.pdf_id),
        { resource: next, expected_revision: resource.revision },
        "PUT",
      );
      invalidate("document", "/api/report-resources");
      onSaved();
      onClose();
    } catch (error) {
      setError(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="编辑报告身份"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button
            type="button"
            className="button-secondary"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button form="edit-report" disabled={busy}>
            {busy ? "保存中…" : "保存修改"}
          </button>
        </>
      }
    >
      <form id="edit-report" onSubmit={save}>
        {error && <Notice tone="error">{error}</Notice>}
        <MetadataFields value={value} set={setValue} />
        <p className="field-help">
          报告身份与数值的实际统计主体、测量期间分别保存。
        </p>
      </form>
    </Modal>
  );
}

export function ImportReportDialog({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: (resource?: ReportResource) => void;
}) {
  const [mode, setMode] = useState<"file" | "folder">("file"),
    [value, setValue] = useState(() => initial()),
    [file, setFile] = useState<File>(),
    [folder, setFolder] = useState(""),
    [preview, setPreview] = useState<ReportImportResult>(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  function changedFolder(value: string) {
    setFolder(value);
    setPreview(undefined);
    setError("");
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "file") {
        if (!file) throw new Error("请选择 PDF。");
        if (file.size > 100 * 1024 * 1024)
          throw new Error("单份 PDF 不得超过 100 MB。");
        const body = new FormData();
        body.append("file", file);
        body.append("metadata", JSON.stringify(metadata(value)));
        const result = await request<ReportUploadResult>(
          "document",
          "/api/report-resources/uploads",
          { method: "POST", body },
        );
        invalidate("document", "/api/report-resources");
        onImported(result.resource);
        onClose();
      } else {
        if (!folder.trim()) throw new Error("请输入本机批量目录。");
        const result = await write<ReportImportResult>(
          "document",
          "/api/report-resources/imports",
          { folder: folder.trim(), preview: !preview },
        );
        if (result.preview) setPreview(result);
        else {
          invalidate("document", "/api/report-resources");
          onImported();
          onClose();
        }
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "导入失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="导入报告"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button
            type="button"
            className="button-secondary"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button form="import-report" disabled={busy}>
            {busy
              ? "处理中…"
              : mode === "folder"
                ? preview
                  ? "确认导入"
                  : "检查目录"
                : "导入报告"}
          </button>
        </>
      }
    >
      <form id="import-report" onSubmit={submit}>
        <div className="segmented" role="group" aria-label="导入方式">
          <button
            type="button"
            aria-pressed={mode === "file"}
            onClick={() => {
              setMode("file");
              setError("");
            }}
            disabled={busy}
          >
            上传 PDF
          </button>
          <button
            type="button"
            aria-pressed={mode === "folder"}
            onClick={() => {
              setMode("folder");
              setError("");
            }}
            disabled={busy}
          >
            本机批量目录
          </button>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {mode === "file" ? (
          <>
            <Field label="PDF 文件">
              <input
                type="file"
                accept=".pdf,application/pdf"
                required
                onChange={(e) => setFile(e.target.files?.[0])}
              />
            </Field>
            <MetadataFields value={value} set={setValue} />
            <p className="field-help">
              只登记报告身份；文档处理在创建提取任务后执行。
            </p>
          </>
        ) : (
          <>
            <Field label="本机目录" hint="目录中包含 manifest.json 和 pdf/。">
              <input
                value={folder}
                onChange={(e) => changedFolder(e.target.value)}
                required
              />
            </Field>
            {preview && (
              <div className="import-preview">
                <h3>检查结果 · {preview.reports.length} 份</h3>
                <ul>
                  {preview.reports.map((report) => (
                    <li key={report.pdf_id}>
                      <code>{report.pdf_id}</code>
                      <span>
                        {report.status === "already_present"
                          ? "已存在"
                          : "可以导入"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </form>
    </Modal>
  );
}
