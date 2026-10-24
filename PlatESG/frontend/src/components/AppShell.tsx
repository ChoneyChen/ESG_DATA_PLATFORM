import {
  useCallback,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { apiUrl } from "../api";
import type { Dataset, View } from "../types";
import { ProductLink } from "./ProductLink";
import { Icon, type IconName } from "./Ui";

const nav: { view: View; label: string; icon: IconName }[] = [
  { view: "overview", label: "概览", icon: "dashboard" },
  { view: "entities", label: "企业", icon: "building" },
  { view: "metrics", label: "指标", icon: "book" },
  { view: "compare", label: "同行比较", icon: "compare" },
  { view: "sources", label: "报告来源", icon: "file" },
  { view: "methods", label: "数据复核", icon: "shield" },
  { view: "api", label: "API", icon: "code" },
];

const sectionTitles: Partial<Record<View, string>> = {
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

export default function AppShell({
  view,
  dataset,
  datasets,
  currentDataset,
  demo,
  onDataset,
  children,
}: {
  view: View;
  dataset: string;
  datasets: Dataset[];
  currentDataset?: Dataset;
  demo: boolean;
  onDataset: (value: string) => void;
  children: ReactNode;
}) {
  const active = nav.findIndex(
    (item) =>
      item.view === view ||
      (view === "entity" && item.view === "entities") ||
      (view === "metric" && item.view === "metrics"),
  );
  const [copied, setCopied] = useState(false);
  const share = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2200);
    } catch {
      setCopied(false);
    }
  }, []);
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        跳转到主要内容
      </a>
      <aside className="sidebar">
        <ProductLink
          view="overview"
          className="brand sidebar-brand"
          ariaLabel="PlatESG 数据平台"
        >
          <span className="brand-mark">
            <Icon name="layers" size={21} />
          </span>
          <span>
            Plat<span className="brand-green">ESG</span>
            <small>数据平台</small>
          </span>
        </ProductLink>
        <nav
          className="side-nav"
          aria-label="平台导航"
          style={{ "--active-index": Math.max(0, active) } as CSSProperties}
        >
          <span className="side-nav-indicator" aria-hidden="true" />
          {nav.map((item) => (
            <ProductLink
              key={item.view}
              view={item.view}
              ariaLabel={item.label}
              className={
                "side-nav-item" +
                (active >= 0 && item.view === nav[active].view ? " active" : "")
              }
            >
              <Icon name={item.icon} size={18} />
              <span>{item.label}</span>
            </ProductLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="eyebrow">数据版本</span>
          <strong className="mono">
            {dataset ? "v" + dataset : "尚无发布"}
          </strong>
          <small>
            {currentDataset
              ? currentDataset.observation_count +
                " 条 · " +
                currentDataset.created_at.slice(0, 10)
              : "等待首次发布"}
          </small>
          <ProductLink view="landing" className="sidebar-intro">
            <Icon name="chevronLeft" size={14} /> 返回产品介绍
          </ProductLink>
        </div>
      </aside>
      <div className="app-main">
        <header className="app-topbar">
          <div className="topbar-left">
            <span className="breadcrumb-root">PlatESG</span>
            <Icon name="chevronRight" size={14} />
            <strong>{sectionTitles[view] || "数据平台"}</strong>
          </div>
          <div className="topbar-right">
            <label className="dataset-picker">
              <span>版本</span>
              <select
                aria-label="选择数据集版本"
                value={dataset}
                disabled={!datasets.length}
                onChange={(event) => onDataset(event.target.value)}
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
            <button
              type="button"
              className="topbar-action"
              onClick={share}
              disabled={!dataset}
            >
              <Icon name="copy" size={16} />
              <span>{copied ? "已复制" : "分享"}</span>
            </button>
            <a
              className="topbar-action"
              aria-disabled={!dataset}
              href={
                dataset
                  ? apiUrl("/v1/exports", {
                      format: "csv",
                      dataset_version: dataset,
                    })
                  : undefined
              }
            >
              <Icon name="download" size={16} />
              <span>下载</span>
            </a>
          </div>
        </header>
        {demo && (
          <div className="demo-banner" role="note">
            演示预览 ·
            页面包含明确标注的合成演示数据，供体验展示与交互，不代表真实企业披露。
          </div>
        )}
        <main id="main-content" className="app-content">
          {children}
        </main>
        <footer className="app-footer">
          <span>
            {dataset ? "固定数据版本 " + dataset : "尚无已发布数据版本"} ·
            保留来源与公开修订
          </span>
          <span className="app-footer-links">
            <ProductLink view="methods">复核标准</ProductLink>
            <ProductLink view="api">API</ProductLink>
            <ProductLink view="landing">产品介绍</ProductLink>
          </span>
        </footer>
      </div>
    </div>
  );
}
