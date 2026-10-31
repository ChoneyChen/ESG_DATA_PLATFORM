import { useEffect, useState } from "react";
import { parseRoute, href } from "./routes";
import { ReportsPage, ReportPage } from "../features/reports/pages";
import { ExtractionsPage } from "../features/extractions/list";
import { CreateExtractionPage } from "../features/extractions/create";
import { ExtractionPage } from "../features/extractions/detail";
import { ClausesPage } from "../features/clauses/pages";
import { RuntimePage } from "../features/runtime/pages";
import { RunMonitor } from "../features/runtime/monitor";
import { ReceivedDataPage } from "../features/publication/data";
import {
  PublicationPage,
  DatasetPageView,
} from "../features/publication/pages";
import { PreparePublicationPage } from "../features/publication/create";
import { Empty } from "../shared/ui";
const nav = [
  ["reports", "报告", "▤"],
  ["extractions", "提取", "↗"],
  ["clauses", "条款", "▦"],
  ["publication", "发布", "⇧"],
  ["runtime", "运行", "◉"],
];
export default function App() {
  const [route, setRoute] = useState(() => parseRoute(location.hash));
  useEffect(() => {
    const change = () => {
      setRoute(parseRoute(location.hash));
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  const { section, parts, params } = route;
  let page;
  if (route.valid && section === "reports" && parts.length <= 1)
    page = parts[0] ? (
      <ReportPage key={parts[0]} pdfId={parts[0]} />
    ) : (
      <ReportsPage />
    );
  else if (route.valid && section === "extractions" && parts.length === 0)
    page = <ExtractionsPage reportId={params.get("report") || undefined} />;
  else if (
    route.valid &&
    section === "extractions" &&
    parts.length === 2 &&
    parts[1] === "data"
  )
    page = <ReceivedDataPage key={parts[0]} sessionId={parts[0]} />;
  else if (route.valid && section === "publication" && parts.length <= 1)
    page =
      parts[0] === "new" ? (
        <PreparePublicationPage
          key={location.hash}
          initialProjection={params.get("projection") || undefined}
        />
      ) : parts[0] ? (
        <DatasetPageView key={parts[0]} id={parts[0]} />
      ) : (
        <PublicationPage />
      );
  else if (
    route.valid &&
    section === "extractions" &&
    parts[0] === "new" &&
    parts.length === 1
  )
    page = (
      <CreateExtractionPage
        key={location.hash}
        initialReport={params.get("report") || undefined}
      />
    );
  else if (
    route.valid &&
    section === "extractions" &&
    parts[0] &&
    (parts.length === 1 ||
      (parts.length === 3 && ["tables", "facts"].includes(parts[1])))
  )
    page = (
      <ExtractionPage
        key={parts.join("/")}
        sessionId={parts[0]}
        child={parts[1]}
        childId={parts[2]}
        tabId={params.get("tab") || undefined}
        returnClause={params.get("clause") || undefined}
      />
    );
  else if (route.valid && section === "clauses" && parts.length <= 2)
    page = (
      <ClausesPage
        key={parts.join("/")}
        groupId={parts[0]}
        clauseKey={parts[1]}
      />
    );
  else if (route.valid && section === "runtime" && parts.length <= 1)
    page = <RuntimePage key={parts[0] || "runtime"} taskId={parts[0]} />;
  else
    page = (
      <Empty
        title="页面不存在"
        action={
          <a className="button" href={href("reports")}>
            返回报告库
          </a>
        }
      />
    );
  return (
    <div className="workspace">
      <a
        href="#main-content"
        className="skip-link"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        跳到主要内容
      </a>
      <aside className="sidebar">
        <a className="brand" href={href("reports")}>
          <span className="brand-mark">E</span>
          <span>
            ESG <small>Console</small>
          </span>
        </a>
        <nav aria-label="主导航">
          {nav.map(([id, name, icon]) => (
            <a
              key={id}
              href={href(id)}
              className={section === id ? "selected" : ""}
              aria-current={section === id ? "page" : undefined}
            >
              <span aria-hidden="true">{icon}</span>
              {name}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="local-dot" />
          本机工作台<small>报告 → 条款 → 原值与证据</small>
        </div>
      </aside>
      <main
        id="main-content"
        tabIndex={-1}
        className="main-content"
        key={section}
      >
        {page}
      </main>
      <RunMonitor />
    </div>
  );
}
