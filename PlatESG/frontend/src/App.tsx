import { useCallback, useEffect } from "react";
import { apiUrl } from "./api";
import { useResource } from "./hooks/useResource";
import { currentView, useLocation } from "./navigation";
import type {
  Dataset,
  DatasetResponse,
  Entity,
  MetricsResponse,
  ReportReleaseResponse,
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
  const reports = useResource<ReportReleaseResponse>(apiUrl("/v1/reports"));
  // The public surface is the current set of published reports. Report
  // versions are independent; there is no global version the reader selects.
  const dataset = "";
  const publishedReports = reports.data?.items || [];
  const latestPublished = publishedReports
    .map((item) => item.published_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
  const currentDataset: Dataset | undefined = publishedReports.length
    ? {
        dataset_version: "current",
        created_at: latestPublished || "",
        reason: null,
        observation_count: publishedReports.reduce(
          (sum, item) => sum + Number(item.observation_count || 0),
          0,
        ),
      }
    : datasets.data?.items.find(
        (item) =>
          String(item.dataset_version) === String(datasets.data?.latest),
      );
  const entities = useResource<VersionedItems<Entity>>(apiUrl("/v1/entities"));
  const metrics = useResource<MetricsResponse>(apiUrl("/v1/metrics"));
  const entityList = entities.data?.items || [],
    metricList = metrics.data?.items || [];
  const selectedEntity = entityList.find(
    (entity) => entity.entity_ref === params.get("entity"),
  );
  const selectedMetric = metricList.find(
    (metric) => metric.metric_id === params.get("metric"),
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
  const contentReady = !!error || (!!datasets.data && !entities.loading && !metrics.loading);

  const content = error ? (
    <ErrorNotice error={error} retry={retry} />
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
    <Notice title="当前发布中未找到该企业">
      请返回企业目录，选择已发布报告中的企业。
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
    <Notice title="当前发布中未找到该指标">
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
    <Methods datasets={datasets.data?.items || []} />
  ) : view === "api" ? (
    <ApiDocs dataset={dataset} />
  ) : null;

  const isLanding = view === "landing";
  const drawer = evidenceId ? (
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
          currentDataset={currentDataset}
          demo={demo}
          error={error}
          reportCount={publishedReports.length}
        />
        {drawer}
      </div>
    );

  return (
    <AppShell
      view={view}
      reportCount={publishedReports.length}
      observationCount={currentDataset?.observation_count || 0}
      latestPublishedAt={latestPublished || null}
      demo={demo}
    >
      {contentReady ? content : <Loading rows={4} />}
      {drawer}
    </AppShell>
  );
}
