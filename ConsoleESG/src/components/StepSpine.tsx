import {navigate} from '../api';

/**
 * One spine for the whole workspace.
 *
 * Reading a PDF is a step inside extraction, not a destination of its own: the
 * operator never has to "read first, then extract". The line is therefore
 * three moves — pick the report, get the numbers, review and publish — and
 * every page states where it sits in it.
 */
export type Stage = 'reports' | 'extraction' | 'review' | 'published';

export const STAGES: {key: Stage; title: string; href: string; hint: string}[] = [
  {key: 'reports', title: '报告', href: '/reports', hint: '选择 PDF 与报告身份'},
  {key: 'extraction', title: '提取', href: '/extractions', hint: '读取、提取与证据'},
  {key: 'review', title: '审核', href: '/review', hint: '事实、映射与口径'},
  {key: 'published', title: '发布', href: '/publication', hint: '正式数据版本'},
];

export function stageOf(section: string): Stage {
  if (section === 'review') return 'review';
  if (section === 'publication') return 'published';
  if (section === 'extractions' || section === 'automation' || section === 'processing' || section === 'ocr' || section === 'runtime') return 'extraction';
  return 'reports';
}

export function StepSpine({stage, next}: {stage: Stage; next?: {label: string; href?: string; onClick?: () => void}}) {
  const index = STAGES.findIndex(item => item.key === stage);
  return (
    <ol className="step-spine" aria-label="当前所处链路">
      {STAGES.map((item, i) => (
        <li key={item.key} className={`${i === index ? 'current' : ''} ${i < index ? 'done' : ''}`}>
          <a href={`#${item.href}`} aria-current={i === index ? 'step' : undefined}>
            <span className="step-index">{i < index ? '✓' : i + 1}</span>
            <span className="step-body"><strong>{item.title}</strong><small>{item.hint}</small></span>
          </a>
        </li>
      ))}
      {next && (
        <li className="step-next">
          {next.href
            ? <button type="button" className="secondary" onClick={() => navigate(next.href!)}>{next.label} →</button>
            : <button type="button" onClick={next.onClick}>{next.label} →</button>}
        </li>
      )}
    </ol>
  );
}
