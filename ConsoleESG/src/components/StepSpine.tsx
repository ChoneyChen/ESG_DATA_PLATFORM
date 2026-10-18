import {navigate} from '../api';

/**
 * One spine for the whole workspace.
 *
 * Every page says where it sits in the production line and what comes next,
 * so the operator never has to reconstruct the route from nine menu entries.
 * The line is deliberately short: a PDF enters at 报告, is read once, yields
 * extraction results, and only then reaches review and publication.
 */
export type Stage = 'reports' | 'reading' | 'extraction' | 'review' | 'published';

export const STAGES: {key: Stage; title: string; href: string; hint: string}[] = [
  {key: 'reports', title: '报告', href: '/reports', hint: 'PDF 与报告身份'},
  {key: 'reading', title: '读取', href: '/processing', hint: '文字层直读与结构证据'},
  {key: 'extraction', title: '提取', href: '/extractions', hint: '条款成果与证据'},
  {key: 'review', title: '审核', href: '/review', hint: '事实、映射与口径'},
  {key: 'published', title: '发布', href: '/publication', hint: '正式数据版本'},
];

export function stageOf(section: string): Stage {
  if (section === 'ocr' || section === 'processing' || section === 'runtime') return 'reading';
  if (section === 'extractions' || section === 'automation') return 'extraction';
  if (section === 'review') return 'review';
  if (section === 'publication') return 'published';
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
