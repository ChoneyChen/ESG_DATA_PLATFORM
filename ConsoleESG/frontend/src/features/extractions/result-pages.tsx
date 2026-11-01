import { useState, useEffect } from "react";
import type { FactPage, OrganizedPage, ResultOverview } from "../../shared/contracts";
import { useResource } from "../../shared/resource";
import { segment } from "../../shared/api";
import { ReadError, Loading } from "../../shared/ui";
import { FactsTable, OrganizedTable } from "./results";

function usePaging() {
  const [cursors, setCursors] = useState<string[]>([]);
  return { cursor: cursors.at(-1), number: cursors.length + 1,
    reset: () => setCursors([]),
    next: (cursor: string) => setCursors([...cursors, cursor]),
    previous: () => setCursors(cursors.slice(0, -1)) };
}
function Pager({ total, returned, next, paging }: { total: number; returned: number; next?: string | null; paging: ReturnType<typeof usePaging> }) {
  return <div className="result-footer"><span>共 {total} 条 · 本页 {returned} 条</span><div>
    <button className="button-secondary" disabled={paging.number === 1} onClick={paging.previous}>上一页</button>
    <span> 第 {paging.number} 页 </span>
    <button className="button-secondary" disabled={!next} onClick={() => next && paging.next(next)}>下一页</button>
  </div></div>;
}
export function FactPages({ sessionId, overview, active }: { sessionId: string; overview?: ResultOverview; active: boolean }) {
  const paging = usePaging();
  const [period, setPeriod] = useState("");
  const [clause, setClause] = useState("");
  const [query, setQuery] = useState("");
  const params = new URLSearchParams({ limit: "50" });
  if (paging.cursor) params.set("cursor", paging.cursor);
  if (period) params.set("period", period);
  if (clause) params.set("clause_key", clause);
  if (query) params.set("q", query);
  const page = useResource<FactPage>("document", `/api/report-sessions/${segment(sessionId)}/facts?${params}`, active ? 4000 : 0);
  useEffect(() => { if (!active) page.reload(); }, [active, page.reload]);
  return <>
    <div className="result-filters">
      <input aria-label="搜索事实" placeholder="搜索项目或原文" value={query} onChange={e => { setQuery(e.target.value); paging.reset(); }}/>
      <select aria-label="条款筛选" value={clause} onChange={e => { setClause(e.target.value); paging.reset(); }}><option value="">全部条款</option>{overview?.clauses.map(c => <option key={c.clause_key} value={c.clause_key}>{c.title}</option>)}</select>
      <select aria-label="期间筛选" value={period} onChange={e => { setPeriod(e.target.value); paging.reset(); }}><option value="">全部期间</option>{Object.keys(overview?.periods || {}).map(p => <option key={p}>{p}</option>)}</select>
    </div>
    <ReadError error={page.error} retry={page.reload}/>
    {page.data ? <><FactsTable facts={page.data.facts} sessionId={sessionId}/><Pager total={page.data.total} returned={page.data.returned} next={page.data.next_cursor} paging={paging}/></> : !page.error && <Loading/>}
  </>;
}
export function ClauseResultPage({ sessionId, clauseKey, active }: { sessionId: string; clauseKey: string; active: boolean }) {
  const paging = usePaging();
  const [relation, setRelation] = useState("match");
  const [period, setPeriod] = useState("");
  const [unit, setUnit] = useState("");
  const [query, setQuery] = useState("");
  const params = new URLSearchParams({ limit: "50" });
  if (paging.cursor) params.set("cursor", paging.cursor);
  if (relation) params.set("relation", relation);
  if (period) params.set("period", period);
  if (unit) params.set("unit", unit);
  if (query) params.set("q", query);
  const page = useResource<OrganizedPage>("document", `/api/report-sessions/${segment(sessionId)}/organized/${segment(clauseKey)}?${params}`, active ? 4000 : 0);
  useEffect(() => { if (!active) page.reload(); }, [active, page.reload]);
  const data = page.data;
  return <>
    <div className="result-toolbar"><div className="segmented" aria-label="结果分类">
      {[["match", "目标数据"], ["different", "其他数据"], ["uncertain", "关系未决"], ["", "全部"]].map(([key, name]) => <button key={key} aria-pressed={relation === key} onClick={() => { setRelation(key); paging.reset(); }}>{name} <span>{key ? data?.relations[key] || 0 : Object.values(data?.relations || {}).reduce((a,b) => a+b, 0)}</span></button>)}
    </div></div>
    <div className="result-filters">
      <input aria-label="搜索结果" placeholder="搜索项目或原文" value={query} onChange={e => { setQuery(e.target.value); paging.reset(); }}/>
      <select aria-label="期间筛选" value={period} onChange={e => { setPeriod(e.target.value); paging.reset(); }}><option value="">全部期间</option>{data?.periods.map(p => <option key={p}>{p}</option>)}</select>
      <select aria-label="单位筛选" value={unit} onChange={e => { setUnit(e.target.value); paging.reset(); }}><option value="">全部单位</option>{data?.units.map(u => <option key={u}>{u}</option>)}</select>
    </div>
    <ReadError error={page.error} retry={page.reload}/>
    {data ? <><OrganizedTable table={data.table} sessionId={sessionId} facts={data.facts}/><Pager total={data.total} returned={data.returned} next={data.next_cursor} paging={paging}/></> : !page.error && <Loading/>}
  </>;
}
