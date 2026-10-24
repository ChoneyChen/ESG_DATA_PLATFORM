import { useCallback, useEffect } from "react";
import { apiUrl } from "./api";
import { useResource } from "./hooks/useResource";
import { currentView, useLocation } from "./navigation";
import type {
  DatasetResponse,
  Entity,
  MetricsResponse,
  VersionedItems,
} from "./types";
import { ErrorNotice, Loading, Notice } from "./components/Ui";
import Landing from "./components/Landing";
import AppShell from "./components/AppShell";
import Overview from "./components/Overview";
import EntityDirectory from "./components/EntityDirectory";
import EntityDetail from "./components/EntityDetail";
import MetricCatalog from "./components/MetricCatalog";
import MetricDetail from "./components/MetricDetail";
import Comparison from "./components/Comparison";
import Sources from "./components/Sources";
import Methods from "./components/Methods";
import ApiDocs from "./components/ApiDocs";
import EvidenceDrawer from "./components/EvidenceDrawer";

const titles: Record<string, string> = {
  landing: "企业数据平台",
  overview: "数据概览",
  entities: "企业目录",
  entity: "企业档案",
  metrics: "指标浏览",
  metric: "指标详情",
  compare: "同行比较",
  sources: "报告来源",
  methods: "数据复核",
  api: "API 文档",
};

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
  const evidenceId = params.get("evidence");
  const closeEvidence = useCallback(
    () => patch({ evidence: null }, true),
    [patch],
  );
  const openEvidence = useCallback(
    (id: string) => patch({ evidence: id }),
    [patch],
  );
  useEffect(() => {
    if (
      dataset &&
      !invalidDataset &&
      (params.get("dataset_version") !== dataset || params.has("dataset"))
    )
      patch({ dataset_version: dataset, dataset: null }, true);
  }, [dataset, invalidDataset, params, patch]);
  useEffect(() => {
    const selectedName =
      view === "entity"
        ? selectedEntity?.name_zh
        : view === "metric"
          ? selectedMetric?.label_zh
          : "";
    document.title =
      [selectedName || titles[view] || titles.landing, "PlatESG"]
        .filter(Boolean)
        .join(" · ");
  }, [view, dataset, selectedEntity, selectedMetric]);
  const demo = entityList.some(
    (entity) => entity.is_demo || /^demo[.:_-]/i.test(entity.entity_ref),
  );
  const retry = () => {
    datasets.reload();
    entities.reload();
    metrics.reload();
  };
  const changeDataset = (value: string) =>
    patch({ dataset_version: value, dataset: null, evidence: null, page: null });

  const contentReady =
    !!error ||
    invalidDataset ||
    (!!datasets.data && !entities.loading && !metrics.loading);

  const content = error ? (
    <ErrorNotice error={error} retry={retry} />
  ) : invalidDataset ? (
    <Notice title="此发布版本不可用">
      链接中的数据版本不存在或未公开。请选择已发布的数据版本。
    </Notice>
  ) : view === "overview" ? (
    <Overview
      entities={entityList}
      metrics={metricList}
      dataset={dataset}
      currentDataset={currentDataset}
      datasets={datasets.data?.items || []}
      onEvidence={openEvidence}
    />
  ) : view === "entities" ? (
    <EntityDirectory entities={entityList} metricCount={metricList.length} />
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
  ) : view === "methods" ? (
    <Methods dataset={dataset} datasets={datasets.data?.items || []} />
  ) : view === "api" ? (
    <ApiDocs dataset={dataset} />
  ) : null;

  const isLanding = view === "landing";
  const drawer =
    evidenceId && dataset ? (
      <EvidenceDrawer
        observationId={evidenceId}
        dataset={dataset}
        close={closeEvidence}
      />
    ) : null;

  if (isLanding)
    return (
      <div className="site-shell">
        <Landing
          entities={entityList}
          metrics={metricList}
          dataset={dataset}
          currentDataset={currentDataset}
          datasets={datasets.data?.items || []}
          demo={demo}
          error={error}
        />
        {drawer}
      </div>
    );

  return (
    <AppShell
      view={view}
      dataset={dataset}
      datasets={datasets.data?.items || []}
      currentDataset={currentDataset}
      demo={demo}
      onDataset={changeDataset}
    >
      {contentReady ? content : <Loading rows={4} />}
      {drawer}
    </AppShell>
  );
}
