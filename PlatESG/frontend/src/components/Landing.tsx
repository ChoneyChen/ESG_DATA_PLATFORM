import { useMemo } from "react";
import type { Dataset, Entity, Metric } from "../types";
import { navigate, useLocation } from "../navigation";
import { topicLabels } from "../format";
import { DemoBadge, Icon, Notice } from "./Ui";
import { ProductLink } from "./ProductLink";

const topics = [
  {
    code: "E1",
    title: "能源与气候",
    text: "能源消费、可再生能源与范围一、范围二排放",
  },
  { code: "E2", title: "污染与物质", text: "大气、水体污染物与废弃物" },
  { code: "E3", title: "水资源", text: "取水、耗水与循环利用" },
  { code: "S1", title: "人员与安全", text: "员工结构、培训、薪酬与职业安全" },
  { code: "S2", title: "价值链", text: "供应商评估与劳动实践" },
  { code: "G1", title: "治理与合规", text: "商业行为、腐败与合规事件" },
];

const steps = [
  {
    mark: "01",
    title: "报告提取",
    text: "从公开报告定位表格与正文，保留原文与页码。",
  },
  {
    mark: "02",
    title: "复核与口径",
    text: "逐条核对期间、边界、方法与单位，不猜测缺失信息。",
  },
  {
    mark: "03",
    title: "固定版本发布",
    text: "以不可变版本对外提供，链接与下载始终可复现。",
  },
];

const apiSample = `GET /api/v1/observations
  ?metric_id=metric-E1-6_07
  &dataset_version=12
  &limit=200`;

