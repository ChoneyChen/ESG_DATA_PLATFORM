import { clauseReference } from "../format";
import { topicLabels, unitLabel } from "../format";
import { useLocation } from "../navigation";
import type { Metric } from "../types";
import { Icon, Notice } from "./Ui";
import { Pagination, usePage } from "./Pagination";
import { ProductLink } from "./ProductLink";
export default function MetricCatalog({ metrics }: { metrics: Metric[] }) {
  const [params, patch] = useLocation(),
    query = params.get("q") || "",
    topic = params.get("topic") || "";
  const filtered = metrics.filter(
    (metric) =>
      (!topic || topic === metric.topic) &&
      [metric.label_zh, metric.source_datapoint_id, metric.description]
        .join(" ")
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const paging = usePage(filtered.length, 24);
  const visible = filtered.slice(paging.start, paging.end);
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">METRIC EXPLORER</div>
        <h1>指标浏览</h1>
        <p>
          从指标含义出发理解企业披露。完整保留 ESRS
          原始编号，分别查看数值、方法和比较条件。
        </p>
      </div>
      <div className="toolbar">
        <label className="search-field">
          <Icon name="search" />
          <input
            aria-label="搜索主要指标"
            placeholder="搜索指标名称、原编号或定义"
            value={query}
            onChange={(event) =>
              patch({ q: event.target.value, page: null }, true)
            }
          />
        </label>
        <span className="muted">
          {filtered.length} / {metrics.length} 项指标定义
          {paging.pages > 1 && ` · 第 ${paging.page} / ${paging.pages} 页`}
        </span>
      </div>
      <div className="topic-tabs" role="group" aria-label="按主题筛选">
        <button
          className={!topic ? "active" : ""}
          onClick={() => patch({ topic: null, page: null })}
        >
          全部 <span>{metrics.length}</span>
        </button>
        {[...new Set(metrics.map((metric) => metric.topic))].map((code) => (
          <button
            key={code}
            className={topic === code ? "active" : ""}
            onClick={() => patch({ topic: code, page: null })}
          >
            {code} {topicLabels[code] || ""}
          </button>
        ))}
      </div>
      <div className="metric-grid">
        {visible.map((metric) => (
          <ProductLink
            view="metric"
            values={{ metric: metric.metric_id, q: "", topic: "" }}
            className="metric-card"
            key={metric.metric_id}
          >
            <div className="metric-card-top">
              <span className="metric-id mono">
                {clauseReference(metric)}
              </span>
              <span className="badge neutral">
                {topicLabels[metric.topic] || metric.topic}
              </span>
            </div>
            <h2>{metric.label_zh}</h2>
            <p className="metric-description">{metric.description}</p>
            <div className="metric-facets">
              <span>
                {metric.canonical_unit
                  ? unitLabel(metric.canonical_unit)
                  : "定性披露"}
              </span>
              <span>
                {["statement", "categorical", "boolean"].includes(
                  metric.value_type,
                )
                  ? "内容与来源"
                  : "数值与趋势"}
              </span>
            </div>
            <div className="metric-more">
              查看定义与数据 <Icon name="arrow" size={16} />
            </div>
          </ProductLink>
        ))}
      </div>
      <Pagination
        page={paging.page}
        pages={paging.pages}
        onChange={paging.setPage}
        label="指标分页"
      />
      {!filtered.length && (
        <Notice title="没有找到匹配的指标">
          调整主题或搜索条件，可以浏览完整主要条款清单。
        </Notice>
      )}
    </>
  );
}
