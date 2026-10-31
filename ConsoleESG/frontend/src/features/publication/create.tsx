import { useEffect, useState } from "react";
import type {
  ProjectionPage,
  DatasetPreview,
  DatasetRequest,
  DatasetVersion,
  ObservationPage,
  SourceSelectionPolicy,
} from "@esg/database-contracts";
import { useResource, invalidate } from "../../shared/resource";
import { request, write } from "../../shared/api";
import {
  PageHeader,
  ReadError,
  Loading,
  Field,
  Badge,
  Empty,
} from "../../shared/ui";
import { href, navigate } from "../../app/routes";
import { ObservationGrid, Pager } from "./observations";

type Relation = NonNullable<SourceSelectionPolicy["relations"]>[number];
export function PreparePublicationPage({
  initialProjection,
}: {
  initialProjection?: string;
}) {
  const [history, setHistory] = useState<(string | null)[]>([null]),
    [selected, setSelected] = useState<string[]>(
      initialProjection ? [initialProjection] : [],
    ),
    [title, setTitle] = useState(""),
    [mode, setMode] =
      useState<NonNullable<SourceSelectionPolicy["mode"]>>("all_sources"),
    [relations, setRelations] = useState<Relation[]>(["match"]),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error>(),
    [preview, setPreview] = useState<DatasetPreview>(),
    [frozen, setFrozen] = useState<DatasetRequest>(),
    [rowHistory, setRowHistory] = useState<(string | null)[]>([null]),
    [rows, setRows] = useState<ObservationPage>(),
    [rowError, setRowError] = useState<Error>();
  const params = new URLSearchParams({ limit: "50" });
  if (history.at(-1)) params.set("cursor", history.at(-1)!);
  const projections = useResource<ProjectionPage>(
    "database",
    "/v1/projections?" + params,
  );
  useEffect(() => {
    if (!title && initialProjection) {
      const item = projections.data?.items.find(
        (item) => item.projection_id === initialProjection,
      );
      if (item)
        setTitle(
          (item.source.title || item.source.report_company || "报告提取") +
            "数据",
        );
    }
  }, [projections.data, initialProjection]);
  useEffect(() => {
    if (!preview || !frozen) return;
    const controller = new AbortController();
    setRows(undefined);
    setRowError(undefined);
    const params = new URLSearchParams({ limit: "100" });
    if (rowHistory.at(-1)) params.set("cursor", rowHistory.at(-1)!);
    request<ObservationPage>(
      "database",
      "/v1/datasets/preview/observations?" + params,
      {
        method: "POST",
        body: JSON.stringify({
          ...frozen,
          preview_digest: preview.preview_digest,
        }),
        signal: controller.signal,
      },
    )
      .then(setRows)
      .catch((e) => {
        if (!controller.signal.aborted) setRowError(e as Error);
      });
    return () => controller.abort();
  }, [preview, frozen, rowHistory]);
  async function prepare() {
    setBusy(true);
    setError(undefined);
    const body: DatasetRequest = {
      title,
      projection_ids: selected,
      policy: { mode, relations },
    };
    try {
      const result = await write<DatasetPreview>(
        "database",
        "/v1/datasets/preview",
        body,
      );
      setFrozen(body);
      setPreview(result);
      setRowHistory([null]);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  async function publish() {
    if (!preview || !frozen) return;
    setBusy(true);
    setError(undefined);
    try {
      const dataset = await write<DatasetVersion>("database", "/v1/datasets", {
        ...frozen,
        preview_digest: preview.preview_digest,
        reason,
      });
      invalidate("database", "/v1/datasets");
      navigate("/publication/" + dataset.dataset_version_id);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader
        title={preview ? "核对发布数据" : "准备发布"}
        back={{ href: href("publication"), label: "发布" }}
      />
      <ReadError
        error={error || (!preview ? projections.error : undefined)}
        retry={!preview ? projections.reload : undefined}
      />
      {!preview ? (
        <section className="section">
          <Field label="数据集名称">
            <input
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <div className="filters">
            <Field label="来源选择">
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as typeof mode)}
              >
                <option value="all_sources">保留全部来源</option>
                <option value="latest_report">相同口径取最新披露</option>
                <option value="prefer_explicit_restated">优先明确重述</option>
              </select>
            </Field>
          </div>
          <fieldset className="relation-choices">
            <legend>包含的目标关系</legend>
            {(
              [
                ["match", "本目标"],
                ["uncertain", "待确认"],
                ["different", "其他目标"],
              ] as const
            ).map(([key, text]) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={relations.includes(key)}
                  onChange={() =>
                    setRelations(
                      relations.includes(key)
                        ? relations.filter((item) => item !== key)
                        : [...relations, key],
                    )
                  }
                />
                {text}
              </label>
            ))}
          </fieldset>
          <h2>选择已投影数据</h2>
          {!projections.data && !projections.error ? (
            <Loading />
          ) : !projections.data?.items.length ? (
            <Empty title="暂无可选数据" />
          ) : (
            <>
              <div className="choice-list">
                {projections.data.items.map((item) => (
                  <label className="choice-row" key={item.projection_id}>
                    <input
                      type="checkbox"
                      checked={selected.includes(item.projection_id)}
                      onChange={() =>
                        setSelected(
                          selected.includes(item.projection_id)
                            ? selected.filter((id) => id !== item.projection_id)
                            : [...selected, item.projection_id],
                        )
                      }
                    />
                    <span>
                      <strong>
                        {item.source.title ||
                          item.source.report_company ||
                          "报告提取数据"}
                      </strong>
                      <small>
                        {item.observations} 条 ·{" "}
                        {Object.keys(item.periods).join("、")}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
              <Pager
                total={projections.data.total}
                returned={projections.data.returned}
                next={projections.data.next_cursor}
                history={history}
                onPage={setHistory}
              />
            </>
          )}
          <div className="form-actions">
            <span>已选择 {selected.length} 份</span>
            <button
              disabled={
                busy || !selected.length || !relations.length || !title.trim()
              }
              onClick={prepare}
            >
              {busy ? "生成预览…" : "核对数据"}
            </button>
          </div>
        </section>
      ) : (
        <>
          <div className="run-heading">
            <strong>{preview.title}</strong>
            <Badge>{preview.selected} 条待发布</Badge>
            <span>源集合共 {preview.total} 条</span>
            {Object.entries(preview.periods).map(([key, count]) => (
              <Badge key={key}>
                {key === "unknown" ? "期间未知" : key} · {count}
              </Badge>
            ))}
          </div>
          <ReadError error={rowError} />
          {!rows && !rowError ? (
            <Loading />
          ) : rows ? (
            <>
              <ObservationGrid rows={rows.items} />
              <Pager
                total={rows.total}
                returned={rows.returned}
                next={rows.next_cursor}
                history={rowHistory}
                onPage={setRowHistory}
              />
            </>
          ) : null}
          <section className="section">
            <Field label="发布说明">
              <textarea
                value={reason}
                maxLength={1000}
                onChange={(e) => setReason(e.target.value)}
              />
            </Field>
            <div className="form-actions">
              <button
                className="button-secondary"
                disabled={busy}
                onClick={() => {
                  setPreview(undefined);
                  setFrozen(undefined);
                  setError(undefined);
                }}
              >
                返回调整
              </button>
              <button
                disabled={
                  busy ||
                  !preview.selected ||
                  !rows ||
                  !!rowError ||
                  !reason.trim()
                }
                onClick={publish}
              >
                {busy ? "发布中…" : "发布此快照"}
              </button>
            </div>
          </section>
        </>
      )}
    </>
  );
}
