import { apiUrl } from "../api";
import { useLocation } from "../navigation";
import { useResource } from "../hooks/useResource";
import { useObservations } from "../hooks/useObservations";
import type { Entity, PublicSource, VersionedItems } from "../types";
import { safePublicUrl } from "../public-contract";
import { ErrorNotice, Icon, Loading, Notice } from "./Ui";
import { ObservationTable } from "./ObservationTable";
export default function Sources({
  dataset,
  entities,
  onEvidence,
}: {
  dataset: string;
  entities: Entity[];
  onEvidence: (id: string) => void;
}) {
  const [params, patch] = useLocation(),
    query = params.get("source_q") || "",
    entity = params.get("source_entity") || "";
  const sources = useResource<VersionedItems<PublicSource>>(
    dataset ? apiUrl("/v1/sources", { dataset_version: dataset }) : null,
  );
  const data = useObservations(dataset, entity ? { entity_ref: entity } : {});
  const filtered = (sources.data?.items || []).filter(
    (source) =>
      (!entity || source.entity_refs.includes(entity)) &&
      [source.report_title, source.publisher]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">SOURCES & PUBLICATIONS</div>
        <h1>报告来源</h1>
        <p>查看本数据版本引用的企业报告，沿着每条数据回到披露片段与页码。</p>
      </div>
      <div className="toolbar">
        <label className="search-field">
          <Icon name="search" />
          <input
            aria-label="搜索来源报告"
            placeholder="搜索报告标题或发布者"
            value={query}
            onChange={(event) => patch({ source_q: event.target.value }, true)}
          />
        </label>
        <select
          className="select"
          aria-label="筛选来源企业"
          value={entity}
          onChange={(event) => patch({ source_entity: event.target.value })}
        >
          <option value="">全部企业</option>
          {entities.map((item) => (
            <option key={item.entity_ref} value={item.entity_ref}>
              {item.name_zh}
            </option>
          ))}
        </select>
      </div>
      {sources.loading ? (
        <Loading />
      ) : sources.error ? (
        <ErrorNotice error={sources.error} retry={sources.reload} />
      ) : filtered.length ? (
        <div className="source-grid">
          {filtered.map((source) => (
            <article className="source-card" key={source.source_id}>
              <span className="source-icon">
                <Icon name="file" size={25} />
              </span>
              <div>
                <span className="eyebrow">
                  {source.report_year || "报告"}
                  {source.is_demo && " · 合成演示"}
                </span>
                <h2>{source.report_title}</h2>
                <p>
                  {source.publisher ||
                    source.entity_refs
                      .map(
                        (ref) =>
                          entities.find((item) => item.entity_ref === ref)
                            ?.name_zh,
                      )
                      .filter(Boolean)
                      .join("、") ||
                    "来源发布者未提供"}
                </p>
                <span className="muted">
                  {source.observation_count} 条已发布数据引用
                </span>
                {safePublicUrl(source.public_url) && (
                  <a
                    href={safePublicUrl(source.public_url)!}
                    target="_blank"
                    rel="noreferrer"
                    className="text-button"
                  >
                    查看原始报告 <Icon name="arrow" size={16} />
                  </a>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Notice title="此版本没有匹配的来源报告">
          来源目录仅收录已发布数据引用的报告。调整筛选或选择其他发布版本。
        </Notice>
      )}
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>从数据查看原文</h2>
            <p>点击“原文”查看披露片段、报告页码与计量口径。</p>
          </div>
        </div>
        {data.loading ? (
          <Loading />
        ) : data.error ? (
          <ErrorNotice error={data.error} retry={data.reload} />
        ) : data.rows.length ? (
          <ObservationTable
            rows={data.rows}
            onEvidence={onEvidence}
            showEntity
          />
        ) : (
          <Notice title="此版本暂无可查看的数据">
            数据发布后，来源与引用将在此展示。
          </Notice>
        )}
        {data.cursor && (
          <div className="load-more">
            <button
              className="button secondary"
              onClick={data.loadMore}
              disabled={data.loadingMore}
            >
              {data.loadingMore ? "正在读取…" : "加载更多数据"}
            </button>
          </div>
        )}
        {data.moreError && (
          <p className="inline-error" role="alert">
            {data.moreError}
          </p>
        )}
      </section>
    </>
  );
}
