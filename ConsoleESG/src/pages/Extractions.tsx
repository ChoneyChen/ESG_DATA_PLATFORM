import {useCallback, useEffect, useState, type FormEvent} from 'react';
import {credentials, items, navigate, post, reportLabel, type Row} from '../api';
import {useResource, useWorkspace} from '../state';
import {Actions, Badge, Diagnostics, Field, MetricValue, Notice, Progress, ResourceState, Section} from '../components/ui';
import {ScopeSelector, type Scope} from '../components/ScopeSelector';
import {DeliveryStatusBadge, SessionDeliveryBadge, useDeliveryStatus} from '../components/DeliveryStatus';
import {RecordManagement} from '../components/RecordManagement';
import {BatchRecordDelete, endedRecords} from '../components/BatchRecordDelete';
import {workActions} from '../management-api';
import {useLibraryResource} from '../library-resource';
import {initialExtractionInput, extractionInput} from '../extraction-input';
import {ExtractionReportInput} from '../components/ExtractionReportInput';
import {PackageBudgetUsage} from '../components/PackageBudgetUsage';
import {ExtractionModelPlan, defaultModelPlan, requestModelPlan, type ModelPlanSelection} from '../components/ExtractionModelPlan';
import {ExtensionSelector} from '../components/ExtensionSelector';
import {Foundation16Board} from '../components/Foundation16Board';
import {PipelineProgress} from '../components/PipelineProgress';
import {PublishFlow} from '../components/PublishFlow';
import {preparationText} from '../pipeline';

export function ReportReadNotice({row}: {row: Row}) {
  const message = row.state_notice || row.read_error?.message;
  if (!message) return null;
  return <Notice>{message}{row.read_error?.request_id && <span> · 问题编号 {row.read_error.request_id}</span>}</Notice>;
}

export function standaloneJobsFromReports(reports: Row[], sessions: Row[]) {
  const projected = new Set(sessions.flatMap(session => session.job_ids || []));
  const unique = new Map<string, Row>();
  for (const report of reports) for (const job of report.standalone_jobs || []) if (job.job_id && !job.report_session_id && !projected.has(job.job_id) && !unique.has(job.job_id)) unique.set(job.job_id, {...job, report_asset_id: report.asset_id, report_title: reportLabel(report)});
  return [...unique.values()];
}

export function standaloneJobsFromCatalog(catalog: Row[], reports: Row[], sessions: Row[]): Row[] {
  const projected = new Set(sessions.flatMap(session => session.job_ids || []));
  const labels = new Map(standaloneJobsFromReports(reports, sessions).map(job => [job.job_id, job]));
  return catalog.filter(job => job.job_id && !job.report_session_id && !projected.has(job.job_id)).map(job => ({...labels.get(job.job_id), ...job, report_title: labels.get(job.job_id)?.report_title || job.document_label || '报告来源待核对'}));
}

export function sessionOutputCounts(session: Row) {
  const request = session.request || {};
  const originals = new Set((request.standards || []).flatMap((standard: Row) =>
    (standard.metric_ids || []).map((metric: string) => `${standard.package_id}:${metric}`)));
  const extensions = new Set((request.extension_selections || []).filter((selection: Row) => selection.extension_id)
    .map((selection: Row) => `${selection.extension_id}@${selection.definition_version || ''}`));
  return {originals: originals.size, extensions: extensions.size, total: originals.size + extensions.size};
}

const ACTIVE = ['running', 'queued'];

/** How far an extraction has got, in the terms an operator uses. */
function sessionProgress(session: Row) {
  const counts = session.counts || {};
  const selected = Number(counts.selected_metrics) || sessionOutputCounts(session).total;
  const done = Math.max(0, selected - (Number(counts.not_executed_metrics) || 0) - (Number(counts.unfinished_metrics) || 0));
  const found = Number(counts.core_facts) || 0;
  return {selected, done, found, ratio: selected ? Math.min(1, done / selected) : 0};
}

