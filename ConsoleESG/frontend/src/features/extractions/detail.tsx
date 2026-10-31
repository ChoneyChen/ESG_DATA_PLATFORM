import { useState, useEffect } from "react";
import type {
  ExtractionSession,
  OrganizedDataset,
  SourceFact,
  ReportList,
  SourceFact_ReportedField,
  CandidateRejection,
} from "../../shared/contracts";
import { useResource, invalidate } from "../../shared/resource";
import { segment, write } from "../../shared/api";
import {
  PageHeader,
  ReadError,
  Loading,
  Badge,
  Notice,
  Tabs,
  Modal,
  Empty,
} from "../../shared/ui";
import {
  date,
  label,
  tone,
  download,
  text,
  periodText,
} from "../../shared/format";
import { href, navigate } from "../../app/routes";
import { ProductStrip } from "../publication/data";
import {
  FactsTable,
  FactEvidence,
  OrganizedList,
  OrganizedTable,
  type FactBundle,
} from "./results";
const identityNames: Record<string, string> = {
  reporting_period: "实际测量期间",
  reporting_entity: "实际统计主体",
  entity_aggregation_role: "主体汇总方式",
  reporting_boundary: "实际范围",
  measurement_method: "测量方法",
  value_role: "数值角色",
  aggregation_role: "汇总角色",
  denominator: "分母",
};
const terminal = ["completed", "partial", "cancelled", "failed", "interrupted"];
export function ExtractionPage({
  sessionId,
  child,
  childId,
  tabId,
  returnClause,
}: {
  sessionId: string;
  child?: string;
  childId?: string;
  tabId?: string;
  returnClause?: string;
}) {
  const session = useResource<ExtractionSession>(
      "document",
      "/api/report-sessions/" + segment(sessionId),
      1800,
    ),
    run = session.data;
  const active = !!run && !terminal.includes(run.status);
  const results = useResource<FactBundle>(
    "document",
    run ? "/api/report-sessions/" + segment(sessionId) + "/facts" : null,
    active ? 4000 : 0,
  );
  const organized = useResource<OrganizedDataset>(
    "document",
    run ? "/api/report-sessions/" + segment(sessionId) + "/organized" : null,
    active ? 4000 : 0,
  );
  useEffect(() => {
    if (run && terminal.includes(run.status)) {
      results.reload();
      organized.reload();
    }
  }, [run?.status, results.reload, organized.reload]);
  const reports = useResource<ReportList>("document", "/api/report-resources");
  const candidates = useResource<CandidateRejection[]>(
    "document",
    run ? "/api/report-sessions/" + segment(sessionId) + "/candidates" : null,
    active ? 4000 : 0,
  );
  const tab = [
    "results",
    "facts",
    "coverage",
    "details",
    "candidates",
  ].includes(tabId || "")
    ? tabId!
    : "results";
  const [confirmCancel, setConfirmCancel] = useState(false),
    [cancelling, setCancelling] = useState(false),
    [error, setError] = useState<Error>(),
    [evidence, setEvidence] = useState<SourceFact>();
  const report =
    run?.run?.document?.report_resource ||
    reports.data?.reports.find(
      (r) => r.asset_id === run?.request.input.asset_id,
    );
  const table = organized.data?.tables?.find((t) => t.clause_key === childId),
    fact = results.data?.facts.find((f) => f.source_fact_id === childId);
  async function cancel() {
    setCancelling(true);
    setError(undefined);
    try {
      await write(
        "document",
        "/api/report-sessions/" + segment(sessionId) + "/cancel",
        {},
      );
      invalidate("document", "/api/report-sessions");
      setConfirmCancel(false);
    } catch (e) {
      setError(e as Error);
    } finally {
      setCancelling(false);
    }
  }
  return (
    <>
      <PageHeader
        title={
          child === "tables"
            ? table?.definition.title || "条款结果"
            : child === "facts"
              ? "独立事实"
              : run?.name || "提取详情"
        }
        back={{
          href:
            child === "facts"
              ? returnClause
                ? href("extractions", sessionId, "tables", returnClause)
                : href("extractions", sessionId) + "?tab=facts"
              : child
                ? href("extractions", sessionId)
                : href("extractions"),
          label:
            child === "facts"
              ? returnClause
                ? "条款结果"
                : "原始事实"
              : child
                ? "本次提取"
                : "提取",
        }}
        subtitle={child === "tables" ? childId : undefined}
        actions={
          !child &&
          run && (
            <>
              {run.available_actions?.some((a) => a.action === "cancel") && (
                <button
                  className="button-danger"
                  onClick={() => setConfirmCancel(true)}
                >
                  取消任务
                </button>
              )}
              <a
                className="button button-secondary"
                href={
                  href("extractions", "new") +
                  (report ? "?report=" + segment(report.pdf_id) : "")
                }
              >
                再次选择提取
              </a>
            </>
          )
        }
      />
      <ReadError error={session.error} retry={session.reload} />
      {!run && !session.error ? (
        <Loading />
      ) : (
        run && (
          <>
            {!child && (
              <>
                <div className="run-heading">
                  <Badge tone={tone(run.status)}>{label(run.status)}</Badge>
                  {run.phase_label !== label(run.status) && (
                    <strong>{run.phase_label}</strong>
                  )}
                  {report && (
                    <a href={href("reports", report.pdf_id)}>
                      {report.title || report.original_filename} →
                    </a>
                  )}
                  <small>{date(run.created_at)}</small>
                </div>
                <ProductStrip sessionId={sessionId} />
                {run.observation === "unavailable" && (
                  <Notice tone="error">
                    {run.observation_error || "暂时无法确认执行状态。"}{" "}
                    <button className="text-button" onClick={session.reload}>
                      重新读取
                    </button>
                  </Notice>
                )}
                {run.error && <Notice tone="error">{run.error}</Notice>}
                {run.run?.delivery && (
                  <div className="toolbar">
                    <Badge
                      tone={
                        run.run.delivery.state === "delivered"
                          ? "success"
                          : "neutral"
                      }
                    >
                      {
                        (
                          {
                            prepared: "原值已封存 · 等待接收",
                            delivered: "原值已入库",
                            partial: "部分原值已入库",
                            failed: "原值交接待处理",
                            unknown: "自动确认接收回执中",
                          } as Record<string, string>
                        )[run.run.delivery.state || "prepared"]
                      }
                    </Badge>
                    {run.run.export_download_url && (
                      <a
                        className="button button-secondary"
                        href={"/extraction" + run.run.export_download_url}
                      >
                        下载独立数据包
                      </a>
                    )}
                    {run.run.delivery.accepted_facts != null && (
                      <small>
                        {run.run.delivery.accepted_facts} 条接收 ·{" "}
                        {run.run.delivery.rejected_facts || 0} 条未接收
                      </small>
                    )}
                  </div>
                )}
                <section className="progress-panel" aria-label="执行进度">
                  <div className="steps">
                    {run.preparation?.steps?.map((step) => (
                      <div className={"step " + step.status} key={step.stage}>
                        <span className="step-dot" />
                        <span>{step.label}</span>
                        <small>
                          {step.status === "partial"
                            ? "已结束 · 证据受限"
                            : label(step.status)}
                          {step.current != null && step.total
                            ? ` · ${step.current} / ${step.total} ${step.unit === "pages" ? "页" : ""}`
                            : ""}
                        </small>
                      </div>
                    ))}
                    <div
                      className={
                        "step " +
                        (run.status === "completed"
                          ? "completed"
                          : run.stage === "extracting"
                            ? run.status
                            : "pending")
                      }
                    >
                      <span className="step-dot" />
                      <span>条款检索与阅读</span>
                      <small>
                        {run.progress?.total
                          ? `${run.progress.current || 0} / ${run.progress.total} 个目标`
                          : "等待文档证据"}
                      </small>
                    </div>
                  </div>
                  {run.progress?.clause_key && active && (
                    <p>
                      当前条款 <strong>{run.progress.clause_key}</strong>
                      {run.progress.object_id && (
                        <small> · {run.progress.object_id}</small>
                      )}
                    </p>
                  )}
                  <div className="metric-strip">
                    <div>
                      <strong>{run.clause_keys?.length || 0}</strong>
                      <span>选择条款</span>
                    </div>
                    <div>
                      <strong>
                        {run.run?.summary?.source_facts ??
                          run.progress?.fact_count ??
                          results.data?.facts.length ??
                          0}
                      </strong>
                      <span>原始事实</span>
                    </div>
                    <div>
                      <strong>
                        {run.run?.summary?.model_calls ??
                          run.progress?.model_calls ??
                          0}
                      </strong>
                      <span>模型调用</span>
                    </div>
                  </div>
                  {run.run?.summary?.assessment?.periods && (
                    <div className="toolbar" aria-label="已取得期间">
                      {Object.entries(run.run.summary.assessment.periods).map(
                        ([period, count]) => (
                          <Badge key={period}>
                            {period} · {count} 条
                          </Badge>
                        ),
                      )}
                    </div>
                  )}
                  {run.run?.current_call && active && (
                    <div className="model-progress" aria-live="polite">
                      <strong>
                        模型：
                        {run.run.current_call.phase === "generation"
                          ? "生成中"
                          : run.run.current_call.phase === "prefill"
                            ? "读取输入"
                            : "处理中"}
                      </strong>
                      <span>
                        输入 {run.run.current_call.input_tokens ?? "—"} token
                      </span>
                      <span>
                        已生成 {run.run.current_call.generation_tokens ?? "—"}{" "}
                        token
                      </span>
                      {run.run.current_call.elapsed_seconds != null && (
                        <span>
                          {Math.round(run.run.current_call.elapsed_seconds)} 秒
                        </span>
                      )}
                    </div>
                  )}
                </section>
                <Tabs
                  value={tab}
                  onChange={(id) =>
                    navigate(href("extractions", sessionId) + "?tab=" + id)
                  }
                  tabs={[
                    { id: "results", label: "条款结果" },
                    {
                      id: "facts",
                      label: `原始事实 · ${results.data?.facts.length || 0}`,
                    },
                    { id: "coverage", label: "覆盖情况" },
                    {
                      id: "candidates",
                      label: `未纳入候选 · ${candidates.data?.length || 0}`,
                    },
                    { id: "details", label: "运行记录" },
                  ]}
                />
                {tab === "results" && (
                  <>
                    <ReadError
                      error={organized.error}
                      retry={organized.reload}
                    />
                    {organized.data ? (
                      <OrganizedList
                        dataset={organized.data}
                        sessionId={sessionId}
                      />
                    ) : (
                      <Loading />
                    )}
                  </>
                )}
                {tab === "facts" && (
                  <>
                    <ReadError error={results.error} retry={results.reload} />
                    {results.data && (
                      <FactsTable
                        facts={results.data.facts}
                        sessionId={sessionId}
                      />
                    )}
                  </>
                )}
                {tab === "coverage" && (
                  <>
                    <ReadError error={results.error} retry={results.reload} />
                    {results.data?.coverage.length ? (
                      <div className="table-scroll">
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th>条款</th>
                              <th>覆盖</th>
                              <th>已阅读 / 候选</th>
                              <th>事实</th>
                              <th>已取得期间</th>
                              <th>原因</th>
                            </tr>
                          </thead>
                          <tbody>
                            {results.data.coverage.map((c) => (
                              <tr key={c.clause_key}>
                                <td>
                                  <a
                                    href={href(
                                      "extractions",
                                      sessionId,
                                      "tables",
                                      c.clause_key,
                                    )}
                                  >
                                    {c.clause_key}
                                  </a>
                                </td>
                                <td>
                                  <Badge tone={tone(c.state)}>
                                    {label(c.state)}
                                  </Badge>
                                </td>
                                <td>
                                  {c.inspected_objects || 0} /{" "}
                                  {c.candidate_objects || 0}
                                  {c.search?.total != null && (
                                    <small>
                                      {" "}
                                      / 共 {c.search.total} 个候选
                                      {c.search.truncated ? " · 尚未读全" : ""}
                                    </small>
                                  )}
                                </td>
                                <td>{c.fact_count || 0}</td>
                                <td>
                                  {Object.entries(
                                    c.periods?.captured_facts || {},
                                  )
                                    .map(
                                      ([period, count]) =>
                                        `${period} · ${count}`,
                                    )
                                    .join("；") || "—"}
                                </td>
                                <td>{c.reasons?.join("；") || "—"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <Empty title="尚未计算覆盖情况" />
                    )}
                  </>
                )}
                {tab === "candidates" && (
                  <>
                    <ReadError
                      error={candidates.error}
                      retry={candidates.reload}
                    />
                    {candidates.data?.length ? (
                      <div className="table-scroll">
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th>条款</th>
                              <th>候选字串</th>
                              <th>PDF页</th>
                              <th>类型</th>
                              <th>处置原因</th>
                            </tr>
                          </thead>
                          <tbody>
                            {candidates.data.map((c, i) => (
                              <tr key={i}>
                                <td>{c.clause_key}</td>
                                <td>{c.value_raw || "—"}</td>
                                <td>
                                  {c.page_index == null
                                    ? "—"
                                    : c.page_index + 1}
                                </td>
                                <td>
                                  {(
                                    {
                                      formula: "化学式",
                                      identifier: "编号",
                                      footnote: "脚注",
                                      date: "日期",
                                      unresolved: "未证实为测量",
                                    } as Record<string, string>
                                  )[c.candidate_kind] || c.candidate_kind}
                                </td>
                                <td>{c.reason}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <Empty title="没有被排除的数字候选" />
                    )}
                  </>
                )}
                {tab === "details" && (
                  <section className="section">
                    <dl className="detail-list">
                      <dt>任务 ID</dt>
                      <dd>{sessionId}</dd>
                      <dt>执行 ID</dt>
                      <dd>{run.run_id || "尚未创建"}</dd>
                      <dt>文档证据 ID</dt>
                      <dd>{run.ir_run_id || "准备中"}</dd>
                      <dt>冻结目标</dt>
                      <dd className="mono">{run.target_digest || "—"}</dd>
                      <dt>引擎指纹</dt>
                      <dd className="mono">
                        {run.engine_fingerprint || "旧任务未记录"}
                      </dd>
                      <dt>条款</dt>
                      <dd>{run.clause_keys?.join("、")}</dd>
                    </dl>
                    <div className="toolbar">
                      <button
                        className="button-secondary"
                        disabled={!results.data}
                        onClick={() =>
                          download(sessionId + "-facts.json", results.data)
                        }
                      >
                        下载原始事实
                      </button>
                      <button
                        className="button-secondary"
                        disabled={!organized.data}
                        onClick={() =>
                          download(
                            sessionId + "-organized.json",
                            organized.data,
                          )
                        }
                      >
                        下载组织结果
                      </button>
                    </div>
                    {run.run?.summary?.errors?.map((e, i) => (
                      <Notice tone="error" key={i}>
                        {e.clause_key} {e.object_id}：{e.error}
                      </Notice>
                    ))}
                  </section>
                )}
              </>
            )}
            {child === "tables" && (
              <>
                <ReadError error={organized.error} retry={organized.reload} />
                {table ? (
                  <OrganizedTable
                    table={table}
                    sessionId={sessionId}
                    facts={results.data?.facts}
                  />
                ) : organized.data ? (
                  <Empty title="此条款尚无结果" />
                ) : (
                  <Loading />
                )}
              </>
            )}
            {child === "facts" && (
              <>
                <ReadError error={results.error} retry={results.reload} />
                {fact ? (
                  <section className="section">
                    <div className="section-heading">
                      <h2>{fact.source_row_label || "原始事实"}</h2>
                      <button onClick={() => setEvidence(fact)}>
                        查看原文证据
                      </button>
                    </div>
                    <div className="source-value">
                      {"value_raw" in fact.value
                        ? fact.value.value_raw
                        : fact.value.statement_raw}
                    </div>
                    <dl className="detail-list">
                      {[
                        ...(Object.entries(fact.identity || {}).filter(
                          ([key]) => key !== "dimensions",
                        ) as [string, SourceFact_ReportedField][]),
                        ...Object.entries(fact.identity?.dimensions || {}),
                      ].map(([key, value]) => (
                        <div className="detail-pair" key={key}>
                          <dt>{identityNames[key] || key}</dt>
                          <dd>
                            {key === "reporting_period"
                              ? periodText(value.value)
                              : text(value.value)}{" "}
                            <Badge>{label(value.state)}</Badge>
                            {value.raw_text && (
                              <small>原文：{value.raw_text}</small>
                            )}
                          </dd>
                        </div>
                      ))}
                    </dl>
                    {Object.keys(fact.extensions || {}).length > 0 && (
                      <details>
                        <summary>补充属性</summary>
                        <pre className="json-view">
                          {JSON.stringify(fact.extensions, null, 2)}
                        </pre>
                      </details>
                    )}
                    <h3>条款映射</h3>
                    {results.data?.clause_mappings
                      .filter((m) => m.source_fact_id === fact.source_fact_id)
                      .map((m) => (
                        <p key={m.clause_key}>
                          <a
                            href={href(
                              "extractions",
                              sessionId,
                              "tables",
                              m.clause_key,
                            )}
                          >
                            {m.clause_key}
                          </a>{" "}
                          <Badge tone={tone(m.match)}>{label(m.match)}</Badge>{" "}
                          {m.reason}
                        </p>
                      ))}
                    {(fact.flags?.length || 0) > 0 && (
                      <details>
                        <summary>属性与比较说明 · {fact.flags?.length}</summary>
                        <ul>
                          {fact.flags?.map((f) => (
                            <li key={f}>{f}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </section>
                ) : results.data ? (
                  <Empty title="未找到这条事实" />
                ) : (
                  <Loading />
                )}
              </>
            )}
          </>
        )
      )}
      {confirmCancel && (
        <Modal
          title="取消这次提取"
          onClose={() => setConfirmCancel(false)}
          busy={cancelling}
          footer={
            <>
              <button
                className="button-secondary"
                disabled={cancelling}
                onClick={() => setConfirmCancel(false)}
              >
                继续执行
              </button>
              <button
                className="button-danger"
                disabled={cancelling}
                onClick={cancel}
              >
                {cancelling ? "正在请求…" : "确认取消"}
              </button>
            </>
          }
        >
          <p>停止待执行工作与当前工作进程。已生成的原值和证据仍可查看。</p>
          <ReadError error={error} />
        </Modal>
      )}
      {evidence && (
        <FactEvidence fact={evidence} onClose={() => setEvidence(undefined)} />
      )}
    </>
  );
}
