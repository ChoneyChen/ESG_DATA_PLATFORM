import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {credentials, items, navigate, post, request, type Row} from '../api';
import {useResource, useWorkspace} from '../state';
import {Badge, Field, Notice, ResourceState, Section} from './ui';
import {DeliveryStatusPanel, useDeliveryStatus} from './DeliveryStatus';

/**
 * The last mile: a finished extraction becomes a published fact.
 *
 * Three things must be true before the platform will accept the data, and each
 * one is a real check rather than a form the operator fills for its own sake:
 * the PDF must have a confirmed business identity, the publisher must be a
 * registered entity, and the session must hold frozen result bundles. The panel
 * shows exactly which of the three is missing and fixes it in place.
 */

const KINDS: [string, string][] = [['listed_company', '上市公司'], ['group', '集团'], ['subsidiary', '子公司'], ['other', '其他']];

function slugOf(name: string) {
  return `org:${name.trim().toLowerCase().replace(/[^a-z0-9一-龥]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'entity'}`;
}

function Step({index, title, state, children}: {index: number; title: string; state: 'done' | 'current' | 'todo' | 'blocked'; children: ReactNode}) {
  return (
    <section className={`publish-step ${state}`}>
      <header>
        <span className="publish-step-index">{state === 'done' ? '✓' : index}</span>
        <strong>{title}</strong>
      </header>
      <div className="publish-step-body">{children}</div>
    </section>
  );
}