export function Extractions({sessionId, standaloneJobId, newTask = false, params}: {sessionId?: string; standaloneJobId?: string; newTask?: boolean; params: URLSearchParams}) {
  const showHistory = !newTask && !sessionId && !standaloneJobId;
  // The legacy catalogue is megabytes; it loads only when the operator opens it.
  const [legacyOpen, setLegacyOpen] = useState(false);
  const list = useResource('document', '/api/report-sessions?include_archived=true&view=summary');
  const reports = useResource('document', showHistory && legacyOpen ? '/api/console/reports?include_archived=true' : null);
  const history = useResource('document', showHistory && legacyOpen ? '/api/console/standalone-jobs?include_archived=false' : null);
  const sessions = items(list.data, 'sessions');
  const standalone = standaloneJobsFromCatalog(items(history.data), items(reports.data), sessions);
  const historyComplete = history.data?.status === 'available';
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">定向数据生产</span>
          <h1>提取任务</h1>
          <p>每份报告一次提取，得到按条款整理的数值、口径与原文证据。</p>
        </div>
        <div className="button-row">
          <button onClick={() => navigate('/extractions/new')}>新建提取</button>
        </div>
      </div>
      {newTask ? <ExtractionCreator params={params}/> : standaloneJobId ? (
        <>
          <Section title="历史独立提取 · 只读成果">
            <Notice>此任务没有父报告会话，可查看原来源与成果，删除条件由后端核查。</Notice>
            <button className="secondary" onClick={() => navigate('/extractions')}>返回提取任务</button>
          </Section>
          <StandaloneManagement jobId={standaloneJobId}/>
          <ProjectionInspection jobId={standaloneJobId}/>
        </>
      ) : sessionId ? (
        <SessionDetail sessionId={sessionId} initialTab={params.get('tab') || undefined}/>
      ) : (
        <>
          <Section title="报告提取会话" action={<BatchRecordDelete rows={endedRecords(sessions)} kind="session" idKey="session_id"/>}>
            <ResourceState error={list.error} loading={list.loading && !list.data} empty={!list.loading && !sessions.length}/>
            <div className="session-list">{sessions.map(s => <SessionRow key={s.session_id} session={s}/>)}</div>
          </Section>
          <Section title="历史独立提取" action={<button className="ghost" onClick={() => setLegacyOpen(!legacyOpen)}>{legacyOpen ? '收起历史' : '读取历史记录'}</button>}>
            {!legacyOpen && <p className="muted">早期没有父报告会话的独立提取任务保存在历史目录中，需要时再读取。</p>}
            {legacyOpen && <ResourceState error={history.error} loading={history.loading && !history.data} empty={historyComplete && !history.loading && !standalone.length}/>}
            {history.data?.status === 'partial' && <Notice>历史目录读取不完整，以下为已确认记录；其余记录尚待读取。</Notice>}
            {history.data?.status === 'unavailable' && <Notice tone="error">历史目录暂不可读，不能据此判断没有历史任务。{history.data.error || history.data.reason}</Notice>}
            {standalone.map(job => (
              <article className="work-item" key={job.job_id}>
                <div>
                  <strong>{job.title || job.disclosure_requirement || job.job_id}</strong>
                  <p>{job.report_title} · {job.disclosure_requirement || job.standard_id || '标准身份见原任务'}</p>
                  {job.diagnostics?.length > 0 && <p>来源或身份待核对，保留原任务供检查。</p>}
                </div>
                <Badge value={job.status}/>
                <button className="secondary" onClick={() => navigate(`/extractions/standalone/${encodeURIComponent(job.job_id)}`)}>只读查看历史成果</button>
                <RecordManagement record={job} kind="job" id={job.job_id} compact/>
              </article>
            ))}
          </Section>
        </>
      )}
    </>
  );
}

/* ==========================================================================
   List
   ========================================================================== */

