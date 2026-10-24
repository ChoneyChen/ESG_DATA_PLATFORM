import { useMemo } from "react";
import type { Dataset, Entity, Metric } from "../types";
import { topicLabels } from "../format";
import { useObservations } from "../hooks/useObservations";
import { ObservationTable } from "./ObservationTable";
import { ProductLink } from "./ProductLink";
import { Icon, Loading, Notice } from "./Ui";

const checks = [
  { title: "来源可追溯", text: "每条数据带报告原文与页码" },
  { title: "口径完整", text: "期间、边界、方法与单位逐条核对" },
  { title: "比较已判定", text: "同口径数据才进入比较" },
  { title: "版本固定", text: "发布后不可静默变更" },
];

export default function Overview({
  entities,
  metrics,
  dataset,
  currentDataset,
  datasets,
  onEvidence,
}: {
  entities: Entity[];
  metrics: Metric[];
  dataset: string;
  currentDataset?: Dataset;
  datasets: Dataset[];
  onEvidence: (id: string) => void;
}) {
  const data = useObservations(dataset);
  const topics = useMemo(() => {
    const counts = new Map<string, number>();
    for (const metric of metrics)
      counts.set(metric.topic, (counts.get(metric.topic) || 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [metrics]);
  const max = Math.max(1, ...topics.map(([, count]) => count));
  const previous = datasets[1];
  const growth = previous
    ? (currentDataset?.observation_count ?? 0) - previous.observation_count
    : 0;
  return (
    <div className="page overview">
      <header className="page-heading">
        <span className="eyebrow">PLATFORM OVERVIEW</span>
        <h1>数据概览</h1>
        <p>当前发布版本的数据范围、主题覆盖与复核状态。</p>
      </header>
      <section className="kpi-row" aria-label="发布规模">
        <article className="kpi-card">
          <span>已发布企业</span>
          <strong>{entities.length}</strong>
          <small className="kpi-foot">
            覆盖{" "}
            {
              new Set(
                entities.map((entity) => entity.industry_code).filter(Boolean),
              ).size
            }{" "}
            个行业
          </small>
        </article>
        <article className="kpi-card">
          <span>指标定义</span>
          <strong>{metrics.length}</strong>
          <small className="kpi-foot">ESRS 原编号完整保留</small>
        </article>
        <article className="kpi-card accent">
          <span>已发布数据</span>
          <strong>{currentDataset?.observation_count ?? 0}</strong>
          <small className="kpi-foot">
            {growth > 0 ? `较上一版新增 ${growth} 条` : "当前稳定版本"}
          </small>
        </article>
        <article className="kpi-card">
          <span>发布版本</span>
          <strong className="mono">{dataset ? "v" + dataset : "—"}</strong>
          <small className="kpi-foot">
            {currentDataset?.created_at?.slice(0, 10) || "尚无发布"}
          </small>
        </article>
      </section>
      <div className="overview-grid">
        <section className="panel coverage-panel">
          <div className="section-heading">
            <div>
              <h2>主题覆盖</h2>
              <p>按指标定义统计，数值来自当前目录。</p>
            </div>
            <ProductLink view="metrics" className="text-button">
              全部指标 <Icon name="arrow" size={15} />
            </ProductLink>
          </div>
          <div className="topic-bars">
            {topics.map(([code, count]) => (
              <ProductLink
                key={code}
                view="metrics"
                values={{ topic: code }}
                className="topic-bar"
              >
                <span>{topicLabels[code] || code}</span>
                <div className="coverage-track">
                  <i
                    style={{ width: `${Math.max(6, (count / max) * 100)}%` }}
                  />
                </div>
                <small>{count}</small>
              </ProductLink>
            ))}
            {!topics.length && <p className="muted">指标目录准备中</p>}
          </div>
        </section>
        <section className="panel review-panel">
          <div className="section-heading">
            <div>
              <h2>复核状态</h2>
              <p>发布前逐项核对，通过后固定为不可变版本。</p>
            </div>
            <span className="stamp">
              <Icon name="shield" size={13} /> 已复核发布
            </span>
          </div>
          <ul className="check-list">
            {checks.map((check) => (
              <li key={check.title}>
                <span className="trail-mark">
                  <Icon name="check" size={12} />
                </span>
                <div>
                  <strong>{check.title}</strong>
                  <small>{check.text}</small>
                </div>
              </li>
            ))}
          </ul>
          <ProductLink view="methods" className="text-button">
            复核标准与口径 <Icon name="arrow" size={15} />
          </ProductLink>
        </section>
      </div>
      <section className="panel">
        <div className="section-heading">
          <div>
            <h2>最近发布的数据</h2>
            <p>点击原文查看披露片段与复核凭证。</p>
          </div>
          <ProductLink view="sources" className="text-button">
            报告来源 <Icon name="arrow" size={15} />
          </ProductLink>
        </div>
        {data.loading ? (
          <Loading />
        ) : data.error ? (
          <p className="inline-error" role="alert">
            {data.error}
          </p>
        ) : data.rows.length ? (
          <ObservationTable
            rows={data.rows.slice(0, 6)}
            onEvidence={onEvidence}
            showEntity
          />
        ) : (
          <Notice title="此版本暂无已发布数据">
            发布完成后，最近数据会在这里出现。
          </Notice>
        )}
      </section>
      <section className="quick-grid" aria-label="快捷入口">
        <ProductLink view="entities" className="quick-card">
          <Icon name="building" size={20} />
          <strong>企业目录</strong>
          <span>{entities.length} 家企业</span>
          <Icon name="arrow" size={16} />
        </ProductLink>
        <ProductLink view="compare" className="quick-card">
          <Icon name="compare" size={20} />
          <strong>同行比较</strong>
          <span>同口径横向对比</span>
          <Icon name="arrow" size={16} />
        </ProductLink>
        <ProductLink view="sources" className="quick-card">
          <Icon name="file" size={20} />
          <strong>报告来源</strong>
          <span>回到原文与页码</span>
          <Icon name="arrow" size={16} />
        </ProductLink>
        <ProductLink view="api" className="quick-card">
          <Icon name="code" size={20} />
          <strong>API 文档</strong>
          <span>只读接口与导出</span>
          <Icon name="arrow" size={16} />
        </ProductLink>
      </section>
    </div>
  );
}
