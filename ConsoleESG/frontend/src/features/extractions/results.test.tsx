import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import {OrganizedTable} from './results';

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
afterEach(cleanup);
it('keeps the raw zero, separates other measurements and filters actual period and unit',()=>{
  render(<OrganizedTable table={fixture()} sessionId="session"/>);
  expect(screen.getByText('0')).toBeTruthy();
  expect(screen.queryByText('项目2')).toBeNull();
  fireEvent.change(screen.getByLabelText('期间筛选'),{target:{value:'2025'}});
  expect(screen.queryByText('项目1')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/其他目标/}));
  expect(screen.getByText('项目2')).toBeTruthy();
  expect(screen.queryByText('项目0')).toBeNull();
  fireEvent.change(screen.getByLabelText('单位筛选'),{target:{value:'kg'}});
  expect(screen.getByText('此分类暂无数据')).toBeTruthy();
});
it('paginates independent records without discarding the later rows',()=>{
  render(<OrganizedTable table={fixture(55)} sessionId="session"/>);
  expect(screen.getByText('项目49')).toBeTruthy();
  expect(screen.queryByText('项目50')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'下一页'}));
  expect(screen.getByText('项目54')).toBeTruthy();
  expect(screen.queryByText('项目49')).toBeNull();
  expect(screen.getByText('55 条原始记录')).toBeTruthy();
});
