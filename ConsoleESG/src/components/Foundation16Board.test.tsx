import {describe, expect, it} from 'vitest';
import {foundationCsv, readingText} from './Foundation16Board';

describe('foundationCsv', () => {
  it('writes one row per member and quotes text that needs it', () => {
    const csv = foundationCsv([
      {code: 'E2-4_02', label: '向空气排放污染物', status: 'found', group: 'air', status_label: '已找到', members: [
        {member: 'NOx', value_raw: '45.63', unit_raw: 't', period: '2025', flag_labels: [], source: {page_number: 161, row_label: '氮氧化物排放量'}},
        {member: 'SOx', value_raw: '0.63', unit_raw: 't', period: '2025', flag_labels: ['原文单位, 需复核'], source: {page_number: 161, row_label: 'SO2'}},
      ]},
      {code: 'E2-5_02', label: '关注物质总量', status: 'not_found_in_selected_sources', group: 'substances', status_label: '未找到', members: []},
    ]);
    const lines = csv.split('\n');
    expect(lines).toHaveLength(4);
    expect(lines[1]).toBe('E2-4_02,向空气排放污染物,已找到,NOx,45.63,t,2025,161,氮氧化物排放量,');
    expect(lines[2].endsWith('"原文单位, 需复核"')).toBe(true);
    expect(lines[3].startsWith('E2-5_02,关注物质总量,未找到,')).toBe(true);
  });
});

describe('readingText', () => {
  it('says how the covered pages were read', () => {
    expect(readingText({text_layer_pages: 22, visual_ocr_pages: 3, on_device_ocr_pages: 2}))
      .toBe('文字层直读 22 页，其中 2 页标题经本机识别，视觉识别 3 页');
    expect(readingText({text_layer_pages: 25, visual_ocr_pages: 0, on_device_ocr_pages: 0})).toBe('文字层直读 25 页，视觉识别 0 页');
  });
});
