import { useState, type ReactNode } from "react";
import { ProductLink } from "./ProductLink";

export default function AppShell({
  datasetCount,
  observationCount,
  latestPublishedAt,
  downloadUrl,
  children,
}: {
  datasetCount: number;
  observationCount: number | null;
  latestPublishedAt: string | null;
  downloadUrl?: string | null;
  children: ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  async function share() {
    try {
      await navigator.clipboard.writeText(location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        跳转到主要内容
      </a>
      <aside className="sidebar">
        <ProductLink view="datasets" className="brand">
          <span className="brand-mark">P</span>
          <span>
            Plat<span className="brand-green">ESG</span>
            <small>已发布科学数据</small>
          </span>
        </ProductLink>
        <nav className="side-nav" aria-label="平台导航">
          <ProductLink view="datasets" className="side-nav-item active">
            已发布数据
          </ProductLink>
        </nav>
        <div className="sidebar-foot">
          <span>已发布数据集</span>
          <strong>{datasetCount} 个</strong>
          {observationCount != null && (
            <small>
              {observationCount} 条观测
              {latestPublishedAt ? " · " + latestPublishedAt.slice(0, 10) : ""}
            </small>
          )}
        </div>
      </aside>
      <div className="app-main">
        <header className="app-topbar">
          <span>PlatESG / 已发布数据</span>
          <div>
            <a href="/docs" className="topbar-action">
              API
            </a>
            <button className="topbar-action" onClick={share}>
              {copied ? "已复制" : "分享"}
            </button>
            {downloadUrl && (
              <a className="topbar-action" href={downloadUrl}>
                下载
              </a>
            )}
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="app-content">
          {children}
        </main>
      </div>
    </div>
  );
}
