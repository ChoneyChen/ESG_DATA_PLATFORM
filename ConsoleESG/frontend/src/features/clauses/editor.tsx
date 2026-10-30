import { useState } from "react";
import type { Clause, ClauseGroup } from "../../shared/contracts";
import { segment, write } from "../../shared/api";
import { invalidate } from "../../shared/resource";
import { Field, Modal, ReadError, Tabs, Notice } from "../../shared/ui";
const lines = (value: string) => value.split("\n");
export function ClauseEditor({
  clause,
  groupId,
  groups,
  onClose,
  onSaved,
}: {
  clause?: Clause;
  groupId?: string;
  groups: ClauseGroup[];
  onClose: () => void;
  onSaved: (c: Clause) => void;
}) {
  const [draft, setDraft] = useState<Clause | undefined>(clause),
    [key, setKey] = useState(""),
    [group, setGroup] = useState(groupId || groups[0]?.group_id || ""),
    [title, setTitle] = useState(""),
    [kind, setKind] = useState<"quantitative" | "qualitative">("quantitative");
  const [tab, setTab] = useState("definition"),
    [json, setJson] = useState(JSON.stringify(clause, null, 2) || ""),
    [organization, setOrganization] = useState(
      JSON.stringify(clause?.organization, null, 2) || "",
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error>(),
    [valid, setValid] = useState(false),
    [discard, setDiscard] = useState(false),
    [dirty, setDirty] = useState(false);
  function update(patch: Partial<Clause>) {
    setDraft((c) => (c ? { ...c, ...patch } : c));
    setValid(false);
    setDirty(true);
  }
  async function create() {
    setBusy(true);
    setError(undefined);
    try {
      const c = await write<Clause>("extraction", "/api/clauses/template", {
        clause_key: key,
        group_id: group,
        data_kind: kind,
        title,
      });
      setDraft(c);
      setJson(JSON.stringify(c, null, 2));
      setOrganization(JSON.stringify(c.organization, null, 2));
      setDirty(true);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  function switchTab(next: string) {
    try {
      let c = draft!;
      if (tab === "json") c = JSON.parse(json) as Clause;
      else if (tab === "organization")
        c = { ...c, organization: JSON.parse(organization) };
      setDraft(c);
      setJson(JSON.stringify(c, null, 2));
      setOrganization(JSON.stringify(c.organization, null, 2));
      setTab(next);
      setError(undefined);
    } catch {
      setError(new Error("JSON 格式无效，请先修正当前内容。"));
    }
  }
  async function validate(save = false) {
    setBusy(true);
    setError(undefined);
    try {
      const body =
        tab === "json"
          ? (JSON.parse(json) as Clause)
          : tab === "organization"
            ? { ...draft!, organization: JSON.parse(organization) }
            : draft!;
      if (tab !== "json") {
        body.exclusions = body.exclusions.map((s) => s.trim()).filter(Boolean);
        body.reading = {
          ...body.reading,
          instructions: body.reading.instructions
            .map((s) => s.trim())
            .filter(Boolean),
        };
        body.retrieval = {
          ...body.retrieval,
          positive_terms: body.retrieval.positive_terms
            .map((s) => s.trim())
            .filter(Boolean),
          negative_terms: body.retrieval.negative_terms
            ?.map((s) => s.trim())
            .filter(Boolean),
        };
      }
      const result = await write<{ clause: Clause; valid: boolean }>(
        "extraction",
        "/api/clauses/validate",
        body,
      );
      setDraft(result.clause);
      setJson(JSON.stringify(result.clause, null, 2));
      setOrganization(JSON.stringify(result.clause.organization, null, 2));
      setValid(true);
      if (save) {
        const saved = await write<Clause>(
          "extraction",
          "/api/clauses/" + segment(result.clause.clause_key),
          {
            clause: result.clause,
            expected_digest: clause?.clause_digest || null,
          },
          "PUT",
        );
        invalidate("extraction", "/api/clause");
        onSaved(saved);
        onClose();
      }
    } catch (e) {
      setValid(false);
      setError(
        e instanceof SyntaxError ? new Error("JSON 格式无效。") : (e as Error),
      );
    } finally {
      setBusy(false);
    }
  }
  const close = () => (dirty ? setDiscard(true) : onClose());
  return (
    <>
      <Modal
        title={clause ? "编辑条款" : "新增独立条款"}
        onClose={close}
        wide
        busy={busy}
        footer={
          <>
            <button
              className="button-secondary"
              disabled={busy}
              onClick={close}
            >
              取消
            </button>
            {draft && (
              <>
                <button
                  className="button-secondary"
                  disabled={busy}
                  onClick={() => validate()}
                >
                  校验合同
                </button>
                <button disabled={busy} onClick={() => validate(true)}>
                  {busy ? "处理中…" : "校验并保存"}
                </button>
              </>
            )}
          </>
        }
      >
        <ReadError error={error} />
        {valid && (
          <Notice tone="success">
            合同结构通过。科学准确性仍须真实报告验证。
          </Notice>
        )}
        {!draft ? (
          <form
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              void create();
            }}
          >
            <Field label="条款编号">
              <input
                required
                pattern="[A-Za-z0-9][A-Za-z0-9_.-]*"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </Field>
            <Field label="核心名称">
              <input
                required
                maxLength={300}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            <Field label="展示组">
              <select value={group} onChange={(e) => setGroup(e.target.value)}>
                {groups.map((g) => (
                  <option value={g.group_id} key={g.group_id}>
                    {g.title}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="数据种类">
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as typeof kind)}
              >
                <option value="quantitative">定量</option>
                <option value="qualitative">定性</option>
              </select>
            </Field>
            <button disabled={busy}>建立条款定义</button>
          </form>
        ) : (
          <>
            <div className="editor-identity">
              <strong>{draft.clause_key}</strong>
              <span>
                {draft.data_kind === "quantitative" ? "定量" : "定性"}
              </span>
            </div>
            <Tabs
              value={tab}
              onChange={switchTab}
              tabs={[
                { id: "definition", label: "定义" },
                { id: "reading", label: "检索与阅读" },
                { id: "organization", label: "输出组织" },
                { id: "json", label: "完整合同" },
              ]}
            />
            {tab === "definition" && (
              <div className="form-stack">
                <Field label="核心名称">
                  <input
                    value={draft.labels.zh || draft.labels.en || ""}
                    onChange={(e) =>
                      update({
                        labels: { ...draft.labels, zh: e.target.value },
                      })
                    }
                  />
                </Field>
                <Field label="定义">
                  <textarea
                    rows={4}
                    value={draft.definition}
                    onChange={(e) => update({ definition: e.target.value })}
                  />
                </Field>
                <Field label="排除项 · 每行一项">
                  <textarea
                    rows={3}
                    value={draft.exclusions.join("\n")}
                    onChange={(e) =>
                      update({ exclusions: lines(e.target.value) })
                    }
                  />
                </Field>
                {draft.data_kind === "quantitative" && (
                  <div className="form-grid">
                    <Field label="标准单位">
                      <input
                        value={draft.value.canonical_unit || ""}
                        onChange={(e) =>
                          update({
                            value: {
                              ...draft.value,
                              canonical_unit: e.target.value || null,
                            },
                          })
                        }
                      />
                    </Field>
                    <Field label="单位维度">
                      <input
                        value={draft.value.unit_dimension || ""}
                        onChange={(e) =>
                          update({
                            value: {
                              ...draft.value,
                              unit_dimension: e.target.value || null,
                            },
                          })
                        }
                      />
                    </Field>
                  </div>
                )}
                <Field label="可执行状态">
                  <select
                    value={draft.readiness}
                    onChange={(e) =>
                      update({
                        readiness: e.target.value as Clause["readiness"],
                        blocking_reasons:
                          e.target.value === "ready" ? [] : ["条款待校核"],
                      })
                    }
                  >
                    <option value="blocked">待完善，禁止提取</option>
                    <option value="ready">允许提取</option>
                  </select>
                </Field>
                {draft.readiness === "blocked" && (
                  <Field label="未决项 · 每行一项">
                    <textarea
                      value={draft.blocking_reasons?.join("\n") || ""}
                      onChange={(e) =>
                        update({ blocking_reasons: lines(e.target.value) })
                      }
                    />
                  </Field>
                )}
              </div>
            )}
            {tab === "reading" && (
              <div className="form-stack">
                <Field label="检索关键词 · 每行一项">
                  <textarea
                    rows={5}
                    value={draft.retrieval.positive_terms.join("\n")}
                    onChange={(e) =>
                      update({
                        retrieval: {
                          ...draft.retrieval,
                          positive_terms: lines(e.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="排除关键词 · 每行一项">
                  <textarea
                    rows={3}
                    value={draft.retrieval.negative_terms?.join("\n") || ""}
                    onChange={(e) =>
                      update({
                        retrieval: {
                          ...draft.retrieval,
                          negative_terms: lines(e.target.value),
                        },
                      })
                    }
                  />
                </Field>
                <Field label="阅读指令 · 每行一项">
                  <textarea
                    rows={6}
                    value={draft.reading.instructions.join("\n")}
                    onChange={(e) =>
                      update({
                        reading: {
                          ...draft.reading,
                          instructions: lines(e.target.value),
                        },
                      })
                    }
                  />
                </Field>
              </div>
            )}
            {tab === "organization" && (
              <Field label="字段、词典与展示合同">
                <textarea
                  className="code-editor"
                  spellCheck={false}
                  rows={22}
                  value={organization}
                  onChange={(e) => {
                    setOrganization(e.target.value);
                    setValid(false);
                    setDirty(true);
                  }}
                />
              </Field>
            )}
            {tab === "json" && (
              <Field label="独立条款完整合同">
                <textarea
                  className="code-editor"
                  spellCheck={false}
                  rows={24}
                  value={json}
                  onChange={(e) => {
                    setJson(e.target.value);
                    setValid(false);
                    setDirty(true);
                  }}
                />
              </Field>
            )}
          </>
        )}
      </Modal>
      {discard && (
        <Modal
          title="放弃未保存的修改"
          onClose={() => setDiscard(false)}
          footer={
            <>
              <button
                className="button-secondary"
                onClick={() => setDiscard(false)}
              >
                继续编辑
              </button>
              <button className="button-danger" onClick={onClose}>
                放弃修改
              </button>
            </>
          }
        >
          <p>本次编辑尚未保存。</p>
        </Modal>
      )}
    </>
  );
}
