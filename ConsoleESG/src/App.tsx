import {useEffect,useState} from 'react';
import {navigate} from './api';
import {useWorkspace} from './state';
import {Notice} from './components/ui';
import {OcrLibrary} from './pages/OcrLibrary';
import {SidebarMonitor} from './components/SidebarMonitor';
import {Reports,ReportDetail} from './pages/Reports';
import {Processing} from './pages/Processing';
import {Automation} from './pages/Automation';
import {Extractions} from './pages/Extractions';
import {Standards} from './pages/Standards';
import {Review} from './pages/Review';
import {Publication} from './pages/Publication';
import {Runtime} from './pages/Runtime';

/**
 * The sidebar follows the production line, not the backend's module list.
 * Four destinations cover an operator's day: reports, extraction, review and
 * publishing. Report processing and page reading are report evidence, reached
 * from a report; reports enter only by uploading a PDF here.
 */
const MAIN = [
  ['reports', '报告', '▤', '选择报告并开始'],
  ['extractions', '提取', '⇢', '读取、成果与证据'],
  ['review', '审核', '✓', '事实、映射与口径'],
  ['publication', '发布', '↗', '版本、来源与发布'],
] as const;

const TOOLS = [
  ['automation', '批量生产', '▷'],
  ['standards', '标准中心', '▦'],
  ['runtime', '运行中心', '⚙'],
] as const;

// Advanced readers stay reachable from the run center and report detail without
// becoming a destination of their own on the production line.
const DIAGNOSTIC_TITLES: Record<string, string> = {processing: '报告处理', ocr: '页面读取结果'};

export function parseRoute(hash: string) {
  const [raw, search = ''] = hash.replace(/^#/, '').split('?');
  const parts = (raw || '/reports').split('/').filter(Boolean).map(decodeURIComponent);
  return {section: parts[0] || 'reports', id: parts[1], tab: parts[2], params: new URLSearchParams(search)};
}

function sectionTitle(section: string) {
  return [...MAIN, ...TOOLS].find(item => item[0] === section)?.[1] || DIAGNOSTIC_TITLES[section] || '数据工作台';
}

export default function App() {
  const [hash, setHash] = useState(window.location.hash);
  const {refresh, connected} = useWorkspace();
  const route = parseRoute(hash);
  useEffect(() => {
    const fn = () => setHash(window.location.hash);
    window.addEventListener('hashchange', fn);
    return () => window.removeEventListener('hashchange', fn);
  }, []);
  useEffect(() => {
    document.title = `${sectionTitle(route.section)} · ConsoleESG`;
    window.scrollTo({top: 0});
  }, [route.section, route.id]);

  let page;
  switch (route.section) {
    case 'reports': page = route.id ? <ReportDetail key={route.id} assetId={route.id} tab={route.tab}/> : <Reports params={route.params}/>; break;
    case 'ocr': page = <OcrLibrary key={`${route.id || ''}:${route.params.toString()}`} runId={route.id} params={route.params}/>; break;
    case 'processing': page = <Processing key={`${route.id || ''}:${route.params.toString()}`} processingId={route.id === 'new' ? undefined : route.id} newTask={route.id === 'new'} params={route.params}/>; break;
    case 'automation': page = <Automation key={`${route.id || ''}:${route.params.toString()}`} automationId={route.id === 'new' ? undefined : route.id} newTask={route.id === 'new'} params={route.params}/>; break;
    case 'extractions': page = <Extractions key={`${route.id || ''}:${route.tab || ''}:${route.params.toString()}`} sessionId={['new', 'standalone'].includes(route.id || '') ? undefined : route.id} standaloneJobId={route.id === 'standalone' ? route.tab : undefined} newTask={route.id === 'new'} params={route.params}/>; break;
    case 'standards': page = <Standards key={`${route.id || 'center'}:${route.tab || ''}`} packageId={route.id} clauseId={route.tab}/>; break;
    case 'review': page = <Review mappingId={route.id} params={route.params}/>; break;
    case 'publication': page = <Publication key={route.id || ''} reportRef={route.id} params={route.params}/>; break;
    case 'runtime': page = <Runtime/>; break;
    default: page = <Notice tone="error">找不到该工作台页面。<button onClick={() => navigate('/reports')}>返回报告</button></Notice>;
  }

  const active = (key: string) => route.section === key || (key === 'extractions' && ['ocr', 'processing'].includes(route.section));
  const toolsOpen = TOOLS.some(([key]) => key === route.section) || ['processing', 'ocr'].includes(route.section);
  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <a className="brand" href="#/reports">
          <span className="brand-icon">E</span>
          <span className="brand-text"><span className="brand-name">Console<span className="brand-accent">ESG</span></span><small>数据生产工作台</small></span>
        </a>
        <div className="side-section">
          <span className="side-label">生产主线</span>
          <nav aria-label="生产主线">
            {MAIN.map(([key, title, icon, hint], index) => (
              <a className={active(key) ? 'active' : ''} href={`#/${key}`} key={key} aria-current={active(key) ? 'page' : undefined}>
                <span className="nav-index">{index + 1}</span>
                <span className="nav-body"><strong>{title}</strong><small>{hint}</small></span>
                <span className="nav-icon" aria-hidden="true">{icon}</span>
              </a>
            ))}
          </nav>
        </div>
        <details className="sidebar-tools" open={toolsOpen}>
          <summary>工具</summary>
          <nav aria-label="工具">
            {TOOLS.map(([key, title, icon]) => (
              <a className={route.section === key ? 'active' : ''} href={`#/${key}`} key={key}><span className="nav-icon" aria-hidden="true">{icon}</span>{title}</a>
            ))}
          </nav>
        </details>
        <div className="sidebar-footer">
          <p>报告 → 提取 → 审核 → 发布</p>
          <a href="http://127.0.0.1:8765/" target="_blank" rel="noreferrer">打开数据网站 ↗</a>
        </div>
        <SidebarMonitor/>
      </aside>
      <div className="app-content">
        <header className="topbar">
          <div className="topbar-context">
            <span className="topbar-section">{sectionTitle(route.section)}</span>
            <span className="topbar-sep" aria-hidden="true">/</span>
            <span className="topbar-sub">数据生产工作台</span>
          </div>
          <div className="button-row">
            <span className="topbar-live" role="status" aria-live="polite">
              <span className={`live-dot ${connected ? 'connected' : ''}`}/>
              {connected ? '实时连接' : '自动刷新与恢复'}
            </span>
            <button className="ghost" onClick={refresh} aria-label="刷新工作台">刷新</button>
          </div>
        </header>
        <main id="main-content" className="workspace">
          {page}
        </main>
        <footer className="app-footer">ConsoleESG · 正式数据与未发布工作分开管理 · 来源与修订可追踪</footer>
      </div>
    </div>
  );
}
