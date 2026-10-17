import {afterEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {ExtractionModelPlan,defaultModelPlan,requestModelPlan} from './components/ExtractionModelPlan';
afterEach(cleanup);
describe('按任务选择实际模型',()=>{
 it('统一方案使用注册模型展示，混合为可选；固定证据跳过识别复核',()=>{
  const onChange=vi.fn(),onPreset=vi.fn();
  render(<ExtractionModelPlan value={defaultModelPlan} onChange={onChange} onPreset={onPreset} brain="local_qwen" fixedEvidence options={{tasks:{specialist:{providers:[{provider:'local_qwen',model:'Registered-Qwen-4bit'}]}}}}/>);
  expect(screen.getByText('Registered-Qwen-4bit')).toBeTruthy();
  expect(screen.getByText('复用已有证据，本次跳过')).toBeTruthy();
  expect(screen.queryByLabelText('执行额外视觉复核')).toBeNull();
  fireEvent.change(screen.getByLabelText('模型方案'),{target:{value:'mixed'}});
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({specialist:'local_nuextract'}));
  expect(onPreset).toHaveBeenCalledWith('local_qwen');
 });
 it('自定义保留选择；未接通的Qwen OCR禁用，视觉复核可选且进入请求',()=>{
  const onChange=vi.fn();
  render(<ExtractionModelPlan value={{...defaultModelPlan,preset:'custom'}} onChange={onChange} onPreset={()=>{}} brain="local_qwen" fixedEvidence={false}/>);
  const ocr=screen.getByLabelText('页面识别引擎');
  expect(within(ocr).getByRole('option',{name:'Qwen 页面识别 · 输出适配器待接入'}).hasAttribute('disabled')).toBe(true);
  fireEvent.change(screen.getByLabelText('结构视觉复核模型'),{target:{value:'local_nuextract'}});
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({review:'local_nuextract'}));
  expect(requestModelPlan({...defaultModelPlan,requiredReviews:true,review:'local_nuextract'})).toMatchObject({visual_review_provider:'local_nuextract',execute_required_reviews:true,execute_visual_reviews:false});
 });
});
