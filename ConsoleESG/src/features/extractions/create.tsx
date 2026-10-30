import { useState, useRef, useEffect } from "react";
import type {
  ReportList,
  ClauseCatalog,
  ClauseGroup,
  CreateExtraction,
  ExtractionSession,
  ExtractionBudget,
  Clause,
} from "../../shared/contracts";
import { write } from "../../shared/api";
import { useResource, invalidate } from "../../shared/resource";
import {
  PageHeader,
  ReadError,
  Loading,
  Field,
  Notice,
  Badge,
  Empty,
} from "../../shared/ui";
import { href, navigate } from "../../app/routes";
import { label } from "../../shared/format";
type Preview = {
  targets: { digest: string; clauses: Record<string, Clause> };
  clause_count: number;
  engine_fingerprint: string;
  calls_model: false;
};
type Selection = { selections: Record<string, string[]> };
const defaults: ExtractionBudget = {
  max_model_calls: 128,
  max_objects_per_clause: 8,
  max_output_tokens: 4096,
  timeout_seconds: 14400,
};
export function CreateExtractionPage({
  initialReport,
}: {
  initialReport?: string;
}) {
  const reports = useResource<ReportList>("document", "/api/report-resources"),
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
    [budget, setBudget] = useState(defaults),
    [advanced, setAdvanced] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [submission] = useState(() => crypto.randomUUID());
  const [previews, setPreviews] = useState<Record<string, Preview>>({}),
    [created, setCreated] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error>(),
    [step, setStep] = useState(1);
  const available = reports.data?.reports || [],
    rows = (catalog.data?.clauses || []).filter(
      (c) =>
        (!group || c.group_id === group) &&
        (!query ||
          `${c.clause_key} ${c.title}`
            .toLowerCase()
            .includes(query.toLowerCase())),
    );
  const body = (pdfId: string): CreateExtraction => ({
    submission_key: submission + "-" + pdfId,
    input: {
      kind: "pdf",
      asset_id: available.find((r) => r.pdf_id === pdfId)!.asset_id,
    },
    clause_keys: keys,
    name:
      selectedReports.length > 1
        ? `${name} · ${available.find((r) => r.pdf_id === pdfId)?.company?.name || pdfId}`
        : name,
    budget,
  });
  function toggle(list: string[], key: string, set: (v: string[]) => void) {
    set(list.includes(key) ? list.filter((x) => x !== key) : [...list, key]);
  }
  async function preview() {
    setBusy(true);
    setError(undefined);
    try {
      const next: Record<string, Preview> = {};
      for (const id of selectedReports)
        next[id] = await write(
          "document",
          "/api/report-sessions/preview",
          body(id),
        );
      setPreviews(next);
      setStep(2);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    setBusy(true);
    setError(undefined);
    const next = { ...created };
    try {
      for (const id of selectedReports) {
        if (next[id]) continue;
        const p = previews[id];
        const run = await write<ExtractionSession>(
          "document",
          "/api/report-sessions",
          {
            ...body(id),
            expected_target_digest: p.targets.digest,
            expected_engine_fingerprint: p.engine_fingerprint,
          },
        );
        next[id] = run.session_id;
        setCreated({ ...next });
      }
      invalidate("document", "/api/report-sessions");
      if (selectedReports.length === 1 && alive.current)
        navigate("/extractions/" + next[selectedReports[0]]);
      else setStep(3);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  const ready =
    !!selectedReports.length && !!keys.length && !!name.trim() && !busy;
  return (
    <>
      <PageHeader
        title="创建提取"
        back={{ href: href("extractions"), label: "提取" }}
        subtitle={`第 ${step} 步 · ${step === 1 ? "选择报告与条款" : step === 2 ? "核对目标" : "已提交"}`}
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
              {available.length ? (
                <div className="choice-list">
                  {available.map((r) => (
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
                  title="请先导入报告"
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
                        {c.clause_key} · {label(c.data_kind)}
                      </small>
                    </span>
                    {c.readiness !== "ready" && (
                      <Badge tone="warning">待完善</Badge>
                    )}
                  </label>
                ))}
              </div>
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
                    ["max_model_calls", "模型调用上限", 1, 2000],
                    ["max_objects_per_clause", "每条款证据对象上限", 1, 100],
                    ["max_output_tokens", "单次输出 token 上限", 128, 8192],
                    ["timeout_seconds", "最长运行秒数", 1, 86400],
                  ] as const
                ).map(([key, title, min, max]) => (
                  <Field label={title} key={key}>
                    <input
                      type="number"
                      min={min}
                      max={max}
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
      ) : step === 2 ? (
        <section className="section">
          <h2>确认本次选择</h2>
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
            提交时冻结条款内容和引擎指纹。PDF
            准备后逐条检索、阅读并保存原值。此预览未调用模型。
          </Notice>
          {Object.entries(created).map(([id, run]) => (
            <p key={id}>
              已提交：
              <a href={href("extractions", run)}>
                {available.find((r) => r.pdf_id === id)?.original_filename}
              </a>
            </p>
          ))}
          <div className="form-actions">
            <button
              className="button-secondary"
              disabled={busy || Object.keys(created).length > 0}
              onClick={() => setStep(1)}
            >
              返回修改
            </button>
            <button disabled={busy} onClick={submit}>
              {busy
                ? "提交中…"
                : Object.keys(created).length
                  ? "提交剩余报告"
                  : "开始提取"}
            </button>
          </div>
        </section>
      ) : (
        <Empty
          title={`已创建 ${Object.keys(created).length} 个独立任务`}
          action={
            <a className="button" href={href("extractions")}>
              查看提取任务
            </a>
          }
        />
      )}
    </>
  );
}