export function PublishFlow({session, reportId, onReportId, deliveryState}: {
  session: Row;
  reportId: string;
  onReportId: (value: string) => void;
  deliveryState: ReturnType<typeof useDeliveryStatus>;
}) {
  const {refresh} = useWorkspace();
  const assetId: string = session?.request?.input?.asset_id || '';
  const reports = useResource('document', '/api/reports');
  // The engine proposes what it read from the document; the operator confirms.
  const identity = useResource('document', session?.session_id
    ? `/api/report-sessions/${encodeURIComponent(session.session_id)}/report-identity` : null);
  const entities = useResource('publication', credentials.ready ? '/internal/v1/entities' : null, true);
  const [entityRef, setEntityRef] = useState('');
  const [publisher, setPublisher] = useState('');
  const [title, setTitle] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [kind, setKind] = useState('listed_company');
  const [industry, setIndustry] = useState('');
  const [reason, setReason] = useState('核对报告身份与原文后交付已保存的提取成果');
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [result, setResult] = useState<Row>();

  const registered = useMemo(() => {
    const rows = items(reports.data, 'reports');
    return rows.find(report => report.report_id === reportId)
      || rows.find(report => (report.asset_versions || []).some((version: Row) => version.asset_id === assetId));
  }, [reports.data, assetId, reportId]);

  // Reports bound to this PDF come first; a session without a fixed asset
  // falls back to every registered report rather than hiding the choice.
  const reportOptions = useMemo(() => {
    const rows = items(reports.data, 'reports');
    const bound = rows.filter(report => (report.asset_versions || []).some((version: Row) => version.asset_id === assetId));
    return bound.length ? bound : rows;
  }, [reports.data, assetId]);

  // Prefill from what extraction already knows; never invent a publisher.
  useEffect(() => {
    if (!registered) return;
    const meta = registered.metadata || {};
    const proposed = (identity.data as Row | undefined)?.proposal || {};
    setTitle(meta.title || proposed.title || session?.name || '');
    setPublisher(meta.publisher_name || meta.publisher_entity_ref || '');
    setEntityRef(meta.publisher_entity_ref || '');
    setStart(String(meta.period_start || proposed.period_start || '').slice(0, 10));
    setEnd(String(meta.period_end || proposed.period_end || '').slice(0, 10));
    onReportId(registered.report_id);
  }, [registered?.report_id, identity.data]);

  const identityPeriod = ((identity.data as Row | undefined)?.evidence as Row | undefined)?.period_detail as
    { basis?: string; evidence?: string; weak?: boolean } | undefined;
  const identityReady = !!(title && publisher && entityRef && start && end);
  const entityList = items(entities.data);
  // An unavailable entity list proves nothing; only a list that loaded without
  // this reference is a real missing registration. The backend still enforces
  // the registration on delivery.
  const entityKnown = !entities.data || entityList.some(entity => entity.entity_ref === entityRef);
  const artifacts: Row[] = session?.delivery_artifacts || [];
  const availableArtifacts = artifacts.filter(a => a.status === 'available').length;
  // Older sessions report the count without the per-artifact list.
  const deliverable = availableArtifacts || Number(session?.delivery_counts?.available_bundle_count) || 0;
  // A session that lists no artifacts cannot be judged here; the backend decides.
  const canDeliver = deliverable > 0 || artifacts.length === 0;
  const delivered = deliveryState.data?.jobs?.filter((job: Row) => job.status === 'imported').length || 0;
  const allImported = artifacts.length > 0 && delivered >= artifacts.length;

  async function saveIdentity() {
    if (!assetId) return;
    setBusy('identity'); setMessage('');
    try {
      let report: Row | undefined = registered;
      if (!report) {
        const created = await post('document', '/api/reports', {asset_id: assetId});
        report = created?.report || created;
      }
      if (!report) throw new Error('报告登记未返回有效身份，请刷新后重试。');
      const metadata = {
        title, publisher_name: publisher, publisher_entity_ref: entityRef,
        period_start: start, period_end: end, language: 'zh', report_kind: 'sustainability',
      };
      const updated = await request('document', `/api/reports/${encodeURIComponent(report.report_id)}`, {
        method: 'PATCH', body: JSON.stringify({expected_revision: report.revision, metadata}),
      });
      onReportId((updated as Row).report_id || report.report_id);
      setMessage('报告身份已保存。');
      refresh();
    } catch (e) { setMessage((e as Error).message); }
    finally { setBusy(''); }
  }

  async function registerEntity() {
    if (!publisher.trim()) { setMessage('请先填写发布主体名称。'); return; }
    setBusy('entity'); setMessage('');
    try {
      await post('publication', '/internal/v1/entities', {
        entity_ref: entityRef || slugOf(publisher), name_zh: publisher, entity_kind: kind,
        industry_code: industry || 'unknown', country_code: 'CN', is_demo: false,
        metadata: {source: 'console', registered_from_report: title || null},
      });
      if (!entityRef) setEntityRef(slugOf(publisher));
      setMessage('发布主体已登记。');
      refresh();
    } catch (e) { setMessage((e as Error).message); }
    finally { setBusy(''); }
  }

  async function deliver() {
    if (!registered || !identityReady) return;
    setBusy('deliver'); setMessage('');
    try {
      const value = await post('document', `/api/console/report-sessions/${encodeURIComponent(session.session_id)}/deliver`, {
        report_id: registered.report_id,
        expected_revision: registered.revision,
        organization_ref: entityRef,
        reason,
      });
      setResult(value);
      refresh();
    } catch (e) { setMessage((e as Error).message); }
    finally { setBusy(''); }
  }

  const imported = (result?.deliveries || []).filter((d: Row) => d.status === 'imported').length;
  const failed = (result?.deliveries || []).filter((d: Row) => d.status !== 'imported');

  return (
    <>
      <Section title="从提取成果到正式数据">
        <div className="publish-rail">
          <span className={identityReady ? 'done' : 'current'}>1 报告身份</span>
          <span className={entityKnown ? 'done' : identityReady ? 'current' : 'todo'}>2 发布主体</span>
          <span className={allImported ? 'done' : imported || delivered ? 'current' : 'todo'}>3 交付候选</span>
          <span className={allImported ? 'current' : 'todo'}>4 审核发布</span>
        </div>
        <Notice>
          交付只把来源事实与标准映射存进平台候选池；事实、映射与测量口径仍要分别审核，发布后才会出现在公开数据网站。
        </Notice>
        {!credentials.ready && <Notice tone="error">数据平台未连接，交付与发布暂不可用。</Notice>}
        <ResourceState error={reports.error || entities.error}/>
      </Section>

      <Step index={1} title="报告身份" state={identityReady ? 'done' : 'current'}>
        <p className="muted">文件名不是身份。这里的标题、发布者与报告期会写进每条已发布数据的来源记录。</p>
        {identityPeriod?.weak && (
          <Notice tone="warning">
            报告期是从{identityPeriod.basis === 'report_file_label' ? '文件名' : '表头年份'}推断的
            （{identityPeriod.evidence || '无原文依据'}），请对照封面或“报告范围”确认后再保存。
          </Notice>
        )}
        <Field label="已明确登记的报告身份" hint="同一份 PDF 可能有多条报告身份；选择与本次固定来源一致的一条。">
          <select aria-label="已明确登记的报告身份" value={registered?.report_id || ''} onChange={e => onReportId(e.target.value)}>
            <option value="">请选择与固定 PDF 一致的报告</option>
            {reportOptions.map((report: Row) => (
              <option key={report.report_id} value={report.report_id}>
                {(report.metadata?.title || '名称待确认')} · {(report.metadata?.publisher_name || '发布者待确认')} · {(report.metadata?.reporting_year || String(report.metadata?.period_start || '').slice(0, 4) || '报告期待确认')}
              </option>
            ))}
          </select>
        </Field>
        <div className="form-grid">
          <Field label="报告标题"><input value={title} onChange={e => setTitle(e.target.value)} placeholder="与封面一致"/></Field>
          <Field label="发布者名称"><input value={publisher} onChange={e => setPublisher(e.target.value)} placeholder="如：厦门法拉电子股份有限公司"/></Field>
          <Field label="报告期开始"><input type="date" value={start} onChange={e => setStart(e.target.value)}/></Field>
          <Field label="报告期结束"><input type="date" value={end} onChange={e => setEnd(e.target.value)}/></Field>
          <Field label="发布主体引用" hint="长期稳定的主体标识，作为平台内的企业身份。">
            <input value={entityRef} onChange={e => setEntityRef(e.target.value)} placeholder={publisher ? slugOf(publisher) : 'org:…'}/>
          </Field>
          <Field label="主体类型">
            <select value={kind} onChange={e => setKind(e.target.value)}>{KINDS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select>
          </Field>
        </div>
        <Field label="行业代码（可选）"><input value={industry} onChange={e => setIndustry(e.target.value)} placeholder="如 C3981"/></Field>
        <div className="button-row">
          <button type="button" className="secondary" disabled={!!busy || !identityReady} onClick={() => void saveIdentity()}>
            {busy === 'identity' ? '保存中…' : registered ? '保存报告身份' : '登记报告身份'}
          </button>
          {registered && <small className="muted">报告编号 <span className="mono">{registered.report_id}</span> · 修订 {registered.revision}</small>}
        </div>
      </Step>

      <Step index={2} title="发布主体" state={entityKnown ? 'done' : identityReady ? 'current' : 'todo'}>
        {entityKnown
          ? <p className="muted">平台已登记该主体：{entityList.find(entity => entity.entity_ref === entityRef)?.name_zh}。测量主体必须与报告发布者一致，集团与子公司不按名称合并。</p>
          : <>
              <p className="muted">这个主体还没有在平台登记。登记一次即可复用于该企业的所有报告。</p>
              <div className="button-row">
                <button type="button" disabled={!!busy || !publisher.trim()} onClick={() => void registerEntity()}>
                  {busy === 'entity' ? '登记中…' : `登记主体「${publisher || '…'}」`}
                </button>
              </div>
            </>}
      </Step>

      <Step index={3} title="交付候选" state={allImported ? 'done' : identityReady && entityKnown ? 'current' : 'todo'}>
        <dl className="facts">
          <dt>可交付成果包</dt><dd>{deliverable}{artifacts.length ? ` / ${artifacts.length}` : ''}</dd>
          <dt>已存档的包</dt><dd>{delivered || '尚未核对'}</dd>
        </dl>
        <p className="muted">每个成果包对应一个条款包，包含来源事实、标准映射与证据位置；交付不改写原值。</p>
        <Field label="交付依据"><textarea value={reason} onChange={e => setReason(e.target.value)}/></Field>
        <div className="button-row">
          <button type="button" disabled={!!busy || !identityReady || !canDeliver} onClick={() => void deliver()}>
            {busy === 'deliver' ? '交付中…' : '核对并交付候选'}
          </button>
          <small className="muted">{deliverable ? `可交付 ${deliverable} 个成果包` : artifacts.length ? '本次会话还没有可交付的冻结成果包。' : '后端将核对本会话的成果包。'}</small>
        </div>
        {result && (
          <div className="delivery-result">
            <Notice tone={failed.length ? 'warning' : 'success'}>
              已导入 {imported} 个成果包{failed.length ? `，${failed.length} 个未通过并保留了原因` : ''}。
            </Notice>
            {(result.deliveries || []).map((d: Row, i: number) => (
              <article className="work-item" key={i}>
                <div>
                  <strong className="mono">{d.job_id}</strong>
                  {d.status === 'imported'
                    ? <p>已保存来源 {d.source_fact_snapshots ?? 0} 条 · 标准候选 {d.imported ?? 0} 条。正式发布仍须独立审核。
                        {d.receipt_id && <span className="mono">{d.receipt_id}</span>}</p>
                    : <p>{d.detail?.message || d.reason || d.status}</p>}
                </div>
                <Badge value={d.status}/>
              </article>
            ))}
          </div>
        )}
        {message && <Notice>{message}</Notice>}
      </Step>

      <Step index={4} title="审核与发布" state={allImported || imported ? 'current' : 'todo'}>
        <DeliveryStatusPanel data={deliveryState.data} error={deliveryState.error} loading={deliveryState.loading}/>
        <div className="button-row">
          <button type="button" className="secondary" disabled={!registered} onClick={() => navigate(`/review?report=${encodeURIComponent(registered?.report_id || '')}`)}>审核这份报告的候选</button>
          <button type="button" className="secondary" onClick={() => navigate('/publication')}>打开发布中心</button>
          <a href="http://127.0.0.1:8765/" target="_blank" rel="noreferrer">查看数据网站 ↗</a>
        </div>
      </Step>
    </>
  );
}