export default function Landing({
  entities,
  metrics,
  dataset,
  currentDataset,
  datasets,
  demo,
  error,
}: {
  entities: Entity[];
  metrics: Metric[];
  dataset: string;
  currentDataset?: Dataset;
  datasets: Dataset[];
  demo: boolean;
  error?: string;
}) {
  const [params, patch] = useLocation();
  const topicCounts = useMemo(
    () =>
      topics
        .map((topic) => ({
          ...topic,
          count: metrics.filter((metric) => metric.topic === topic.code).length,
        }))
        .filter((topic) => topic.count > 0),
    [metrics],
  );
  const maxTopic = Math.max(1, ...topicCounts.map((topic) => topic.count));
  const observationCount = currentDataset?.observation_count ?? 0;
  return (
    <div className="landing">
      <a href="#main-content" className="skip-link">
        跳转到主要内容
      </a>
      <header className="landing-header">
        <div className="landing-header-inner">
          <ProductLink view="overview" className="brand">
            <span className="brand-mark">
              <Icon name="layers" size={22} />
            </span>
            <span>
              Plat<span className="brand-green">ESG</span>
              <small>企业可持续发展数据</small>
            </span>
          </ProductLink>
          <nav className="site-nav" aria-label="主要导航">
            <ProductLink view="overview">概览</ProductLink>
            <ProductLink view="entities">企业</ProductLink>
            <ProductLink view="metrics">指标</ProductLink>
            <ProductLink view="compare">同行比较</ProductLink>
            <ProductLink view="sources">来源</ProductLink>
            <ProductLink view="api">API</ProductLink>
          </nav>
          <div className="header-actions">
            <label className="dataset-picker">
              <span className="sr-only">数据版本</span>
              <select
                aria-label="选择数据集版本"
                value={dataset}
                disabled={!datasets.length}
                onChange={(event) =>
                  patch({
                    dataset_version: event.target.value,
                    dataset: null,
                    page: null,
                  })
                }
              >
                {!datasets.length && <option value="">尚无发布版本</option>}
                {datasets.map((item) => (
                  <option
                    key={item.dataset_version}
                    value={String(item.dataset_version)}
                  >
                    {item.dataset_version}
                    {String(item.dataset_version) ===
                    String(datasets[0]?.dataset_version)
                      ? " · 最新"
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <ProductLink view="overview" className="button primary small">
              进入平台 <Icon name="arrow" size={16} />
            </ProductLink>
          </div>
        </div>
      </header>
      {demo && (
        <div className="demo-banner" role="note">
          演示预览 ·
          页面包含明确标注的合成演示数据，供体验展示与交互，不代表真实企业披露。
        </div>
      )}
      {error && (
        <div className="landing-error" role="alert">
          数据服务暂时不可用 · {error}
        </div>
      )}
      <main id="main-content" className="landing-main">
        <section className="landing-hero">
          <div className="hero-copy">
            <span className="eyebrow">ESG DATA PLATFORM</span>
            <h1>
              看清数据。
              <br />
              <span>理解企业。</span>
            </h1>
            <p className="hero-lead">
              企业 ESG
              披露的公开数据层。每条数值保留报告原文、计量口径与固定数据版本。
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
              <Icon name="search" size={18} />
              <input
                name="q"
                aria-label="搜索企业或指标"
                placeholder="搜索企业，或浏览指标"
              />
              <button type="submit" aria-label="搜索">
                <Icon name="arrow" size={18} />
              </button>
            </form>
            <div className="hero-links">
              <ProductLink view="overview" className="button primary">
                进入数据平台 <Icon name="arrow" size={17} />
              </ProductLink>
              <ProductLink view="metrics" className="button ghost">
                浏览全部指标
              </ProductLink>
            </div>
            <div className="hero-proof">
              <span>
                <Icon name="shield" size={15} /> 发布前逐条复核
              </span>
              <span>
                <Icon name="layers" size={15} /> 版本固定、链接可复现
              </span>
              <span>
                <Icon name="code" size={15} /> 开放只读 API
              </span>
            </div>
          </div>
          <aside className="hero-panel" aria-label="当前发布">
            <div className="hero-panel-head">
              <span>
                <span className="status-dot" />
                {currentDataset ? "当前发布版本" : "尚无已发布数据"}
              </span>
              <strong className="mono">
                {currentDataset ? "v" + currentDataset.dataset_version : "—"}
              </strong>
            </div>
            <div className="hero-stats">
              <div>
                <strong>{entities.length}</strong>
                <span>已发布企业</span>
              </div>
              <div>
                <strong>{metrics.length}</strong>
                <span>指标定义</span>
              </div>
              <div>
                <strong>{observationCount}</strong>
                <span>已发布数据</span>
              </div>
              <div>
                <strong>
                  {currentDataset?.created_at?.slice(0, 10) || "—"}
                </strong>
                <span>最近发布</span>
              </div>
            </div>
            <div className="hero-coverage">
              <span className="hero-coverage-title">指标主题覆盖</span>
              {topicCounts.length ? (
                topicCounts.map((topic) => (
                  <div className="coverage-row" key={topic.code}>
                    <span>{topicLabels[topic.code] || topic.title}</span>
                    <div className="coverage-track">
                      <i
                        style={{
                          width: `${Math.max(6, (topic.count / maxTopic) * 100)}%`,
                        }}
                      />
                    </div>
                    <small>{topic.count}</small>
                  </div>
                ))
              ) : (
                <p className="muted">指标目录准备中</p>
              )}
            </div>
            <ProductLink view="sources" className="hero-panel-foot">
              查看本期来源报告 <Icon name="arrow" size={15} />
            </ProductLink>
          </aside>
        </section>

        <section className="landing-section">
          <div className="section-heading">
            <div>
              <span className="eyebrow">HOW IT WORKS</span>
              <h2>从报告到数据，只走三步</h2>
            </div>
            <ProductLink view="methods" className="text-button">
              数据与复核方法 <Icon name="arrow" size={16} />
            </ProductLink>
          </div>
          <ol className="step-grid">
            {steps.map((step) => (
              <li className="step-card" key={step.mark}>
                <span className="step-mark mono">{step.mark}</span>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="landing-section">
          <div className="section-heading">
            <div>
              <span className="eyebrow">TOPICS</span>
              <h2>按主题浏览指标</h2>
            </div>
            <ProductLink view="metrics" className="text-button">
              全部指标 <Icon name="arrow" size={16} />
            </ProductLink>
          </div>
          <div className="topic-grid">
            {topics.map((topic, index) => (
              <ProductLink
                key={topic.code}
                view="metrics"
                values={{ topic: topic.code }}
                className={"topic-card topic-" + topic.code}
              >
                <span className="topic-number mono">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3>{topic.title}</h3>
                <p>{topic.text}</p>
                <span className="topic-meta">
                  {
                    metrics.filter((metric) => metric.topic === topic.code)
                      .length
                  }{" "}
                  项定义
                  <Icon name="arrow" size={15} />
                </span>
              </ProductLink>
            ))}
          </div>
        </section>

        <section className="landing-section">
          <div className="section-heading">
            <div>
              <span className="eyebrow">COMPANIES</span>
              <h2>已发布企业</h2>
            </div>
            <ProductLink view="entities" className="text-button">
              企业目录 <Icon name="arrow" size={16} />
            </ProductLink>
          </div>
          {entities.length ? (
            <div className="entity-card-grid landing-entities">
              {entities.slice(0, 3).map((entity) => (
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
                    <h3>
                      {entity.name_zh} <DemoBadge entity={entity} />
                    </h3>
                    <p>
                      {entity.industry_code || "行业信息未提供"} ·{" "}
                      {entity.country_code || "地区信息未提供"}
                    </p>
                    <span>{entity.coverage_count} 项已发布指标</span>
                  </span>
                  <Icon name="arrow" size={17} />
                </ProductLink>
              ))}
            </div>
          ) : (
            <Notice title="等待首批数据发布">
              这里将展示企业的正式发布数据。您可以先浏览指标定义、数据范围和比较方法。
            </Notice>
          )}
        </section>

        <section className="landing-section api-teaser">
          <div className="api-teaser-copy">
            <span className="eyebrow">OPEN API</span>
            <h2>把同一份数据接入你的分析</h2>
            <p>
              公开只读接口与本页使用同一发布版本。版本、口径与来源，通过接口读取时完全一致。
            </p>
            <div className="hero-links">
              <ProductLink view="api" className="button primary">
                查看 API 文档 <Icon name="arrow" size={16} />
              </ProductLink>
              <ProductLink view="methods" className="button ghost">
                数据与复核方法
              </ProductLink>
            </div>
          </div>
          <pre className="code-block">
            <code>{apiSample}</code>
          </pre>
        </section>
      </main>
      <footer className="site-footer">
        <div>
          <ProductLink view="landing" className="footer-brand">
            PlatESG
          </ProductLink>
          <p>从企业披露出发，让数据有据可依。</p>
        </div>
        <nav aria-label="页脚导航">
          <ProductLink view="entities">企业目录</ProductLink>
          <ProductLink view="metrics">指标浏览</ProductLink>
          <ProductLink view="sources">报告来源</ProductLink>
          <ProductLink view="methods">数据与方法</ProductLink>
          <ProductLink view="api">API</ProductLink>
        </nav>
        <span>
          {dataset ? "固定数据版本 " + dataset : "尚无已发布数据版本"} ·
          保留来源与公开修订
        </span>
      </footer>
    </div>
  );
}
