import type {Row} from './api';

/**
 * One place for how the document pipeline is described to the operator.
 *
 * The backend contract stays in its own field names (`page_selection`,
 * `ocr_versions`, `document_coverage.reading`); these helpers only translate
 * those fields into the reading route the platform actually runs today, so a
 * wording change never has to be repeated across pages.
 */

export type Reading = {
  text_layer_pages?: number;
  visual_ocr_pages?: number;
  on_device_ocr_pages?: number;
  layout_model?: string | null;
};

export const READING_STEPS = [
  ['select', '选页'],
  ['layout', '版面区域'],
  ['text', '文字层直读'],
  ['visual', '视觉识别不可读页'],
  ['ir', '结构证据'],
  ['extract', '指标提取'],
] as const;

/** The route a PDF takes, in the order the platform performs it. */
export function pipelineSteps(autoEvidence: boolean): string[] {
  return autoEvidence
    ? ['报告 PDF', '选页', '版面区域', '文字层直读（不可读页视觉识别）', '结构证据', '指标提取']
    : ['已有结构证据', '指标提取'];
}

export function hasReading(value: Row | undefined): value is Reading {
  return !!value && typeof value.text_layer_pages === 'number';
}

/** "文字层直读 22 页，其中 2 页标题经本机识别，视觉识别 3 页" */
export function readingText(reading: Reading): string {
  const parts = [`文字层直读 ${reading.text_layer_pages ?? 0} 页`];
  if (reading.on_device_ocr_pages) parts.push(`其中 ${reading.on_device_ocr_pages} 页标题经本机识别`);
  parts.push(`视觉识别 ${reading.visual_ocr_pages ?? 0} 页`);
  return parts.join('，');
}

export function coverageText(coverage: Row | undefined): string {
  if (!coverage) return '';
  const pages = coverage.mode === 'targeted'
    ? `读取 ${coverage.covered_pages}/${coverage.source_pages} 页`
    : `全文 ${coverage.covered_pages} 页`;
  return hasReading(coverage.reading) ? `${pages}（${readingText(coverage.reading)}）` : pages;
}

/** What the preview says will happen to the source before extraction starts. */
export function preparationText(preparation: Row | undefined): string {
  if (!preparation) return '';
  if (preparation.ir_reused) return `直接使用已有结构证据 ${preparation.ir_run_id || ''}，本次不再读取页面。`;
  if (preparation.ocr_reused) return '复用这份报告已有的页面读取成果，只重建结构证据后提取。';
  if (preparation.page_selection === 'foundation16') {
    return '先在 PDF 文字层选出可能含 16 项数据的页面（约 25 页），按版面区域直读文字并重建表格；'
      + '扫描页、图片表格等文字层不可用的页才调用视觉识别。';
  }
  return '按版面区域直读 PDF 文字层构建结构证据；文字层不可用的页调用视觉识别，然后提取。';
}

export function routeLabel(route: string | undefined): string {
  return route === 'foundation16' ? '基础 16 项快速通道' : '标准逐包提取';
}
