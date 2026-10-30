import { useState } from "react";
import type {
  Clause,
  ClauseCatalog,
  ClauseGroup,
} from "../../shared/contracts";
import { useResource } from "../../shared/resource";
import { segment } from "../../shared/api";
import {
  PageHeader,
  Field,
  Loading,
  ReadError,
  Badge,
  Tabs,
  Notice,
  Empty,
} from "../../shared/ui";
import { label, tone, text } from "../../shared/format";
import { href, navigate } from "../../app/routes";
import { ClauseEditor } from "./editor";
export function ClausesPage({
  groupId,
  clauseKey,
}: {
  groupId?: string;
  clauseKey?: string;
}) {
  const groups = useResource<{ groups: ClauseGroup[] }>(
      "extraction",
      "/api/clause-groups",
    ),
    catalog = useResource<ClauseCatalog>("extraction", "/api/clauses"),
    detail = useResource<Clause>(
      "extraction",
      clauseKey ? "/api/clauses/" + segment(clauseKey) : null,
    );
  const [query, setQuery] = useState(""),
    [kind, setKind] = useState(""),
    [tab, setTab] = useState("definition"),
    [edit, setEdit] = useState(false),
    [adding, setAdding] = useState(false);
  const group = groups.data?.groups.find((g) => g.group_id === groupId),
    clause = detail.data;
  const all = catalog.data?.clauses || [],
    rows = all.filter(
      (c) =>
        (!groupId || c.group_id === groupId) &&
        (!kind || c.data_kind === kind) &&
        (!query ||
          `${c.clause_key} ${c.title}`
            .toLowerCase()
            .includes(query.toLowerCase())),
    );
  const groupRows = (groups.data?.groups || []).filter(
    (g) =>
      !query ||
      `${g.group_id} ${g.title}`.toLowerCase().includes(query.toLowerCase()) ||
      all.some(
        (c) =>
          c.group_id === g.group_id &&
          `${c.clause_key} ${c.title}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
  );
  if (groupId && groups.data && !group)
    return (
      <>
        <PageHeader
          title="展示组不存在"
          back={{ href: href("clauses"), label: "条款" }}
        />
        <Empty title="请返回条款目录重新选择" />
      </>
    );
  if (clause && clause.group_id !== groupId)
    return (
      <>
        <PageHeader
          title="条款不属于这个展示组"
          back={{ href: href("clauses"), label: "条款" }}
        />
        <Empty
          title="打开条款所在展示组"
          action={
            <a
              className="button"
              href={href("clauses", clause.group_id, clause.clause_key)}
            >
              查看条款
            </a>
          }
        />
      </>
    );
  return (
    <>
      <PageHeader
        title={
          clauseKey
            ? clause?.labels.zh || clause?.labels.en || clauseKey
            : groupId
              ? group?.title || groupId
              : "条款"
        }
        back={
          clauseKey
            ? {
                href: href("clauses", groupId!),
                label: group?.title || groupId!,
              }
            : groupId
              ? { href: href("clauses"), label: "展示组" }
              : undefined
        }
        subtitle={
          clauseKey
            ? clauseKey
            : groupId
              ? `${rows.length} 条独立条款`
              : "按展示组浏览，按独立条款维护"
        }
        actions={
          <>
            {clauseKey && clause && (
              <button
                className="button-secondary"
                onClick={() => setEdit(true)}
              >
                编辑条款
              </button>
            )}
            <button onClick={() => setAdding(true)}>新增条款</button>
          </>
        }
      />
      <ReadError
        error={groups.error || catalog.error || detail.error}
        retry={() => {
          groups.reload();
          catalog.reload();
          detail.reload();
        }}
      />
      {!catalog.data || !groups.data ? (
        <Loading />
      ) : !clauseKey ? (
        <>
          <div className="filters">
            <Field label={groupId ? "搜索条款" : "搜索展示组或条款"}>
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="名称或编号"
              />
            </Field>
            {groupId && (
              <Field label="数据种类">
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  <option value="">全部</option>
                  <option value="quantitative">定量</option>
                  <option value="qualitative">定性</option>
                </select>
              </Field>
            )}
          </div>
          {!groupId ? (
            <div className="group-grid">
              {groupRows.map((g) => (
                <a
                  key={g.group_id}
                  className="group-card"
                  href={href("clauses", g.group_id)}
                >
                  <small>{g.group_id}</small>
                  <h2>{g.title}</h2>
                  <div>
                    <span>
                      {all.filter((c) => c.group_id === g.group_id).length} 条款
                    </span>
                    <span aria-hidden="true">→</span>
                  </div>
                </a>
              ))}
            </div>
          ) : rows.length ? (
            <div className="object-list">
              {rows.map((c) => (
                <a
                  className="object-row"
                  key={c.clause_key}
                  href={href("clauses", groupId, c.clause_key)}
                >
                  <div>
                    <strong>{c.title}</strong>
                    <small>
                      {c.clause_key} · {label(c.data_kind)}
                    </small>
                  </div>
                  <Badge tone={tone(c.readiness)}>{label(c.readiness)}</Badge>
                  <span aria-hidden="true">→</span>
                </a>
              ))}
            </div>
          ) : (
            <Empty title="没有符合筛选的条款" />
          )}
        </>
      ) : !clause ? (
        <Loading />
      ) : (
        <>
          <div className="run-heading">
            <Badge>{label(clause.data_kind)}</Badge>
            <Badge tone={tone(clause.readiness)}>
              {label(clause.readiness)}
            </Badge>
            <Badge>{label(clause.organization.curation || "template")}</Badge>
            {clause.value.canonical_unit && (
              <span>{clause.value.canonical_unit}</span>
            )}
          </div>
          {clause.blocking_reasons?.length ? (
            <Notice>{clause.blocking_reasons.join("；")}</Notice>
          ) : null}
          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "definition", label: "定义与范围" },
              { id: "reading", label: "检索与阅读" },
              { id: "organization", label: "输出字段" },
              { id: "evidence", label: "证据要求" },
            ]}
          />
          {tab === "definition" && (
            <section className="section">
              <p className="lead">{clause.definition}</p>
              <h2>排除</h2>
              <ul>
                {clause.exclusions.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
              <h2>输出范围</h2>
              <div className="object-list">
                {clause.output_scopes.map((s) => (
                  <div className="object-row" key={s.scope_id}>
                    <div>
                      <strong>{s.label_zh}</strong>
                      <small>
                        {Object.entries(s)
                          .filter(
                            ([k, v]) =>
                              ![
                                "scope_id",
                                "label_zh",
                                "required",
                                "axes",
                              ].includes(k) && v != null,
                          )
                          .map(([k, v]) => `${k}: ${text(v)}`)
                          .join(" · ")}
                      </small>
                    </div>
                    {s.required && <Badge>必需</Badge>}
                  </div>
                ))}
              </div>
            </section>
          )}
          {tab === "reading" && (
            <section className="section">
              <h2>检索关键词</h2>
              <div className="chips">
                {clause.retrieval.positive_terms.map((s) => (
                  <span key={s}>{s}</span>
                ))}
              </div>
              {!!clause.retrieval.negative_terms?.length && (
                <>
                  <h3>排除词</h3>
                  <div className="chips">
                    {clause.retrieval.negative_terms.map((s) => (
                      <span key={s}>{s}</span>
                    ))}
                  </div>
                </>
              )}
              <h2>阅读指令</h2>
              <ol className="instruction-list">
                {clause.reading.instructions.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
              <details>
                <summary>
                  规则与词典 · {clause.reading.rules?.length || 0} 条规则
                </summary>
                <pre className="json-view">
                  {JSON.stringify(
                    {
                      operator: clause.reading.operator,
                      rules: clause.reading.rules,
                      vocabulary: clause.reading.vocabulary,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </section>
          )}
          {tab === "organization" && (
            <section className="section">
              <div className="section-heading">
                <h2>{clause.organization.title}</h2>
                <Badge>
                  {clause.organization.presentation?.view || "table"}
                </Badge>
              </div>
              {clause.organization.description && (
                <p>{clause.organization.description}</p>
              )}
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>来源</th>
                      <th>确认要求</th>
                      <th>词典</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clause.organization.fields.map((f) => (
                      <tr key={f.key}>
                        <td>
                          <strong>{f.label}</strong>
                          <small>{f.key}</small>
                        </td>
                        <td>
                          {f.binding.source} · {f.binding.key}
                        </td>
                        <td>{f.required_for_confirmation ? "必需" : "可选"}</td>
                        <td>
                          {f.vocabulary?.length ? (
                            <details>
                              <summary>{f.vocabulary.length} 项</summary>
                              {f.vocabulary.map((v) => (
                                <p key={v.code}>
                                  {v.label}{" "}
                                  <small>
                                    {[v.code, ...(v.aliases || [])].join(" · ")}
                                  </small>
                                </p>
                              ))}
                            </details>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {tab === "evidence" && (
            <section className="section">
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>字段</th>
                      <th>证据层级</th>
                      <th>要求</th>
                      <th>说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clause.evidence.map((e, i) => (
                      <tr key={i}>
                        <td>{e.field}</td>
                        <td>{e.level}</td>
                        <td>{e.required ? "必需" : "可选"}</td>
                        <td>{e.note || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
      {(adding || edit) && groups.data && (
        <ClauseEditor
          clause={edit ? clause : undefined}
          groupId={groupId}
          groups={groups.data.groups}
          onClose={() => {
            setAdding(false);
            setEdit(false);
          }}
          onSaved={(c) => {
            detail.reload();
            catalog.reload();
            groups.reload();
            navigate(
              "/clauses/" + segment(c.group_id) + "/" + segment(c.clause_key),
            );
          }}
        />
      )}
    </>
  );
}