function SessionRow({session: s}: {session: Row}) {
  const {refresh} = useWorkspace();
  const [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  const counts = sessionOutputCounts(s);
  const progress = sessionProgress(s);
  const actions: Row[] = Array.isArray(s.available_actions) ? s.available_actions : [];
  const resume = actions.find(a => a.action === 'resume' && a.enabled !== false);
  const cancel = actions.find(a => a.action === 'cancel' && a.enabled !== false);
  const active = ACTIVE.includes(String(s.execution_status));
  const stopped = !active && ['failed', 'interrupted', 'cancelled'].includes(String(s.execution_status));
  const reason = String(s.error || '').replace(/^HTTPException:\s*\d+:\s*/, '');
  async function run(action: Row) {
    if (!action.endpoint) return;
    setBusy(true); setMessage('');
    try { await post('document', action.endpoint, {}); refresh(); }
    catch (e) { setMessage((e as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <article className={`session-card ${active ? 'active' : ''} ${stopped ? 'stopped' : ''}`}>
      <div className="session-card-main">
        <div className="session-card-head">
          <strong>{s.name}</strong>
          <div className="session-card-badges">
            <Badge value={s.execution_status}/>
            <Badge value={s.result_status}/>
            {s.database_status && s.database_status !== 'not_imported'
              ? <SessionDeliveryBadge sessionId={s.session_id}/>
              : <span className="muted-chip">未交付</span>}
          </div>
        </div>
        <p className="session-card-meta">
          {s.request?.standards?.length || 0} 个标准包 · {counts.total} 个输出条款
          {counts.extensions > 0 ? `（原条款 ${counts.originals} 项；延伸 ${counts.extensions} 项）` : ''}
        </p>
        <div className="session-meter" aria-hidden="true">
          <span style={{width: `${Math.round(progress.ratio * 100)}%`}}/>
        </div>
        <p className="session-card-meta">
          {progress.selected ? `${progress.done}/${progress.selected} 条已处理 · ${progress.found} 条形成事实` : '等待开始'}
        </p>
        {stopped && reason && <p className="session-stop-reason">已停止：{reason}。成果保留，可继续执行。</p>}
        <ReportReadNotice row={s}/>
        {message && <p className="session-stop-reason">{message}</p>}
      </div>
      <div className="session-card-actions">
        {resume && <button disabled={busy} onClick={() => void run(resume)}>{busy ? '正在继续…' : '继续执行'}</button>}
        {active && cancel && <button className="secondary" disabled={busy} onClick={() => void run(cancel)}>取消</button>}
        <button className="secondary" onClick={() => navigate(`/extractions/${s.session_id}`)}>{active ? '查看进度' : '查看成果'}</button>
        <RecordManagement record={s} kind="session" id={s.session_id} compact/>
      </div>
    </article>
  );
}

/* ==========================================================================
   Creator — three questions, everything else decided for the operator
   ========================================================================== */

const PROFILES = [
  {key: 'priority32', title: '重点条款', badge: '32 条', hint: '环境指标之外，再加能源结构、废弃物、员工与治理；一年报告一次到位。'},
  {key: 'base16', title: '基础指标', badge: '16 条', hint: 'E1–E3 环境核心：能源、温室气体、空气与水污染物、水与资源。'},
] as const;

function ExtractionCreator({params}: {params: URLSearchParams}) {
  const {refresh} = useWorkspace();
  const assets = useResource('document', '/api/report-assets');
  const irRuns = useResource('targeted', '/api/ir-runs');
  const compatibility = useResource('document', '/api/pipeline/compatibility');
  const modelOptions = useResource('document', '/api/pipeline/model-task-options');
  const library = useLibraryResource('report');
  const [inputChoice, setInputChoice] = useState(() => initialExtractionInput(params));
  const [modelPlan, setModelPlan] = useState<ModelPlanSelection>(defaultModelPlan);
  const [scope, setScope] = useState<Scope>({standards: [], originalIds: [], kind: 'custom'});
  const [scopeError, setScopeError] = useState('');
  const [extensionSelections, setExtensionSelections] = useState<Row[]>([]);
  const [extensionError, setExtensionError] = useState('');
  const [provider, setProvider] = useState('local_qwen');
  const [route, setRoute] = useState('foundation16');
  const [metricProfile, setMetricProfile] = useState<'base16' | 'priority32'>('priority32');
  const [model, setModel] = useState('');
  const [completeness, setCompleteness] = useState('balanced');
  const [limited, setLimited] = useState(true);
  const [cloud, setCloud] = useState(false);
  const [cloudOcr, setCloudOcr] = useState(false);
  const [budget, setBudget] = useState<Row>({});
  const [preview, setPreview] = useState<Row[]>();
  const [requests, setRequests] = useState<Row[]>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [results, setResults] = useState<Row[]>([]);

  const foundation = useResource('document', route === 'foundation16' ? `/api/pipeline/extraction-profiles/foundation?profile=${metricProfile}` : null);
  const onExtensions = useCallback((value: Row[], error?: string) => {
    setExtensionSelections(old => JSON.stringify(old) === JSON.stringify(value) ? old : value);
    setExtensionError(error || '');
  }, []);
  const onScope = useCallback((value: Scope, error?: string) => {
    setScope(old => JSON.stringify(old) === JSON.stringify(value) ? old : value);
    setScopeError(error || '');
  }, []);

  useEffect(() => { setPreview(undefined); setRequests(undefined); },
    [inputChoice, modelPlan, scope, scopeError, extensionSelections, extensionError, provider, model, completeness, limited, cloud, cloudOcr, budget, route, metricProfile]);

  // The metric set is the profile; the scope follows it instead of being edited beside it.
  useEffect(() => {
    const data = foundation.data as Row | undefined;
    if (route !== 'foundation16' || !data?.standards) return;
    const digests = data.package_digests || {};
    onScope({
      standards: data.standards,
      originalIds: (data.metrics || []).map((m: Row) => m.code),
      kind: 'foundation',
      parent_snapshots: (data.standards || []).map((s: Row) => ({package_id: s.package_id, package_version: s.package_version, source_digest: digests[s.package_id]})),
      ...(data.selection_snapshot ? {selection_snapshot: data.selection_snapshot} : {}),
    });
  }, [route, metricProfile, foundation.data, onScope]);

  const assetRows = items(assets.data, 'assets');
  const catalogRows = items(irRuns.data, 'runs');
  const selectedAssets = inputChoice.mode === 'batch' ? inputChoice.assetIds : (inputChoice.assetId ? [inputChoice.assetId] : []);
  // History mode submits an existing IR revision, not a PDF asset.
  const submissionUnits = inputChoice.mode === 'history' ? (inputChoice.irId ? [inputChoice.irId] : []) : selectedAssets;

  function build(asset: string): Row {
    if (compatibility.data?.ready !== true) throw new Error('服务合同或当前标准目录尚未就绪，请检查系统运行。');
    if (scopeError) throw new Error(scopeError);
    if (extensionError) throw new Error(extensionError);
    if (!scope.standards.length && !extensionSelections.length) throw new Error('请明确选择输出条款');
    if ((provider === 'qiniu_vlm' || modelPlan.specialist === 'qiniu_vlm' || modelPlan.review === 'qiniu' || modelPlan.ocr === 'paddle_api') && !cloud) throw new Error('使用云模型或云 OCR 需明确授权');
    if (modelPlan.ocr === 'paddle_api' && !cloudOcr) throw new Error('请明确授权云 OCR，或让文字层不可用的页使用本地识别。');
    const input = extractionInput(inputChoice, assetRows, library.data, catalogRows, limited, asset);
    const report = assetRows.find(row => row.asset_id === asset);
    const version = catalogRows.find(row => row.run_id === input.ir_run_id);
    return {
      input, standards: scope.standards, extension_selections: extensionSelections,
      ...(scope.selection_snapshot ? {selection_snapshot: scope.selection_snapshot} : {}),
      name: (report ? reportLabel(report) : version?.document_label || '历史报告证据提取').slice(0, 120),
      role_config: {
        semantic_brain: {provider, model: provider === 'qiniu_vlm' ? model || null : null},
        specialist: {provider: modelPlan.specialist, model: modelPlan.specialist === provider && provider === 'qiniu_vlm' ? model || null : null},
        cloud_authorization: {allowed: cloud, allow_ocr_api: cloudOcr},
      },
      model_plan: requestModelPlan(modelPlan), completeness, allow_limited_evidence: limited, semantic_search: true,
      ...(route === 'foundation16' ? {extraction_route: 'foundation16', metric_profile: metricProfile} : {}),
      ...(Object.keys(budget).length ? {budget} : {}),
    };
  }

  async function runPreview() {
    setBusy(true); setMessage(''); setResults([]);
    const output: Row[] = []; const inputs: Row[] = []; const failures: string[] = [];
    try {
      if (inputChoice.mode === 'batch' && !selectedAssets.length) throw new Error('请至少选择一份报告。');
      // One bad report must not hide the plans that are valid.
      for (const asset of submissionUnits) {
        try {
          const req = build(asset);
          const value = await post('document', '/api/report-sessions/preview', req);
          inputs.push(req);
          output.push({...value, report_asset_id: asset, report_name: req.name});
        } catch (e) { failures.push(`${asset}: ${(e as Error).message}`); }
      }
      setRequests(inputs.length ? inputs : undefined); setPreview(output.length ? output : undefined);
      if (failures.length) setMessage(`有 ${failures.length} 份报告未能生成预览：${failures.join('；')}`);
    } catch (e) { setMessage((e as Error).message); }
    finally { setBusy(false); }
  }

  async function create(e?: FormEvent) {
    e?.preventDefault();
    setBusy(true); setMessage(''); setResults([]);
    const output: Row[] = [];
    try {
      if (inputChoice.mode === 'batch' && !selectedAssets.length) throw new Error('请至少选择一份报告。');
      const inputs = requests?.length ? requests : submissionUnits.map(build);
      for (const input of inputs) {
        try {
          const value = await post('document', '/api/report-sessions', {...input, qiniu_api_key: credentials.secret('qiniu_api_key'), ocr_token: credentials.secret('ocr_token')});
          output.push({report_name: input.name, status: 'queued', session_id: value.session_id});
        } catch (e) { output.push({report_name: input.name, status: 'failed', reason: (e as Error).message}); }
        setResults([...output]);
      }
    } catch (e) { setMessage((e as Error).message); }
    finally {
      setBusy(false); refresh();
      if (output.length === 1 && output[0].session_id) navigate(`/extractions/${output[0].session_id}`);
    }
  }

  const metricCount = foundation.data?.metric_count ?? (metricProfile === 'priority32' ? 32 : 16);
  const blocked = compatibility.data?.ready === false || !!scopeError || !!extensionError || (!scope.standards.length && !extensionSelections.length);
  const profileTitle = PROFILES.find(p => p.key === metricProfile)!.title;

  return (
    <form onSubmit={create} className="creator">
      <ExtractionReportInput choice={inputChoice} onChange={setInputChoice} assets={assetRows} library={library.data} catalog={catalogRows} limited={limited}
        error={assets.error || library.error} loading={assets.loading && !assets.data}/>

      <Section title="2 · 选择指标集">
        <div className="metric-choice" role="radiogroup" aria-label="指标集">
          {PROFILES.map(p => (
            <label key={p.key} data-selected={metricProfile === p.key}>
              <input type="radio" name="metric-profile" value={p.key} checked={metricProfile === p.key} onChange={() => setMetricProfile(p.key)}/>
              <span className="metric-count">{p.key === metricProfile ? `${metricCount} 条` : p.badge}</span>
              <strong>{p.title}</strong>
              <small>{p.hint}</small>
            </label>
          ))}
        </div>
        <details className="advanced-settings">
          <summary>自定义条款范围</summary>
          <p className="muted">默认按上面选中的指标集执行。只有在需要逐包挑选条款时才切换。</p>
          <Field label="提取路线" hint="基础指标集走快速通道；标准路线按条款包逐个提取。">
            <select value={route} onChange={e => setRoute(e.target.value)}>
              <option value="foundation16">按指标集统一提取</option>
              <option value="standard">按标准包逐条提取</option>
            </select>
          </Field>
          {route === 'foundation16'
            ? <div className="chips">{(foundation.data?.metrics || []).map((m: Row) => <span className="clause-chip" key={m.code} title={m.label}>{m.code}</span>)}</div>
            : <ScopeSelector onChange={onScope}/>}
          <div className="chips" style={{maxHeight: 96, overflow: 'auto'}}>
            {scope.originalIds.map(id => <span className="clause-chip" key={id}>{id}</span>)}
          </div>
          <ResourceState error={foundation.error} loading={foundation.loading && !foundation.data}/>
        </details>
        {extensionError && <Notice tone="error">{extensionError}</Notice>}
        {scopeError && <Notice tone="error">{scopeError}</Notice>}
      </Section>

      <details className="advanced-settings">
        <summary>高级设置 · 模型方案、云授权、资源预算</summary>
        <p className="muted">默认使用本机模型与均衡预算，通常无需改动。改变读取方式或需要云服务时才展开。</p>
        <div className="form-grid form-grid-3">
          <Field label="完整度预设">
            <select value={completeness} onChange={e => setCompleteness(e.target.value)}>
              <option value="quick">快速 · 有限探索</option>
              <option value="balanced">均衡 · 发现与补漏</option>
              <option value="thorough">深入 · 更多补漏预算</option>
            </select>
          </Field>
          <Field label="模型方案">
            <select value={modelPlan.preset} onChange={e => {
              const preset = e.target.value as ModelPlanSelection['preset'];
              setModelPlan(preset === 'custom' ? {...modelPlan, preset} : {...defaultModelPlan, preset, specialist: preset === 'mixed' ? 'local_nuextract' : 'local_qwen'});
              if (preset !== 'custom') setProvider('local_qwen');
            }}>
              <option value="qwen">Qwen 统一理解与提取</option>
              <option value="mixed">Qwen 理解 + NuExtract 模板抽取</option>
              <option value="custom">按任务自定义</option>
            </select>
          </Field>
          <Field label="语义主脑">
            <select value={provider} onChange={e => { setProvider(e.target.value); setModelPlan(value => ({...value, preset: 'custom'})); }}>
              <option value="local_qwen">本地 Qwen</option>
              <option value="qiniu_vlm">七牛云视觉模型</option>
            </select>
          </Field>
        </div>
        <ExtractionModelPlan value={modelPlan} onChange={setModelPlan} brain={provider} onPreset={setProvider} options={modelOptions.data} fixedEvidence={inputChoice.mode === 'history' || inputChoice.evidence === 'fixed'} showPreset={false}/>
        {provider === 'qiniu_vlm' && <Field label="云模型名称"><input value={model} onChange={e => setModel(e.target.value)}/></Field>}
        <label className="check"><input type="checkbox" checked={limited} onChange={e => setLimited(e.target.checked)}/>允许可靠局部证据，保留覆盖缺口</label>
        <label className="check"><input type="checkbox" checked={cloud} onChange={e => setCloud(e.target.checked)}/>允许本次报告相关证据发送到所选云服务</label>
        <label className="check"><input type="checkbox" checked={cloudOcr} onChange={e => setCloudOcr(e.target.checked)}/>允许云 OCR 或 API 回退</label>
        <details>
          <summary>资源预算 · 调用、修复与 token 上限</summary>
          <p>调用、修复及 token 上限分别完整应用于每个标准包，不按包数拆分。报告总执行时限覆盖整份报告；单调用时限限制一次模型调用。</p>
          <div className="form-grid">
            {[['retrieval_object_top_n', '检索候选', 1, 5], ['shared_object_budget', '初始共享对象', 1, 8], ['agent_max_steps', '步骤上限', 1, 24], ['total_timeout_seconds', '整份报告总执行时限（秒）', 30, 86400], ['call_timeout_seconds', '单调用时限（秒）', 10, 1800], ['max_model_calls', '每包模型调用上限', 1, 2000], ['max_format_repairs', '每包协议修复上限', 0, 200], ['max_input_tokens', '每包输入 token 上限', 1000, 20000000], ['max_output_tokens', '每包输出 token 上限', 1000, 4000000]].map(([key, title, min, max]) => (
              <Field key={key} label={String(title)}>
                <input type="number" min={Number(min)} max={Number(max)} value={budget[key] ?? ''} placeholder="由完整度预设决定"
                  onChange={e => { const next = {...budget}; e.target.value ? next[key] = Number(e.target.value) : delete next[key]; setBudget(next); }}/>
              </Field>
            ))}
          </div>
        </details>
        <ExtrasSelector onChange={onExtensions} parentSnapshots={scope.parent_snapshots || []}/>
      </details>

      <div className="launch-bar">
        <p>
          将提取 <strong>{PROFILES.find(p => p.key === metricProfile)!.title} {metricCount} 条</strong>
          {submissionUnits.length ? ` · ${submissionUnits.length} ${inputChoice.mode === 'history' ? '个历史结构证据' : '份报告'}` : ' · 尚未选择报告或结构证据'}
          <br/>
          <span className="muted">读取页面、构建结构证据与提取在同一个任务里完成；开始后可在任务页看到逐条进度。</span>
        </p>
        <div className="button-row">
          <button type="button" className="ghost" disabled={busy || blocked} onClick={runPreview}>{busy ? '处理中…' : '预览后端实际计划'}</button>
          <button type="submit" disabled={busy || blocked || !submissionUnits.length}>{busy ? '正在提交…' : `开始提取${submissionUnits.length > 1 ? `（${submissionUnits.length} ${inputChoice.mode === 'history' ? '个证据' : '份'}）` : ''}`}</button>
        </div>
      </div>

      <ResourceState error={compatibility.error || irRuns.error || modelOptions.error || foundation.error}/>
      {compatibility.data?.ready === false && <Notice tone="error">{compatibility.data.blockers?.join('；') || '当前服务业务未就绪'}</Notice>}
      {message && <Notice tone="error">{message}</Notice>}
      {preview?.map(p => <PlanPreview key={p.report_asset_id || p.report_name} plan={p}/>)}
      {requests?.length ? (
        <div className="launch-bar">
          <p>已确认 {requests.length} 份报告的执行计划。</p>
          <button type="submit" disabled={busy}>{busy ? '提交中…' : `确认已预览计划并加入队列${requests.length > 1 ? `（${requests.length} 份）` : ''}`}</button>
        </div>
      ) : null}
      {results.length > 0 && (
        <Section title="逐报告提交结果">
          {results.map((r, i) => (
            <article className="work-item" key={i}>
              <div><strong>{r.report_name}</strong>{r.reason && <p>{r.reason}</p>}</div>
              <Badge value={r.status}/>
              {r.session_id && <button type="button" className="secondary" onClick={() => navigate(`/extractions/${r.session_id}`)}>打开任务</button>}
            </article>
          ))}
        </Section>
      )}
    </form>
  );
}

// Extensions stay available; they are a separate axis from the metric set.
function ExtrasSelector({onChange, parentSnapshots}: {onChange: (rows: Row[], error?: string) => void; parentSnapshots: Scope['parent_snapshots']}) {
  return (
    <details>
      <summary>项目延伸（可选）</summary>
      <ExtensionSelector onChange={onChange} parentSnapshots={parentSnapshots}/>
    </details>
  );
}

/* ==========================================================================
   Plans
   ========================================================================== */

function PreparedPlan({plan}: {plan: Row}) {
  const preparation = plan.document_preparation || {}, roles = plan.roles || {};
  const label = (role: Row) => role.model || (({local_qwen: '本地 Qwen', local_nuextract: '本地 NuExtract', qiniu_vlm: '所选云视觉模型'} as Record<string, string>)[role.provider] || '等待后端确认');
  return (
    <div className="prepared-plan">
      <h3>后端确认的读取方式与模型</h3>
      <p>{preparationText(preparation)}</p>
      <div className="chips">
        <span>理解与事实解释：{label(roles.semantic_brain || {})}</span>
        <span>模板抽取：{label(roles.specialist || {})}</span>
        {!preparation.ir_reused && <span>文字层不可用页：{preparation.ocr_provider === 'paddle_api' ? '云端 ' : ''}{preparation.ocr_model || 'PaddleOCR-VL'}</span>}
      </div>
      {!preparation.ir_reused && <p className="muted">必要模型复核：{preparation.required_reviews ? '开启' : '关闭'} · 额外视觉复核：{preparation.optional_reviews ? '开启' : '关闭'}</p>}
    </div>
  );
}

export function PlanPreview({plan}: {plan: Row}) {
  const clauses = (rows: Row[]) => rows.map((c, i) => <li key={c.metric_id || i}><strong>{c.source_datapoint_id || (c.clause_kind === 'project_extension' ? '项目延伸' : c.metric_id)}</strong> {c.label || c.description}</li>);
  return (
    <Section title={`实际计划 · ${plan.report_name}`}>
      <PreparedPlan plan={plan}/>
      <div className="two-columns">
        <div>
          <h3>所选输出（{plan.output_count ?? plan.selected_outputs?.length ?? '以合同为准'}）</h3>
          <ul>{clauses(plan.selected_outputs || [])}</ul>
          {plan.extension_output_count > 0 && <Notice>原条款 {plan.original_output_count} 项；项目延伸 {plan.extension_output_count} 项；内部操作数 {plan.internal_operand_count} 项。内部依赖不计入所选输出。</Notice>}
          <h3>必要身份与方法</h3>
          <ul>{(plan.identity_requirements || []).map((r: Row, i: number) => <li key={i}><strong>{r.source_datapoint_id}</strong>：{r.identity_axes?.join('、')}</li>)}</ul>
        </div>
        <div>
          <h3>共用背景（不扩大输出）</h3>
          <ul>{(plan.context_requirements || []).map((r: Row, i: number) => <li key={i}>{r.description}<ul>{clauses(r.clauses || [])}</ul></li>)}</ul>
          <h3>内部计算依赖</h3>
          <ul>{(plan.internal_dependencies || []).map((r: Row, i: number) => <li key={i}>{r.package_id}：{r.metric_ids?.join('、') || '无额外依赖'}</li>)}</ul>
          <h3>证据限制</h3>
          <ul>{(plan.evidence_limitations || []).map((r: Row, i: number) => <li key={i}>{r.message || r.reason}</li>)}</ul>
        </div>
      </div>
      <details><summary>计算操作数与排除条件</summary><Diagnostics value={{formula_operands: plan.formula_operands, exclusions: plan.exclusions}}/></details>
      <Diagnostics value={plan} title="角色、预算、输入身份与冻结计划"/>
    </Section>
  );
}

/* ==========================================================================
   Detail
   ========================================================================== */

function SessionDetail({sessionId, initialTab}: {sessionId: string; initialTab?: string}) {
  const resource = useResource('document', `/api/report-sessions/${encodeURIComponent(sessionId)}`);
  const facts = useResource('document', `/api/report-sessions/${encodeURIComponent(sessionId)}/facts`);
  const [tab, setTab] = useState(initialTab || 'results');
  const [selectedFact, setSelectedFact] = useState<Row>();
  const [deliverySelection, setDeliverySelection] = useState({sessionId, reportId: ''});
  const deliveryReportId = deliverySelection.sessionId === sessionId ? deliverySelection.reportId : '';
  const setDeliveryReportId = (reportId: string) => setDeliverySelection({sessionId, reportId});
  const deliveryState = useDeliveryStatus(sessionId, deliveryReportId || undefined);
  const session = resource.data?.session || resource.data;
  const foundation = session?.request?.extraction_route === 'foundation16';
  const defaultTab = initialTab || (foundation ? 'foundation' : 'results');
  useEffect(() => { setTab(defaultTab); setSelectedFact(undefined); }, [sessionId, defaultTab]);

  const tabs: [string, string][] = [
    ...(foundation ? [['foundation', '指标成果'] as [string, string]] : []),
    ['results', foundation ? '分包成果' : '条款成果'],
    ['facts', '来源事实'],
    ['progress', '阶段与运行'],
    ['deliver', '交付审核'],
  ];
  return (
    <>
      <ResourceState error={resource.error} loading={resource.loading && !session}/>
      {session && (
        <>
          <Section title={session.name} action={<Badge value={session.execution_status}/>}>
            <div className="outcome-grid">
              <div><small>执行</small><Badge value={session.execution_status}/></div>
              <div><small>成果</small><Badge value={session.result_status}/></div>
              <div><small>数据库存档</small><DeliveryStatusBadge data={deliveryState.data} error={deliveryState.error} loading={deliveryState.loading}/></div>
            </div>
            {session.error && <Notice tone="error">{session.error}{session.read_error?.request_id && <span> · 问题编号 {session.read_error.request_id}</span>}</Notice>}
            {session.state_notice && <ReportReadNotice row={session}/>}
            <Actions actions={workActions(session.available_actions)} revision={session.revision}/>
            <details className="session-more">
              <summary>记录管理</summary>
              <RecordManagement record={session} kind="session" id={sessionId}/>
            </details>
          </Section>
          <div className="tabs" role="tablist">
            {tabs.map(([key, title]) => <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{title}</button>)}
          </div>
          {tab === 'foundation' && <Foundation16Board sessionId={sessionId} assetId={session?.request?.input?.asset_id}/>}
          {tab === 'results' && (
            <>
              <details className="scope-summary">
                <summary>本次固定的输出范围（原版本，不随标准更新改变）</summary>
                <SelectedScope session={session}/>
              </details>
              {session.job_ids?.map((id: string) => <ProjectionInspection key={id} jobId={id}/>)}
            </>
          )}
          {tab === 'facts' && (
            <Section title="来源事实（候选，不等同于条款映射）">
              <ResourceState error={facts.error} loading={facts.loading && !facts.data}/>
              <div className="table-scroll">
                <table>
                  <thead><tr><th>来源名称</th><th>报告原值</th><th>单位</th><th>主体与期间</th><th>证据</th></tr></thead>
                  <tbody>
                    {(facts.data?.facts || []).map((f: Row) => (
                      <tr key={f.source_fact_id}>
                        <td>{f.source_row_label || f.identity_raw?.indicator_name || '正文陈述'}</td>
                        <td className="metric-value">{f.value_raw ?? f.statement_raw}</td>
                        <td>{f.unit_raw || '未记录'}</td>
                        <td>{f.identity_raw?.reporting_entity || f.semantics?.reporting_entity || '主体待核对'}<small className="table-secondary">{f.identity_raw?.reporting_period || f.semantics?.reporting_period || '期间待核对'}</small></td>
                        <td><button className="text-button" onClick={() => setSelectedFact(f)}>核对原文</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {selectedFact && (
                <Section title="所选事实来源">
                  <p className="raw-text">{selectedFact.value_raw ?? selectedFact.statement_raw}</p>
                  <ul>{selectedFact.physical_sources?.map((s: Row, i: number) => <li key={i}>PDF 第 {s.pdf_page_index + 1} 页 · <button className="text-button" onClick={() => navigate(`/processing?ir=${selectedFact.ir_run_id}&page=${s.pdf_page_index}&object=${encodeURIComponent(s.ir_object_id)}`)}>定位原文对象</button></li>)}</ul>
                  <Diagnostics value={selectedFact.identity_raw} title="原始身份及推断边界"/>
                </Section>
              )}
            </Section>
          )}
          {tab === 'progress' && (
            <>
              <Section title="运行进度"><PipelineProgress session={session}/></Section>
              <Section title="剩余工作与证据缺口">
                <ResourceState error={facts.error}/>
                {(facts.data?.gaps || session.gaps || []).map((g: Row, i: number) => (
                  <article className="gap-card" key={g.gap_id || i}>
                    <strong>{g.kind}</strong><p>{g.reason || g.message}</p>
                    <p>影响条款：{g.metric_ids?.join('、') || '见对象范围'}</p>
                    <p>保留对象：{g.object_ids?.join('、') || g.object_id || '见详情'}</p>
                    <Diagnostics value={g} title="下一步及受影响范围"/>
                  </article>
                ))}
                {!(facts.data?.gaps || session.gaps || []).length && <Notice>接口未报告剩余缺口。请同时检查所选条款处理状态及事实、映射、发布条件。</Notice>}
              </Section>
              <Section title="实际阶段与诊断">
                <PackageBudgetUsage budget={session.model_budget || session.native?.runtime_budget}/>
                {(session.preparation_stages || []).map((s: Row, i: number) => (
                  <div className="task-card" key={s.task_id || i}><Progress task={s}/>{s.error_detail && <Notice tone="error">{s.error_detail.message || s.error}</Notice>}</div>
                ))}
                {(session.stages || []).map((s: Row, i: number) => (
                  <div className="task-card" key={s.job_id || i}>
                    <h3>{s.disclosure_requirement || s.label || s.name || '投影任务'}</h3>
                    {s.job_id && <p className="mono">{s.job_id}</p>}
                    <Progress task={s}/>
                    {s.result_status && <p>成果：<Badge value={s.result_status}/></p>}
                    {s.counts && <dl className="facts"><dt>所选条款</dt><dd>{s.counts.selected_metrics ?? '未提供'}</dd><dt>已有处理记录</dt><dd>{s.counts.processed_metrics ?? '未提供'}</dd><dt>候选来源事实</dt><dd>{s.counts.candidate_source_facts ?? '未提供'}</dd><dt>已保存规范事实</dt><dd>{s.counts.core_facts ?? '未提供'}</dd></dl>}
                  </div>
                ))}
                <Diagnostics value={{execution_budget: session.execution_budget, model_budget: session.model_budget || session.native?.runtime_budget, error_detail: session.error_detail, events: session.events, plan: session.plan}} title="资源、错误归属和冻结运行计划"/>
              </Section>
            </>
          )}
          {tab === 'deliver' && <PublishFlow session={session} reportId={deliveryReportId} onReportId={setDeliveryReportId} deliveryState={deliveryState}/>}
        </>
      )}
    </>
  );
}

export function DeliveryInventory({session}: {session: Row}) {
  const counts = session.delivery_counts, artifacts = session.delivery_artifacts;
  if (!counts && !Array.isArray(artifacts)) return <Notice>此历史接口尚未返回可交付版本清单，交付结果以正式回执为准。</Notice>;
  return (
    <div aria-label="已冻结交付成果">
      <dl className="facts">
        <dt>可交付来源事实</dt><dd>{counts?.saved_source_facts ?? '未核对'}</dd>
        <dt>已保存规范事实</dt><dd>{counts?.saved_core_facts ?? '未核对'}</dd>
        <dt>已有规范清单</dt><dd>{counts?.available_bundle_count ?? '未核对'}</dd>
      </dl>
      <Notice>已保存来源可以进入中立存档。范围处理、来源质量、标准映射、审核与发布分别核对；规范事实为零也可以保留有来源的阶段成果。</Notice>
      {artifacts?.map((a: Row) => (
        <article className="work-item" key={`${a.job_id}:${a.artifact_revision_id}`}>
          <div><strong>{a.artifact_revision_id === 'original' ? '原始已保存成果' : '失败或中断后的阶段成果'}</strong><p>来源 {a.source_fact_count} 条 · 规范事实 {a.core_fact_count} 条</p></div>
          <Badge value={a.status}/>
          <Diagnostics value={a} title="固定成果身份、质量及来源指纹"/>
        </article>
      ))}
      {session.delivery_artifact_blockers?.map((b: Row, i: number) => <Notice tone="error" key={i}>成果版本不可交付：{b.reason}</Notice>)}
    </div>
  );
}

function SelectedScope({session}: {session: Row}) {
  return (
    <Section title="会话固定输出（原版本）">
      {(session.request?.standards || []).map((s: Row) => <ScopePackage key={s.package_id} selected={s}/>)}
      {session.request?.extension_selections?.map((selection: Row) => (
        <article className="task-card" key={selection.extension_id}>
          <h3>项目延伸 · {selection.extension_id}</h3>
          <p>定义版本 {selection.definition_version}；{selection.value_origins?.includes('reported') ? '读取报告直接披露' : '不读取直接披露'}。</p>
          <p>计算规则：{selection.derivation_rule_ids?.join('、') || '未选择计算规则'}。</p>
          <Diagnostics value={selection} title="冻结延伸身份与所选路径"/>
        </article>
      ))}
      <Diagnostics value={{selection_snapshot: session.request?.selection_snapshot, input: session.request?.input, plan: session.plan?.input_identity}} title="范围、原编号与来源快照"/>
    </Section>
  );
}

function ScopePackage({selected}: {selected: Row}) {
  const resource = useResource('document', `/api/pipeline/standards/${selected.package_id}/${selected.package_version}`);
  return (
    <div>
      <h3>{selected.package_id} · v{selected.package_version}</h3>
      <ResourceState error={resource.error}/>
      {resource.data?.metrics
        ? <ul>{resource.data.metrics.filter((m: Row) => selected.metric_ids.includes(m.metric_id)).map((m: Row) => <li key={m.metric_id}><strong>{m.source_datapoint_id}</strong> {m.labels?.zh || m.labels?.en}</li>)}</ul>
        : <Notice>保留所选 {selected.metric_ids.length} 个稳定条款身份；详细原编号待原版本读取，不能改用当前版本替代。</Notice>}
    </div>
  );
}

export function ProjectionInspection({jobId}: {jobId: string}) {
  const resource = useResource('targeted', `/api/jobs/${encodeURIComponent(jobId)}/inspection`);
  const [selected, setSelected] = useState<Row>();
  return (
    <Section title={resource.data?.standard?.disclosure_requirement || '条款成果'}>
      <ResourceState error={resource.error} loading={resource.loading && !resource.data}/>
      {resource.data && (
        <>
          <Notice>范围已处理、事实可审核、数据正式发布是独立结果。未提取、未披露、不适用和证据不可读分别保留。</Notice>
          <div className="table-scroll">
            <table>
              <thead><tr><th>原编号 / 条款</th><th>处理</th><th>来源结果</th><th>事实</th><th>审核</th><th>操作</th></tr></thead>
              <tbody>
                {resource.data.metrics?.map((m: Row) => (
                  <tr key={m.metric_id}>
                    <td><strong>{m.source_datapoint_id}</strong><small className="table-secondary">{m.labels?.zh || m.labels?.en}</small></td>
                    <td><Badge value={m.processing_status}/></td>
                    <td><Badge value={m.found_status}/></td>
                    <td>{m.facts?.length || 0}</td>
                    <td><Badge value={m.review_status}/></td>
                    <td><button className="secondary" onClick={() => setSelected(m)}>原值与证据</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selected && (
            <div className="inspection-detail">
              <h3>{selected.source_datapoint_id} · {selected.labels?.zh}</h3>
              {selected.uncertainty_reason && <Notice>{selected.uncertainty_reason}</Notice>}
              {selected.facts?.map((f: Row) => (
                <article key={f.fact_id} className="task-card">
                  <MetricValue value={f.value_raw ?? f.statement_raw ?? f.display_value} unit={f.unit_raw}/>
                  <dl className="facts">
                    <dt>主体</dt><dd>{f.organization?.name || f.organization?.entity_ref || '待核对'}</dd>
                    <dt>期间</dt><dd>{f.period_start && f.period_end ? `${f.period_start} — ${f.period_end}` : f.reporting_period_raw || '待核对'}</dd>
                    <dt>边界</dt><dd>{f.reporting_boundary || '待核对'}</dd>
                    <dt>来源</dt><dd>{f.source_kind === 'text' ? '正文' : f.source_kind === 'ir_table' ? '结构化表格' : f.source_kind === 'visual_table' ? '视觉表格' : '待核对'}</dd>
                  </dl>
                  {f.evidence?.map((e: Row) => (
                    <blockquote key={e.evidence_id}>
                      {e.excerpt}
                      <footer>PDF 第 {e.pdf_page_number} 页 · <button className="text-button" onClick={() => navigate(`/processing?ir=${e.ir_run_id}&page=${e.pdf_page_index}&object=${encodeURIComponent(e.ir_object_id)}`)}>定位原文</button></footer>
                    </blockquote>
                  ))}
                  {f.quality_issues?.map((q: Row, i: number) => <Notice tone="error" key={i}>{q.message}</Notice>)}
                  <Diagnostics value={{dimensions: f.dimensions, attributes: f.attributes, element_cells: f.element_cells}} title="身份、方法及合同字段"/>
                </article>
              ))}
              {!selected.facts?.length && <Notice>本条款尚未形成可映射事实。{selected.contract_error || selected.uncertainty_reason}</Notice>}
              <Diagnostics value={{gaps: selected.gaps, dependency_blocks: selected.dependency_blocks, unmapped_measurements: selected.unmapped_measurements}} title="缺口与未映射来源"/>
            </div>
          )}
        </>
      )}
    </Section>
  );
}

function StandaloneManagement({jobId}: {jobId: string}) {
  const record = useResource('document', `/api/jobs/${encodeURIComponent(jobId)}/lifecycle`);
  return (
    <Section title="历史任务记录管理">
      <ResourceState error={record.error} loading={record.loading}/>
      {record.data && <RecordManagement record={{lifecycle: record.data, available_actions: record.data.available_actions}} kind="job" id={jobId}/>}
    </Section>
  );
}
