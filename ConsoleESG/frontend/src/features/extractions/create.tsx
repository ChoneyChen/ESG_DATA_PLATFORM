import { useState, useRef, useEffect } from "react";
import type {
  ReportList,
  ClauseCatalog,
  ClauseGroup,
  CreateExtraction,
  ExtractionBatch,
  ExtractionBatchRequest,
  ExtractionSession,
  ExtractionBudget,
  Clause,
  ReportFolderList,
  ModelProviders,
  ModelSelection,
} from "../../shared/contracts";
import { EXTRACTION_BUDGET_DEFAULTS, EXTRACTION_BUDGET_LIMITS } from "../../shared/contracts";
import { RequestError, write } from "../../shared/api";
import { useResource, invalidate } from "../../shared/resource";
import {
  PageHeader,
  ReadError,
  Loading,
  Field,
  Notice,
  Badge,
  Empty,
  SelectionCheckbox,
} from "../../shared/ui";
import { href, navigate } from "../../app/routes";
import { label, modelSelectionLabel } from "../../shared/format";
import { extractionBatchesPath } from "./batches";
type Preview = {
  targets: { digest: string; clauses: Record<string, Clause> };
  clause_count: number;
  engine_fingerprint: string;
  calls_model: false;
  model_selection?: ModelSelection;
  document_model_selection?: ModelSelection | null;
};
type Selection = { selections: Record<string, string[]> };
const modelProvidersPath = "/api/model-providers";
export function CreateExtractionPage({
  initialReport,
  initialFolder,
}: {
  initialReport?: string;
  initialFolder?: string;
}) {
  const [reportFolder, setReportFolder] = useState(initialFolder || "");
  const reports = useResource<ReportList>("document", "/api/report-resources"),
    folders = useResource<ReportFolderList>("document","/api/report-resources/folders",10000),
    members = useResource<ReportList>("document",reportFolder ? "/api/report-resources?"+(reportFolder === "__unfiled" ? "unfiled=true" : "folder_id="+encodeURIComponent(reportFolder)) : null,10000),
    catalog = useResource<ClauseCatalog>("extraction", "/api/clauses"),
    groups = useResource<{ groups: ClauseGroup[] }>(
      "extraction",
      "/api/clause-groups",
    ),
    sets = useResource<Selection>("extraction", "/api/clause-selections");
  const [selectedReports, setReports] = useState<string[]>(
      initialReport ? [initialReport] : [],
    ),
    [keys, setKeys] = useState<string[]>([]),
    [name, setName] = useState("条款提取"),
    [query, setQuery] = useState(""),
    [group, setGroup] = useState(""),
    [budget, setBudget] = useState<ExtractionBudget>(EXTRACTION_BUDGET_DEFAULTS),
    [modelSelection, setModelSelection] = useState<ModelSelection>({ provider: "local" }),
    [documentModel, setDocumentModel] = useState<"native" | "local" | "deepseek">("native"),
    [advanced, setAdvanced] = useState(false);
  const cloud = modelSelection.provider === "deepseek";
  const usesCloud = cloud || documentModel === "deepseek";
  const providers = useResource<ModelProviders>("extraction", usesCloud ? modelProvidersPath : null);
  const deepseek = providers.data?.providers.find((provider) => provider.provider === "deepseek");
  const credentialInput = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [submission] = useState(() => crypto.randomUUID());
  const submittedSessions = useRef<CreateExtraction[] | undefined>(undefined);
  const [confirmedTargets, setConfirmedTargets] = useState<Preview>(),
    [attempted, setAttempted] = useState(false),
    [unconfirmed, setUnconfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error>(),
    [step, setStep] = useState(1);
  const available = reports.data?.reports || [],
    visibleReports = (reportFolder ? members.data?.reports || [] : available)
      .slice().sort((a,b) => (a.company?.name || "").localeCompare(b.company?.name || "") || (b.reporting_year || 0)-(a.reporting_year || 0)),
    rows = (catalog.data?.clauses || []).filter(
      (c) =>
        (!group || c.group_id === group) &&
        (!query ||
          `${c.clause_key} ${c.title}`
            .toLowerCase()
            .includes(query.toLowerCase())),
    );
  const isBatch = selectedReports.length > 1;
  const reportsById = new Map(available.map(report => [report.pdf_id, report]));
  function body(pdfId: string): CreateExtraction {
    const report = reportsById.get(pdfId);
    if (!report?.asset_available) throw new Error("有报告已不可用，请返回重新选择。");
    const reportName = report.title || report.original_filename || pdfId;
    const suffix = isBatch ? ` · ${reportName.slice(0, 116)}` : "";
    return {
      input: { kind: "pdf", asset_id: report.asset_id },
      clause_keys: keys,
      name: name.trim().slice(0, 120 - suffix.length) + suffix,
      budget,
      ...(cloud ? { model_selection: modelSelection } : {}),
      ...(documentModel !== "native" ? { document_model_selection: documentModel === "deepseek"
        ? { provider: "deepseek", model: "deepseek-flash" } : { provider: "local" } } : {}),
    };
  }
  function toggle(list: string[], key: string, set: (v: string[]) => void) {
    set(list.includes(key) ? list.filter((x) => x !== key) : [...list, key]);
  }
  async function saveCredential() {
    const input = credentialInput.current;
    const apiKey = input?.value.trim();
    if (!input || !apiKey) {
      setError(new Error("请输入 DeepSeek API 密钥。"));
      return;
    }
    input.value = "";
    setBusy(true);
    setError(undefined);
    try {
      await write("extraction", `${modelProvidersPath}/deepseek/credential`, { api_key: apiKey }, "PUT");
      invalidate("extraction", modelProvidersPath);
    } catch {
      setError(new Error("DeepSeek 密钥保存失败，请检查本地提取服务后重试。"));
    } finally {
      setBusy(false);
    }
  }
  async function preview() {
    setBusy(true);
    setError(undefined);
    try {
      const target = await write<Preview>(
        "document", "/api/report-sessions/preview", body(selectedReports[0]),
      );
      setConfirmedTargets(target);
      setStep(2);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!confirmedTargets) return;
    setBusy(true);
    setError(undefined);
    setAttempted(true);
    try {
      const sessions = submittedSessions.current ?? selectedReports.map(id => ({
        ...body(id),
        expected_target_digest: confirmedTargets.targets.digest,
        expected_engine_fingerprint: confirmedTargets.engine_fingerprint,
      }));
      submittedSessions.current = sessions;
      let destination;
      if (isBatch) {
        const request: ExtractionBatchRequest = { submission_key: submission, name: name.trim(), sessions };
        const batch = await write<ExtractionBatch>("document", extractionBatchesPath, request);
        invalidate("document", extractionBatchesPath);
        destination = href("extractions", "batches", batch.batch_id);
      } else {
        const run = await write<ExtractionSession>("document", "/api/report-sessions", {
          ...sessions[0],
          submission_key: submission,
        });
        destination = href("extractions", run.session_id);
      }
      invalidate("document", "/api/report-sessions");
      invalidate("document", "/api/console/monitor");
      if (alive.current) navigate(destination);
    } catch (e) {
      setUnconfirmed(e instanceof RequestError && (e.status < 400 || e.status >= 500));
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  const ready =
    !!selectedReports.length && !!keys.length && !!name.trim() && !busy &&
    (!usesCloud || !!deepseek?.configured) &&
    (!cloud || (!!modelSelection.model && !!deepseek?.models.includes(modelSelection.model)));
  return (
    <>
      <PageHeader
        title="创建提取"
        back={{ href: href("extractions"), label: "提取" }}
        subtitle={`第 ${step} 步 · ${step === 1 ? "选择报告与条款" : "核对目标"}`}
      />
      <ReadError error={error} />
      {!reports.data || !catalog.data ? (
        <>
          <ReadError
            error={reports.error || catalog.error}
            retry={() => {
              reports.reload();
              catalog.reload();
            }}
          />
          <Loading />
        </>
      ) : step === 1 ? (
        <>
          <fieldset className="form-stack creation-fields" disabled={busy}>
            <Field label="任务名称">
              <input
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <section className="section">
              <div className="section-heading">
                <h2>1. 报告</h2>
                <span>已选 {selectedReports.length} 份</span>
              </div>
              <div className="toolbar">
                <Field label="报告文件夹"><select value={reportFolder} onChange={e => setReportFolder(e.target.value)}>
                  <option value="">全部报告</option><option value="__unfiled">独立报告</option>
                  {folders.data?.folders.map(f => <option key={f.folder_id} value={f.folder_id}>{f.name} · {f.report_count} 份</option>)}
                </select></Field>
                <SelectionCheckbox ids={visibleReports.filter(r => r.asset_available).map(r => r.pdf_id)} selected={selectedReports} onChange={setReports} label={reportFolder && reportFolder !== "__unfiled" ? "全选此文件夹" : "全选当前报告"}/>
                {!!selectedReports.length && <button className="text-button" onClick={() => setReports([])}>清空选择</button>}
              </div>
              <ReadError error={folders.error || members.error} retry={() => { folders.reload(); members.reload(); }}/>
              {reportFolder && !members.data && !members.error ? <Loading/> : visibleReports.length ? (
                <div className="choice-list">
                  {visibleReports.map((r) => (
                    <label className="choice-row" key={r.pdf_id}>
                      <input
                        type="checkbox"
                        disabled={!r.asset_available}
                        checked={selectedReports.includes(r.pdf_id)}
                        onChange={() =>
                          toggle(selectedReports, r.pdf_id, setReports)
                        }
                      />
                      <span>
                        <strong>{r.title || r.original_filename}</strong>
                        <small>
                          {r.company?.name || "企业未提供"} · 覆盖年度{" "}
                          {r.reporting_year || "未提供"}
                        </small>
                      </span>
                      {!r.asset_available && (
                        <Badge tone="error">PDF 不可用</Badge>
                      )}
                    </label>
                  ))}
                </div>
              ) : (
                <Empty
                  title={reportFolder ? "这个文件夹中没有可用报告" : "请先导入报告"}
                  action={
                    <a className="button" href={href("reports")}>
                      报告库
                    </a>
                  }
                />
              )}
            </section>
            <section className="section">
              <div className="section-heading">
                <h2>2. 独立条款</h2>
                <span>已选 {keys.length} 条</span>
              </div>
              <div className="toolbar">
                {Object.entries(sets.data?.selections || {}).map(
                  ([key, value]) => (
                    <button
                      className="button-secondary"
                      key={key}
                      onClick={() => setKeys([...new Set(value)])}
                    >
                      {(
                        {
                          base16: "基础",
                          priority32: "重点",
                          quantitative: "常用定量",
                          universal: "通用",
                          platform_major: "平台指标",
                        } as Record<string, string>
                      )[key] || key}{" "}
                      · {value.length} 条
                    </button>
                  ),
                )}
                <button className="text-button" onClick={() => setKeys([])}>
                  清空
                </button>
              </div>
              <div className="filters">
                <Field label="搜索条款">
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="编号或核心句"
                  />
                </Field>
                <Field label="展示组">
                  <select
                    value={group}
                    onChange={(e) => setGroup(e.target.value)}
                  >
                    <option value="">全部展示组</option>
                    {groups.data?.groups.map((g) => (
                      <option value={g.group_id} key={g.group_id}>
                        {g.title}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <p className="muted">
                快捷集合仅替你勾选条款；每条仍独立检索和阅读。
              </p>
              <div className="choice-list clause-picker">
                {rows.map((c) => (
                  <label className="choice-row" key={c.clause_key}>
                    <input
                      type="checkbox"
                      disabled={c.readiness !== "ready"}
                      checked={keys.includes(c.clause_key)}
                      onChange={() => toggle(keys, c.clause_key, setKeys)}
                    />
                    <span>
                      <strong>{c.title}</strong>
                      <small>
                        {c.clause_key} · {c.entry_kind === "structure" ? "结构描述" : label(c.data_kind)}
                      </small>
                    </span>
                    {c.readiness !== "ready" && (
                      <Badge tone={c.readiness === "structural" ? "neutral" : "warning"}>{label(c.readiness)}</Badge>
                    )}
                  </label>
                ))}
              </div>
            </section>
            <section className="section">
              <div className="section-heading"><h2>3. 提取模型</h2></div>
              <div className="form-grid">
                <Field label="文档定位">
                  <select value={documentModel} onChange={(e) => {
                    const choice = e.target.value;
                    setDocumentModel(choice === "deepseek" || choice === "local" ? choice : "native");
                  }}>
                    <option value="native">原生文字定位</option>
                    <option value="local">本地 Qwen 视觉定位</option>
                    <option value="deepseek">DeepSeek Flash 视觉定位</option>
                  </select>
                </Field>
                <Field label="模型提供者">
                  <select
                    value={modelSelection.provider}
                    onChange={(e) => {
                      setError(undefined);
                      setModelSelection({ provider: e.target.value === "deepseek" ? "deepseek" : "local" });
                    }}
                  >
                    <option value="local">本地 Qwen</option>
                    <option value="deepseek">DeepSeek 云端 API</option>
                  </select>
                </Field>
                {cloud && (
                  <Field label="DeepSeek 模型">
                    <select value={modelSelection.model || ""} onChange={(e) =>
                      setModelSelection({ provider: "deepseek", model: deepseek?.models.find((model) => model === e.target.value) })}>
                      <option value="">请选择模型</option>
                      {deepseek?.models.map((model) => <option key={model} value={model}>
                        {model}{model === "deepseek-v4-pro" ? "（纯文字）" : ""}
                      </option>)}
                    </select>
                  </Field>
                )}
              </div>
              {usesCloud && (
                <>
                  <Notice>选用 DeepSeek 的阶段会发送所需的页面图像或文字。PDF 页面在本机渲染。</Notice>
                  <ReadError error={providers.error} retry={providers.reload} />
                  {providers.loading && !deepseek && <Loading />}
                  {deepseek && (
                    <div className="toolbar">
                      <Field label="DeepSeek API 密钥">
                        <input ref={credentialInput} type="password" autoComplete="off" spellCheck={false} />
                      </Field>
                      <button type="button" className="button-secondary" onClick={saveCredential}>
                        {deepseek.configured ? "更新密钥" : "保存密钥"}
                      </button>
                      <Badge tone={deepseek.configured ? "success" : "warning"}>
                        {deepseek.configured ? "已配置" : "未配置"}
                      </Badge>
                    </div>
                  )}
                </>
              )}
            </section>
            <button
              className="disclosure-button"
              aria-expanded={advanced}
              onClick={() => setAdvanced(!advanced)}
            >
              执行预算 {advanced ? "−" : "＋"}
            </button>
            {advanced && (
              <div className="form-grid">
                {(
                  [
                    ["max_model_calls", "模型调用上限"],
                    ["max_objects_per_clause", "每条款证据对象上限"],
                    ["max_output_tokens", "单次输出 token 上限"],
                    ["timeout_seconds", "最长运行秒数"],
                  ] as const
                ).map(([key, title]) => (
                  <Field label={title} key={key}>
                    <input
                      type="number"
                      min={EXTRACTION_BUDGET_LIMITS[key].minimum}
                      max={EXTRACTION_BUDGET_LIMITS[key].maximum}
                      value={budget[key]}
                      onChange={(e) =>
                        setBudget({ ...budget, [key]: Number(e.target.value) })
                      }
                    />
                  </Field>
                ))}
              </div>
            )}
            <div className="form-actions">
              <a className="button button-secondary" href={href("extractions")}>
                取消
              </a>
              <button disabled={!ready} onClick={preview}>
                {busy ? "核对中…" : "核对提取目标"}
              </button>
            </div>
          </fieldset>
        </>
      ) : (
        <section className="section">
          <h2>确认本次选择</h2>
          <p>提取模型 · {modelSelectionLabel(confirmedTargets?.model_selection || modelSelection)}</p>
          <p>文档定位 · {documentModel === "native" ? "原生文字定位" : documentModel === "deepseek" ? "DeepSeek Flash 视觉定位" : "本地 Qwen 视觉定位"}</p>
          <div className="metric-strip">
            <div>
              <strong>{selectedReports.length}</strong>
              <span>份报告</span>
            </div>
            <div>
              <strong>{keys.length}</strong>
              <span>条独立条款 / 报告</span>
            </div>
            <div>
              <strong>{selectedReports.length * keys.length}</strong>
              <span>个提取目标</span>
            </div>
          </div>
          <ul className="plain-list">
            {keys.map((k) => (
              <li key={k}>
                {catalog.data?.clauses.find((c) => c.clause_key === k)?.title ||
                  k}{" "}
                <small>{k}</small>
              </li>
            ))}
          </ul>
          <Notice>
            提交时冻结条款和模型选择。模型按页读取并填写原值。此预览未调用模型。
          </Notice>
          {isBatch && <details open>
            <summary>查看报告入队顺序</summary>
            <p className="field-help">报告按此顺序进入批次窗口。有报告结束时，下一份自动补入。</p>
            <ol>
              {selectedReports.map(id => {
                const report = reportsById.get(id);
                return <li key={id}>{report?.title || report?.original_filename || id}</li>;
              })}
            </ol>
          </details>}
          {unconfirmed && <Notice>
            服务尚未确认本次提交，可以用同一次提交重试。
            {isBatch && <>也可以到<a href={href("extractions", "batches")}>提取批次</a>核对。</>}
          </Notice>}
          <div className="form-actions">
            <button
              className="button-secondary"
              disabled={busy || unconfirmed}
              onClick={() => {
                setStep(1);
                submittedSessions.current = undefined;
                setAttempted(false);
                setError(undefined);
              }}
            >
              返回修改
            </button>
            <button disabled={busy} onClick={submit}>
              {busy
                ? "提交中…"
                : attempted
                  ? "重试提交"
                  : "开始提取"}
            </button>
          </div>
        </section>
      )}
    </>
  );
}
