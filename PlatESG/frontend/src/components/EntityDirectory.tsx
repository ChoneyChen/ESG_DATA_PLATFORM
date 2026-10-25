import { useLocation } from "../navigation";
import type { Entity } from "../types";
import { DemoBadge, Icon, Kind, Notice } from "./Ui";
import { Pagination, usePage } from "./Pagination";
import { ProductLink } from "./ProductLink";
export default function EntityDirectory({
  entities,
  metricCount,
}: {
  entities: Entity[];
  metricCount: number;
}) {
  const [params, patch] = useLocation(),
    query = params.get("q") || "",
    industry = params.get("industry") || "",
    kind = params.get("kind") || "";
  const display =
    params.get("directory_display") === "table" ? "table" : "cards";
  const filtered = entities.filter(
    (entity) =>
      (!industry || entity.industry_code === industry) &&
      (!kind || entity.entity_kind === kind) &&
      entity.name_zh.toLowerCase().includes(query.toLowerCase()),
  );
  const paging = usePage(filtered.length, 12);
  const visible = filtered.slice(paging.start, paging.end);
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">COMPANY EXPLORER</div>
        <h1>企业目录</h1>
        <p>查看企业的已发布指标、年度变化与报告来源。</p>
      </div>
      <div className="toolbar">
        <label className="search-field">
          <Icon name="search" />
          <input
            aria-label="搜索企业"
            placeholder="搜索企业名称"
            value={query}
            onChange={(event) =>
              patch({ q: event.target.value, page: null }, true)
            }
          />
        </label>
        <select
          className="select"
          aria-label="筛选行业"
          value={industry}
          onChange={(event) =>
            patch({ industry: event.target.value, page: null })
          }
        >
          <option value="">全部行业</option>
          {[
            ...new Set(
              entities.map((entity) => entity.industry_code).filter(Boolean),
            ),
          ].map((value) => (
            <option key={value} value={value!}>
              {value}
            </option>
          ))}
        </select>
        <select
          className="select"
          aria-label="筛选主体类型"
          value={kind}
          onChange={(event) => patch({ kind: event.target.value, page: null })}
        >
          <option value="">全部主体类型</option>
          {[...new Set(entities.map((entity) => entity.entity_kind))].map(
            (value) => (
              <option key={value} value={value}>
                <Kind value={value} />
              </option>
            ),
          )}
        </select>
        <div className="display-switch" role="group" aria-label="企业展示方式">
          <button
            aria-pressed={display === "cards"}
            onClick={() => patch({ directory_display: "cards" })}
          >
            卡片
          </button>
          <button
            aria-pressed={display === "table"}
            onClick={() => patch({ directory_display: "table" })}
          >
            列表
          </button>
        </div>
      </div>
      <p className="result-caption">
        {filtered.length} 家企业 · 当前发布
        {paging.pages > 1 && ` · 第 ${paging.page} / ${paging.pages} 页`}
      </p>
      {display === "cards" ? (
        <div className="entity-card-grid">
          {visible.map((entity) => (
            <ProductLink
              view="entity"
              values={{ entity: entity.entity_ref }}
              className="entity-card"
              key={entity.entity_ref}
            >
              <span className="entity-avatar">
                {entity.name_zh.slice(0, 1)}
              </span>
              <span className="entity-card-body">
                <h2>
                  {entity.name_zh} <DemoBadge entity={entity} />
                </h2>
                <p>
                  <Kind value={entity.entity_kind} /> ·{" "}
                  {entity.country_code || "地区未提供"}
                </p>
                <p>{entity.industry_code || "行业信息未提供"}</p>
                <span>{entity.coverage_count} 项已发布指标</span>
              </span>
              <Icon name="arrow" />
            </ProductLink>
          ))}
        </div>
      ) : (
        <section className="panel">
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>企业</th>
                  <th>主体类型</th>
                  <th>行业 / 地区</th>
                  <th>已发布指标</th>
                  <th>企业档案</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((entity) => (
                  <tr key={entity.entity_ref}>
                    <td>
                      <strong>{entity.name_zh}</strong>{" "}
                      <DemoBadge entity={entity} />
                    </td>
                    <td>
                      <Kind value={entity.entity_kind} />
                    </td>
                    <td>
                      {entity.industry_code || "未提供"}
                      <span className="table-subtitle">
                        {entity.country_code || "地区未提供"}
                      </span>
                    </td>
                    <td>
                      {entity.coverage_count}
                      <span className="muted"> / {metricCount} 项</span>
                    </td>
                    <td>
                      <ProductLink
                        view="entity"
                        values={{ entity: entity.entity_ref }}
                        className="text-button"
                      >
                        查看档案 <Icon name="arrow" size={16} />
                      </ProductLink>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <Pagination
        page={paging.page}
        pages={paging.pages}
        onChange={paging.setPage}
        label="企业分页"
      />
      {!filtered.length && (
        <Notice title="没有找到匹配企业">
          调整搜索与筛选条件，浏览其他企业。
        </Notice>
      )}
    </>
  );
}
