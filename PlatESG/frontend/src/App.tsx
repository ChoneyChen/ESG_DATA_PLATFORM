import { useCallback, useEffect, useState } from "react";
import { apiUrl } from "./api";
import { useResource } from "./hooks/useResource";
import { currentView, useLocation } from "./navigation";
import type {
  DatasetResponse,
  Entity,
  MetricsResponse,
  VersionedItems,
  View,
} from "./types";
import { ErrorNotice, Icon, Loading, Notice } from "./components/Ui";
import { ProductLink } from "./components/ProductLink";
import Home from "./components/Home";
import EntityDirectory from "./components/EntityDirectory";
import EntityDetail from "./components/EntityDetail";
import MetricCatalog from "./components/MetricCatalog";
import MetricDetail from "./components/MetricDetail";
import Comparison from "./components/Comparison";
import Sources from "./components/Sources";
import Methods from "./components/Methods";
import EvidenceDrawer from "./components/EvidenceDrawer";

const menu: { view: View; label: string }[] = [
  { view: "home", label: "首页" },
  { view: "entities", label: "企业" },
  { view: "metrics", label: "指标" },
  { view: "compare", label: "同行比较" },
  { view: "sources", label: "来源" },
  { view: "methods", label: "数据与方法" },
];
export default function App() {
  const [params, patch] = useLocation(),
    view = currentView(params);
  const datasets = useResource<DatasetResponse>(apiUrl("/v1/datasets"));
  const dataset = String(
    params.get("dataset_version") ||
      params.get("dataset") ||
      datasets.data?.latest ||
      "",
  );
  const invalidDataset =
    !!datasets.data &&
    !!dataset &&
    !datasets.data.items.some(
      (item) => String(item.dataset_version) === dataset,
    );
  const entities = useResource<VersionedItems<Entity>>(
    datasets.data && !invalidDataset
      ? apiUrl("/v1/entities", { dataset_version: dataset })
      : null,
  );
  const metrics = useResource<MetricsResponse>(
    datasets.data && !invalidDataset
      ? apiUrl("/v1/metrics", { dataset_version: dataset })
      : null,
  );
  const entityList = entities.data?.items || [],
    metricList = metrics.data?.items || [];
  const selectedEntity = entityList.find(
    (entity) => entity.entity_ref === params.get("entity"),
  );
  const selectedMetric = metricList.find(
    (metric) => metric.metric_id === params.get("metric"),
  );
  const currentDataset = datasets.data?.items.find(
    (item) => String(item.dataset_version) === dataset,
  );
  const error = datasets.error || entities.error || metrics.error;
  const loading = datasets.loading || entities.loading || metrics.loading;
  const evidenceId = params.get("evidence");
  const closeEvidence = useCallback(
    () => patch({ evidence: null }, true),
    [patch],
  );
  const openEvidence = useCallback(
    (id: string) => patch({ evidence: id }),
    [patch],
  );
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (
      dataset &&
      !invalidDataset &&
      (params.get("dataset_version") !== dataset || params.has("dataset"))
    )
      patch({ dataset_version: dataset, dataset: null }, true);
  }, [dataset, invalidDataset, params, patch]);
  useEffect(() => {
    document.title =
      (menu.find((item) => item.view === view)?.label ||
        (view === "entity"
          ? selectedEntity?.name_zh
          : selectedMetric?.label_zh) ||
        "企业数据") + " · PlatESG";
    setCopied(false);
  }, [view, dataset, selectedEntity, selectedMetric]);
  const demo = entityList.some(
    (entity) => entity.is_demo || /^demo[.:_-]/i.test(entity.entity_ref),
  );
  const retry = () => {
    datasets.reload();
    entities.reload();
    metrics.reload();
  };
  return (
    <div className="site-shell">
      <a href="#main-content" className="skip-link">
        跳转到主要内容
      </a>
      <header className="site-header">
        <div className="header-inner">
          <ProductLink view="home" className="brand">
            <span className="brand-mark">
              <Icon name="layers" size={25} />
            </span>
            <span>
              Plat<span className="brand-green">ESG</span>
              <small>企业可持续发展数据</small>
            </span>
          </ProductLink>
          <nav className="site-nav" aria-label="主要导航">
            {menu.map((item) => (
              <ProductLink
                key={item.view}
                view={item.view}
                className={
                  view === item.view ||
                  (view === "entity" && item.view === "entities") ||
                  (view === "metric" && item.view === "metrics")
                    ? "active"
                    : ""
                }
              >
                {item.label}
              </ProductLink>
            ))}
          </nav>
          <div className="header-actions">
            <a
              className="header-data-link"
              href={
                dataset
                  ? apiUrl("/v1/exports", {
                      format: "csv",
                      dataset_version: dataset,
                    })
                  : undefined
              }
              aria-disabled={!dataset}
            >
              <Icon name="download" size={18} />
              <span>下载数据</span>
            </a>
          </div>
        </div>
      </header>
      <div className="data-context">
        <div className="context-inner">
          <span>
            <span className="status-dot" />
            {currentDataset
              ? "发布于 " + currentDataset.created_at.slice(0, 10)
              : "尚无已发布数据"}
          </span>
          <div>
            <label className="dataset-picker">
              数据版本
              <select
                aria-label="选择数据集版本"
                value={dataset}
                disabled={!datasets.data?.items.length}
                onChange={(event) =>
                  patch({
                    dataset_version: event.target.value,
                    dataset: null,
                    evidence: null,
                  })
                }
              >
                {!datasets.data?.items.length && (
                  <option value="">尚无发布版本</option>
                )}
                {datasets.data?.items.map((item) => (
                  <option
                    key={item.dataset_version}
                    value={String(item.dataset_version)}
                  >
                    {item.dataset_version}
                    {String(item.dataset_version) ===
                    String(datasets.data?.latest)
                      ? " · 最新"
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="share-button"
              disabled={!dataset}
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(window.location.href);
                  setCopied(true);
                } catch {
                  setCopied(false);
                }
              }}
            >
              <Icon name="layers" size={15} />
              {copied ? "链接已复制" : "分享此页面"}
            </button>
          </div>
        </div>
      </div>
      {demo && (
        <div className="demo-banner" role="note">
          演示预览 ·
          页面包含明确标注的合成演示数据，供体验展示与交互，不代表真实企业披露。
        </div>
      )}
      <main id="main-content" className="site-content">
        {error ? (
          <ErrorNotice error={error} retry={retry} />
        ) : invalidDataset ? (
          <Notice title="此发布版本不可用">
            链接中的数据版本不存在或未公开。请选择已发布的数据版本。
          </Notice>
        ) : loading ? (
          <Loading rows={5} />
        ) : view === "home" ? (
          <Home
            entities={entityList}
            metrics={metricList}
            dataset={dataset}
            currentDataset={currentDataset}
            onEvidence={openEvidence}
          />
        ) : view === "entities" ? (
          <EntityDirectory
            entities={entityList}
            metricCount={metricList.length}
          />
        ) : view === "entity" && selectedEntity ? (
          <EntityDetail
            entity={selectedEntity}
            metrics={metricList}
            dataset={dataset}
            onEvidence={openEvidence}
          />
        ) : view === "entity" ? (
          <Notice title="此版本未找到企业">
            请返回企业目录，选择本版本中已发布的企业。
          </Notice>
        ) : view === "metrics" ? (
          <MetricCatalog metrics={metricList} />
        ) : view === "metric" && selectedMetric ? (
          <MetricDetail
            metric={selectedMetric}
            dataset={dataset}
            onEvidence={openEvidence}
          />
        ) : view === "metric" ? (
          <Notice title="此版本未找到指标">
            请返回指标浏览，选择已有定义的指标。
          </Notice>
        ) : view === "compare" ? (
          <Comparison
            entities={entityList}
            metrics={metricList}
            dataset={dataset}
            onEvidence={openEvidence}
          />
        ) : view === "sources" ? (
          <Sources
            dataset={dataset}
            entities={entityList}
            onEvidence={openEvidence}
          />
        ) : (
          <Methods dataset={dataset} datasets={datasets.data?.items || []} />
        )}
      </main>
      <footer className="site-footer">
        <div>
          <ProductLink view="home" className="footer-brand">
            PlatESG
          </ProductLink>
          <p>从企业披露出发，让数据有据可依。</p>
        </div>
        <nav aria-label="页脚导航">
          <ProductLink view="entities">企业目录</ProductLink>
          <ProductLink view="metrics">指标浏览</ProductLink>
          <ProductLink view="sources">报告来源</ProductLink>
          <ProductLink view="methods">数据与方法</ProductLink>
        </nav>
        <span>
          {dataset ? "固定数据版本 " + dataset : "尚无已发布数据版本"} ·
          保留来源与公开修订
        </span>
      </footer>
      {evidenceId && dataset && (
        <EvidenceDrawer
          observationId={evidenceId}
          dataset={dataset}
          close={closeEvidence}
        />
      )}
    </div>
  );
}
