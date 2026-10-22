import {Badge} from './ui';
import {type Row} from '../api';

/**
 * One bar for the whole run.
 *
 * A session used to show a progress bar per task, so the operator had to add
 * them up mentally. This renders the pipeline itself — read the pages, build
 * the evidence version, extract each clause package, collect the result — as
 * a single ordered track with one overall number and the current action.
 */
const DONE = new Set(['completed', 'done', 'reused', 'finished']);
const FAILED = new Set(['failed', 'interrupted', 'cancelled', 'error']);

function statusOf(row: Row): 'done' | 'active' | 'failed' | 'pending' {
  const status = String(row.status || row.execution_status || row.result_status || '').toLowerCase();
  if (DONE.has(status)) return 'done';
  if (FAILED.has(status)) return 'failed';
  if (['running', 'queued', 'active', 'stopping', 'cancelling'].includes(status)) return 'active';
  return 'pending';
}

export type PipelineStep = {key: string; label: string; status: 'done' | 'active' | 'failed' | 'pending'; detail?: string};

export function pipelineSteps(session: Row): PipelineStep[] {
  const preparation: Row[] = Array.isArray(session.preparation_stages) ? session.preparation_stages : [];
  const stages: Row[] = Array.isArray(session.stages) ? session.stages : [];
  const reading = preparation.filter(step => step.task_id);
  const readingStatus: PipelineStep['status'] = reading.length
    ? reading.some(step => statusOf(step) === 'failed') ? 'failed'
      : reading.every(step => statusOf(step) === 'done') ? 'done'
        : reading.some(step => statusOf(step) === 'active') ? 'active' : 'pending'
    : 'pending';
  const steps: PipelineStep[] = [
    {key: 'reading', label: '读取页面', status: readingStatus,
      detail: reading.length ? `${reading.filter(step => statusOf(step) === 'done').length} / ${reading.length} 个阶段` : '文字层直读，不可用页才识别'},
  ];
  if (stages.length) {
    for (const stage of stages) {
      steps.push({
        key: String(stage.job_id || stage.disclosure_requirement || stage.label || 'stage'),
        label: String(stage.disclosure_requirement || stage.label || stage.name || '提取'),
        status: statusOf(stage),
        detail: stage.result_status ? `成果 ${stage.result_status}` : undefined,
      });
    }
  } else {
    steps.push({key: 'extraction', label: '提取指标', status: 'pending', detail: '读取完成后按所选条款提取'});
  }
  steps.push({key: 'collect', label: '汇总成果', status: session.result_status && session.result_status !== 'not_evaluated' ? 'done' : 'pending'});
  return steps;
}

export function PipelineProgress({session}: {session: Row}) {
  const steps = pipelineSteps(session);
  const finished = steps.filter(step => step.status === 'done').length;
  const failed = steps.some(step => step.status === 'failed');
  const percent = steps.length ? Math.round((finished / steps.length) * 100) : 0;
  const call = session.progress?.current_call;
  const running = steps.find(step => step.status === 'active');
  return (
    <div className="pipeline-progress" aria-label="运行进度">
      <div className="pipeline-head">
        <strong>{failed ? '运行中止' : finished === steps.length ? '已完成' : running ? `正在${running.label}` : '等待开始'}</strong>
        <span className="pipeline-count">{finished} / {steps.length} 步{failed ? ' · 有失败' : ''}</span>
      </div>
      <div className="pipeline-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <span className={failed ? 'failed' : ''} style={{width: `${percent}%`}} />
      </div>
      <ol className="pipeline-steps">
        {steps.map(step => (
          <li key={step.key} className={step.status}>
            <span className="pipeline-dot" aria-hidden="true">{step.status === 'done' ? '✓' : step.status === 'failed' ? '!' : ''}</span>
            <strong>{step.label}</strong>
            {step.detail && <small>{step.detail}</small>}
          </li>
        ))}
      </ol>
      {call && <p className="pipeline-call">当前动作：{call.label || call.stage_label || call.tool || call.action || '模型处理中'}</p>}
      {session.execution_status && <p className="pipeline-call">执行状态 <Badge value={session.execution_status}/></p>}
    </div>
  );
}
