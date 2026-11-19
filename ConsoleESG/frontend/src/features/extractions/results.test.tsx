import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import type {OrganizedTable} from './results';
import {ClauseResultPage} from './result-pages';
import {invalidate} from '../../shared/resource';

type Table = Parameters<typeof OrganizedTable>[0]['table'];
function fixture(count = 4): Table {
  const cell = (value: string) => ({state:'available', value, raw_text:value, evidence:[]});
  return {data_kind:'quantitative', clause_key:'TEST', definition:{fields:[
    {key:'label',label:'项目',role:'source_label',binding:{source:'fact',key:'source_row_label'}},
    {key:'raw',label:'原值',role:'raw_value',binding:{source:'value',key:'value_raw'}},
    {key:'unit',label:'原单位',role:'unit',binding:{source:'value',key:'unit_raw'}},
    {key:'period',label:'期间',role:'reported_identity',binding:{source:'identity',key:'reporting_period'}},
  ]}, rows:Array.from({length:count},(_,i)=>({row_id:'r'+i,source_fact_id:'f'+i,
    result_class:count>4 || i<2 ? 'target':i===2?'other':'unresolved',
    cells:{label:cell('项目'+i),raw:cell(i===0?'0':'1,530.14'),unit:cell(i%2?'kg':'吨'),period:cell(i%2?'2024':'2025')}}))} as unknown as Table;
}
function loadResults(table: Table) {
  const rows = table.rows || [];
  const periodLabel = (value: unknown) => typeof value === 'string' ? value : '2025 Q2';
  const fetchPage = vi.fn(async (url: string) => {
    const { searchParams: params } = new URL(url, 'http://localhost');
    const period = params.get('period');
    const unit = params.get('unit');
    const relation = params.get('relation');
    const relationByClass = {target: 'match', other: 'different', unresolved: 'uncertain'};
    const selected = rows.filter(row =>
      (!relation || relationByClass[row.result_class as keyof typeof relationByClass] === relation) &&
      (!period || periodLabel(row.cells.period.value) === period) &&
      (!unit || row.cells.unit.value === unit));
    const offset = Number(params.get('cursor') || 0);
    const size = Number(params.get('limit'));
    const items = selected.slice(offset, offset + size);
    return new Response(JSON.stringify({
      table: {...table, rows: items}, facts: [], total: selected.length, returned: items.length,
      next_cursor: offset + size < selected.length ? String(offset + size) : null,
      periods: [...new Set(rows.map(row => periodLabel(row.cells.period.value)))],
      units: [...new Set(rows.map(row => row.cells.unit.value))],
      relations: {match: rows.filter(row => row.result_class === 'target').length, different: 1, uncertain: 1},
    }));
  });
  vi.stubGlobal('fetch', fetchPage);
  render(<ClauseResultPage sessionId="session" clauseKey="TEST" active={false}/>);
  return fetchPage;
}

afterEach(() => {
  cleanup();
  invalidate();
  vi.unstubAllGlobals();
});

it('keeps the raw zero and requests the selected relation, period, and unit', async () => {
  const requests = loadResults(fixture());
  expect(await screen.findByText('0')).toBeTruthy();
  expect(screen.queryByText('项目2')).toBeNull();
  const period = screen.getByLabelText<HTMLSelectElement>('期间筛选');
  fireEvent.change(period, {target: {value: '2025'}});
  expect(period.value).toBe('2025');
  await screen.findByText('共 1 条 · 本页 1 条');
  expect(screen.queryByText('项目1')).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: /其他数据/}));
  expect(await screen.findByText('项目2')).toBeTruthy();
  expect(screen.queryByText('项目0')).toBeNull();
  fireEvent.change(screen.getByLabelText('单位筛选'), {target: {value: 'kg'}});
  expect(await screen.findByText('此分类暂无数据')).toBeTruthy();
  expect(requests.mock.lastCall?.[0]).toContain('relation=different&period=2025&unit=kg');
});

it('uses the returned cursor to read the remaining rows', async () => {
  const requests = loadResults(fixture(55));
  expect(await screen.findByText('项目49')).toBeTruthy();
  expect(screen.queryByText('项目50')).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: '下一页'}));
  expect(await screen.findByText('项目54')).toBeTruthy();
  expect(screen.queryByText('项目49')).toBeNull();
  expect(screen.getByText('共 55 条 · 本页 5 条')).toBeTruthy();
  expect(requests.mock.lastCall?.[0]).toContain('cursor=50');
});

it('shows all years by default and requests the displayed quarter as a filter', async () => {
  const table = fixture(3);
  table.rows!.forEach((row, i) => {
    row.result_class = 'target';
    row.cells.period.value = i === 2 ? {kind: 'quarter', year: 2025, quarter: 2, raw: '2025年第二季度'} : String(2023 + i);
  });
  const requests = loadResults(table);
  expect(await screen.findByText('项目2')).toBeTruthy();
  expect(screen.getByText('项目0')).toBeTruthy();
  expect(screen.getByText('项目1')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('期间筛选'), {target: {value: '2025 Q2'}});
  await screen.findByText('共 1 条 · 本页 1 条');
  expect(screen.getByText('项目2')).toBeTruthy();
  expect(screen.queryByText('项目0')).toBeNull();
  expect(requests.mock.lastCall?.[0]).toContain('period=2025+Q2');
});
