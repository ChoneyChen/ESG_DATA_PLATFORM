import { clauseReference } from "../format";
import type { Dataset, Entity, Metric } from "../types";
import { contextLabel, topicLabels, unitLabel } from "../format";
import { useLocation, navigate } from "../navigation";
import { useObservations } from "../hooks/useObservations";
import { buildTrendSeries } from "../chart-contract";
import { DataChart } from "./DataChart";
import { DemoBadge, Dimensions, Icon, Notice } from "./Ui";
import { ProductLink } from "./ProductLink";
import { DisplaySwitch, ObservationTable } from "./ObservationTable";

const topics = [
  {
    code: "E1",
    title: "能源与气候",
    text: "能源消费、可再生能源与温室气体排放",
    mark: "01",
  },
  {
    code: "E2",
    title: "污染与物质",
    text: "按污染物、排放介质和物质类型理解披露",
    mark: "02",
  },
  {
    code: "E3",
    title: "水资源",
    text: "取水、耗水及水资源循环利用",
    mark: "03",
  },
  {
    code: "S1",
    title: "人员与安全",
    text: "员工结构、培训、薪酬与职业安全",
    mark: "04",
  },
];
export default function Home({
  entities,
  metrics,
  dataset,
  currentDataset,
  onEvidence,
}: {
  entities: Entity[];
  metrics: Metric[];
  dataset: string;
  currentDataset?: Dataset;
  onEvidence: (id: string) => void;
}) {
  const [params, patch] = useLocation();
  const data = useObservations(dataset);
  const groups = buildTrendSeries(data.rows);
  const featured = groups[0];
  const display = params.get("display") === "table" ? "table" : "chart";
  return (
    <>
      <section className="site-hero">
        <div className="hero-copy">
          <div className="eyebrow">企业可持续发展数据</div>
          <h1>
            看清数据。
            <br />
            <span>理解企业。</span>
          </h1>
          <p>
            从企业报告出发，探索能源、排放、水与社会责任。每条数据保留口径与来源，让趋势和比较有据可依。
          </p>
          <form
            className="hero-search"
            onSubmit={(event) => {
              event.preventDefault();
              const query = String(
                new FormData(event.currentTarget).get("q") || "",
              ).trim();
              const metricMatch = metrics.some((metric) =>
                [metric.label_zh, metric.source_datapoint_id]
                  .join(" ")
                  .toLowerCase()
                  .includes(query.toLowerCase()),
              );
              const entityMatch = entities.some((entity) =>
                entity.name_zh.toLowerCase().includes(query.toLowerCase()),
              );
              navigate(metricMatch && !entityMatch ? "metrics" : "entities", {
                q: query,
                topic: "",
              });
            }}
          >
            <Icon name="search" />
            <input
              name="q"
              aria-label="搜索企业或指标"
              placeholder="搜索企业，或浏览指标"
            />
            <button type="submit">
              探索数据 <Icon name="arrow" size={17} />
            </button>
          </form>
          <div className="hero-links">
            <ProductLink view="metrics">
              浏览全部指标 <Icon name="arrow" size={16} />
            </ProductLink>
            <ProductLink view="methods">了解数据与方法</ProductLink>
          </div>
        </div>
        <div className="hero-visual" aria-hidden="true">
          <span className="visual-caption">来源 · 口径 · 时间</span>
          <div className="visual-ring ring-a" />
          <div className="visual-ring ring-b" />
          <div className="visual-ring ring-c" />
          <div className="visual-core">
            <span>ESG</span>
            <small>DATA EXPLORER</small>
          </div>
          <div className="visual-tag tag-climate">能源与气候</div>
          <div className="visual-tag tag-water">水资源</div>
          <div className="visual-tag tag-source">可追溯来源</div>
        </div>
      </section>
      <section className="site-stats" aria-label="数据范围">
        <div>
          <strong>{entities.length}</strong>
          <span>家已发布企业</span>
        </div>
        <div>
          <strong>{metrics.length}</strong>
          <span>项主要指标定义</span>
        </div>
        <div>
          <strong>{currentDataset?.observation_count ?? 0}</strong>
          <span>条已发布数据</span>
        </div>
        <div>
          <strong>{currentDataset?.created_at?.slice(0, 10) || "—"}</strong>
          <span>最近一次发布</span>
        </div>
      </section>
      <section className="home-section">
        <div className="section-heading">
          <div>
            <div className="eyebrow">从主题开始</div>
            <h2>探索企业可持续发展</h2>
          </div>
          <ProductLink className="text-button" view="metrics">
            全部主题 <Icon name="arrow" size={16} />
          </ProductLink>
        </div>
        <div className="topic-grid">
          {topics.map((topic) => (
            <ProductLink
              view="metrics"
              values={{ topic: topic.code }}
              className={"topic-card topic-" + topic.code}
              key={topic.code}
            >
              <span className="topic-number">{topic.mark}</span>
              <h3>{topic.title}</h3>
              <p>{topic.text}</p>
              <span>
                {metrics.filter((metric) => metric.topic === topic.code).length}{" "}
                项定义 <Icon name="arrow" size={17} />
              </span>
            </ProductLink>
          ))}
        </div>
      </section>
      {featured && (
        <section className="panel featured-chart">
          <div className="section-heading">
            <div>
              <div className="eyebrow">
                年度数据观察 ·{" "}
                {
                  topicLabels[
                    (featured.observation.source_datapoint_id?.slice(0, 2) || featured.observation.parent_package_id?.split(".").pop()?.slice(0,2).toUpperCase() || "—")
                  ]
                }
              </div>
              <h2>
                {featured.observation.entity_name} ·{" "}
                {featured.observation.label_zh}
              </h2>
              <p>同一披露口径下的年度变化。点击数据点，查看报告原文。</p>
              <p>
                {contextLabel(featured.observation.boundary_code)} ·{" "}
                {contextLabel(featured.observation.measurement_method)} ·{" "}
                {unitLabel(featured.observation.canonical_unit)}
              </p>
            </div>
            <DisplaySwitch
              value={display}
              onChange={(value) => patch({ display: value })}
            />
          </div>
          <Dimensions values={featured.observation.dimensions} />
          {display === "chart" ? (
            <DataChart
              rows={featured.rows.filter((row) => row !== null)}
              onEvidence={onEvidence}
            />
          ) : (
            <ObservationTable
              rows={featured.rows.filter((row) => row !== null)}
              onEvidence={onEvidence}
            />
          )}
          <div className="chart-footer">
            <span>
              {clauseReference(featured.observation)} · 数据版本 {dataset}
              {data.cursor ? " · 展示当前已读取数据" : ""}
            </span>
            <ProductLink
              view="entity"
              values={{ entity: featured.observation.entity_ref }}
            >
              查看企业 <Icon name="arrow" size={15} />
            </ProductLink>
          </div>
        </section>
      )}
      <section className="home-section">
        <div className="section-heading">
          <div>
            <div className="eyebrow">企业档案</div>
            <h2>从一家企业的披露开始</h2>
          </div>
          <ProductLink view="entities" className="text-button">
            企业目录 <Icon name="arrow" size={16} />
          </ProductLink>
        </div>
        <div className="entity-card-grid">
          {entities.slice(0, 3).map((entity) => (
            <ProductLink
              className="entity-card"
              view="entity"
              values={{ entity: entity.entity_ref }}
              key={entity.entity_ref}
            >
              <span className="entity-avatar">
                {entity.name_zh.slice(0, 1)}
              </span>
              <h3>
                {entity.name_zh} <DemoBadge entity={entity} />
              </h3>
              <p>
                {entity.industry_code || "行业信息未提供"} ·{" "}
                {entity.country_code || "地区信息未提供"}
              </p>
              <div>
                <span>{entity.coverage_count} 项已发布指标</span>
                <Icon name="arrow" />
              </div>
            </ProductLink>
          ))}
        </div>
        {!entities.length && (
          <Notice title="等待首批数据发布">
            这里将展示企业的正式发布数据。您可以先浏览指标定义、数据范围和比较方法。
          </Notice>
        )}
      </section>
      <section className="data-promise">
        <div>
          <div className="eyebrow">让每一次比较都有依据</div>
          <h2>先理解口径，再理解数值。</h2>
          <p>
            同名指标也可能覆盖不同主体、期间和边界。我们保留这些条件，清楚说明可比较的数据与暂不能比较的原因。
          </p>
          <ProductLink view="compare" className="button primary">
            开始同行比较 <Icon name="arrow" size={18} />
          </ProductLink>
        </div>
        <ol>
          <li>
            <span>01</span>
            <div>
              <h3>保留报告原意</h3>
              <p>取水与耗水、范围 1 与范围 2 分别记录；缺失不填零。</p>
            </div>
          </li>
          <li>
            <span>02</span>
            <div>
              <h3>独立呈现不同口径</h3>
              <p>单位、方法、边界和污染物各自分组，区间与上下限保留原义。</p>
            </div>
          </li>
          <li>
            <span>03</span>
            <div>
              <h3>回到原始来源</h3>
              <p>数据连接披露片段与报告页码，固定发布版本便于复现。</p>
            </div>
          </li>
        </ol>
      </section>
    </>
  );
}
