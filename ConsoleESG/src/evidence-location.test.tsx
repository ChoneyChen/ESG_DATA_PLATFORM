import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import {boxStyle,EvidenceExplorer} from './components/EvidenceExplorer';
import {WorkspaceProvider} from './state';
import {credentials,type Row} from './api';

const bbox=(changes:Row={})=>({x0:10,y0:20,x1:30,y1:60,unit:'points',origin:'top_left',...changes});
const page={page_index:0,width:100,height:200,page_image_path:'artifacts/page-images/page-0001.png'};
let routes:Record<string,unknown>={};
beforeEach(()=>{
  credentials.clear();
  vi.stubGlobal('EventSource',class {onopen=null;onerror=null;onmessage=null;addEventListener(){}close(){}});
  vi.stubGlobal('fetch',vi.fn((url:string)=>Promise.resolve(new Response(JSON.stringify(routes[String(url)] || {detail:'Undeclared test route'}),{status:Object.hasOwn(routes,String(url))?200:404,headers:{'Content-Type':'application/json'}}))));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();credentials.clear();});

function show(cellBox:Row|null,tableBox:Row|null,cellCapability=true,tableCapability=true,extraPage:Row={}){
  const base='/document/api/document-ir/jobs/fixture';
  routes={
    [base+'/pages']:[{...page,...extraPage,page_number:1}],
    [base+'/pages/0']:{page:{...page,...extraPage},layout_objects:[],tables:[{
      table_id:'table-1',page_index:0,bbox:tableBox,evidence_capabilities:{cell_grid:true,cell_geometry:false,table_region:tableCapability},
      cells:[{cell_id:'cell-1',page_index:0,row_index:1,col_index:2,text:'123',bbox:cellBox,location_capabilities:{cell_geometry:cellCapability,table_region:tableCapability}}],
    }]},
    [base+'/quality-report']:{issues:[]},[base+'/human-review/inbox']:{groups:{resolved:[]}},
  };
  return render(<WorkspaceProvider><EvidenceExplorer irRunId="fixture" focusObject="cell-1"/></WorkspaceProvider>);
}

describe('证据位置能力与真实降级',()=>{
  it.each([
    {x0:-1},{y1:201},{unit:'pixels'},{origin:'bottom_left'},{x0:NaN},{x1:10},{x0:'10'},{x0:true},
  ])('非空但无效的坐标不用于位置高亮：%j',change=>expect(boxStyle(bbox(change),page)).toBeUndefined());
  it.each([{width:0},{height:Infinity},{width:NaN},{width:null},{width:'100'}])('页尺寸无效不绘制：%j',change=>expect(boxStyle(bbox(),{...page,...change})).toBeUndefined());
  it('声明坐标系必须属于该页、单位一致且尺寸一致',()=>{
    const declared={...page,coordinate_system_ids:['canonical'],coordinate_systems:[{coordinate_system_id:'canonical',page_index:0,width:100,height:200,unit:'points',origin:'top_left'}]};
    expect(boxStyle(bbox({coordinate_system_id:'canonical'}),declared)).toBeDefined();
    expect(boxStyle(bbox({coordinate_system_id:'different-page'}),declared)).toBeUndefined();
    for(const change of [{unit:'pixels'},{origin:'bottom_left'},{page_index:1},{width:101}])expect(boxStyle(bbox({coordinate_system_id:'canonical'}),{...declared,coordinate_systems:[{...declared.coordinate_systems[0],...change}]})).toBeUndefined();
    expect(boxStyle(bbox({coordinate_system_id:'canonical'}),{...declared,coordinate_systems:[]})).toBeUndefined();
    expect(boxStyle(bbox({coordinate_system_id:'canonical'}),{...declared,page_index:null})).toBeUndefined();
    expect(boxStyle(bbox({coordinate_system_id:'canonical'}),{...declared,coordinate_system_ids:'canonical'})).toBeUndefined();
  });
  it('部分表格中有真实坐标的所选格子仍可精确定位',async()=>{
    show(bbox(),bbox({x1:90,y1:190}));
    const overlay=await screen.findByRole('button',{name:'精确定位单元格 行 2 列 3'});
    expect(overlay.getAttribute('data-location-scope')).toBe('cell');
    expect(overlay.style.left).toBe('10%');expect(overlay.style.width).toBe('20%');
    expect(screen.getByRole('button',{name:'123'}).closest('td')?.className).toBe('selected-cell');
    expect(screen.queryByRole('button',{name:/定位整表区域/})).toBeNull();
    expect(document.querySelectorAll('.position-box')).toHaveLength(1);
  });
  it.each([null,bbox({x1:101})])('无单元格坐标或非空非法坐标时，仅展示真实整表位置',async cellBox=>{
    show(cellBox,bbox({x1:90,y1:190}));
    const overlay=await screen.findByRole('button',{name:'定位整表区域 table-1'});
    expect(overlay.getAttribute('data-location-scope')).toBe('table');
    expect(overlay.style.width).toBe('80%');
    expect(screen.queryByRole('button',{name:/精确定位单元格/})).toBeNull();
    expect(screen.getByText(/仅能定位整表区域/)).toBeTruthy();
    expect(document.querySelectorAll('.position-box')).toHaveLength(1);
  });
  it('服务明确禁用的单元格能力不会被非空bbox重新打开',async()=>{
    show(bbox(),bbox({x1:90,y1:190}),false);
    await screen.findByRole('button',{name:'定位整表区域 table-1'});
    expect(screen.queryByRole('button',{name:/精确定位单元格/})).toBeNull();
  });
  it.each([null,bbox({unit:'pixels'}),bbox({x1:101})])('父表也无真实位置时，只定位结构格，不能暗示整表高亮',async tableBox=>{
    show(null,tableBox,false);
    await screen.findByText('单元格 · 行 2 / 列 3');
    expect(screen.getByText('仅能在结构表格中定位真实格子，没有可验证的页面坐标。')).toBeTruthy();
    expect(screen.getByRole('button',{name:'123'}).closest('td')?.className).toBe('selected-cell');
    expect(document.querySelectorAll('.position-box')).toHaveLength(0);
  });
  it('服务明确禁用整表位置时不重新打开',async()=>{
    show(null,bbox(),false,false);
    await screen.findByText('单元格 · 行 2 / 列 3');
    expect(document.querySelectorAll('.position-box')).toHaveLength(0);
  });
});
