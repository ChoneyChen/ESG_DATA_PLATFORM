import {useState} from 'react';
import {afterEach,expect,it} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {ReportedPropertiesForm,type PropertySelection} from './ReportedPropertiesForm';

afterEach(cleanup);
it('publishes a selection and label only, retaining independent repeated source attributes',()=>{
 let result:PropertySelection[]=[];
 function Form(){const [selection,setSelection]=useState<PropertySelection[]>([]);return <ReportedPropertiesForm
  available={[{source_field:'legacy_dimensions.facility',ordinal:1,field_key:'facility',label_suggestion:'场址',value_raw:'炼化设施 A',selectable:true,evidence_scope:'source',evidence:[{page_number:90,source_quote:'炼化设施 A'}]},
   {source_field:'legacy_dimensions.facility',ordinal:2,field_key:'facility',label_suggestion:'场址',value_raw:'炼化设施 B',selectable:true,evidence_scope:'field',evidence:[]}]}
  selections={selection} onChange={value=>{result=value;setSelection(value);}}/>;}
 render(<Form/>);
 fireEvent.click(screen.getAllByRole('checkbox')[1]);
 fireEvent.change(screen.getByLabelText('补充信息中文名称：场址'),{target:{value:'涉及场址'}});
 expect(result).toEqual([{source_field:'legacy_dimensions.facility',ordinal:2,label_zh:'涉及场址'}]);
 expect(screen.queryByDisplayValue('炼化设施 B')).toBeNull(); // report values are not editable inputs
 expect(screen.getByText('炼化设施 B')).toBeDefined();
 expect(screen.getByText('来源级说明，需核对所选属性与原文的关系')).toBeDefined();
});
it('unsupported nested attributes remain visible as pending without dumping JSON or accepting a selection',()=>{
 render(<ReportedPropertiesForm available={[{source_field:'identity_raw.extra',ordinal:1,field_key:'extra',value_raw:null,
  value_type:'unsupported',selectable:false,evidence:[]}]}
  selections={[]} onChange={()=>{throw new Error('unsupported selection');}}/>);
 expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(true);
 expect(screen.getByText('原始内容已保留，当前类型或证据尚不满足发布要求。')).toBeDefined();
});
