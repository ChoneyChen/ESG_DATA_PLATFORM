import {afterEach,expect,it} from 'vitest';
import {cleanup,render,screen,within} from '@testing-library/react';
import {PackageBudgetUsage} from './PackageBudgetUsage';
afterEach(cleanup);
const budget={scope:'report_summary',policy:'independent-standard-package-v1',enforces_report_limit:false,
 usage:{model_calls:12,format_repairs:1,input_tokens:190,output_tokens:5,unfinished_calls:0},
 packages:[{job_id:'job-a',package_id:'E1-5',limits:{max_model_calls:10,max_format_repairs:2,max_input_tokens:100,max_output_tokens:100},usage:{model_calls:10,format_repairs:1,input_tokens:95,output_tokens:3}},
 {job_id:'job-b',package_id:'E2-4',limits:{max_model_calls:10,max_format_repairs:2,max_input_tokens:100,max_output_tokens:100},usage:{model_calls:2,format_repairs:0,input_tokens:95,output_tokens:2}}]};

it('报告汇总超过单包上限时仍逐包显示完整额度，不生成共享剩余额度',()=>{
 render(<PackageBudgetUsage budget={budget}/>);
 expect(screen.getByText('输入 token累计 190')).toBeTruthy();
 const rows=screen.getAllByRole('row');
 expect(within(rows[1]).getByText('10 / 10')).toBeTruthy();
 expect(within(rows[2]).getByText('2 / 10')).toBeTruthy();
 expect(screen.getAllByText('95 / 100')).toHaveLength(2);
 expect(screen.getByText('达到本包上限')).toBeTruthy();
 expect(screen.queryByText(/报告剩余/)).toBeNull();
});

it('历史未归属、缺失上限和未完成预留分别表达，不能显示成实测零',()=>{
 render(<PackageBudgetUsage budget={{...budget,usage:{...budget.usage,unfinished_calls:1},
 packages:[{...budget.packages[1],limits:null,legacy_attributed:true}],
 legacy_unattributed_usage:{model_calls:3},accounting:{input:{estimated_prompt_tokens:95},output:{configured_output_reservation:35}}}}/>);
 expect(screen.getByText('由历史调用记录归属')).toBeTruthy();
 expect(screen.getByText('95 / 未记录')).toBeTruthy();
 expect(screen.getByText(/含 1 次未完成调用的预留用量/)).toBeTruthy();
 expect(screen.getByText(/另有 3 次历史调用尚未归属到标准包/)).toBeTruthy();
 expect(screen.getByText('输入：输入估算 95')).toBeTruthy();
 expect(screen.getByText('输出：调用预留 35')).toBeTruthy();
});

it('旧预算口径保留历史语义，不伪造新版各包剩余额度',()=>{
 render(<PackageBudgetUsage budget={{usage:{input_tokens:190},limits:{max_input_tokens:100}}}/>);
 expect(screen.getByText(/此历史记录采用旧预算口径/)).toBeTruthy();
 expect(screen.queryByRole('table')).toBeNull();
});

it('未返回用量时显示读取状态，不宣称未调用或用量为零',()=>{
 render(<PackageBudgetUsage/>);
 expect(screen.getByText('尚未取得各标准包的模型用量记录。')).toBeTruthy();
 expect(screen.queryByText(/累计 0/)).toBeNull();
});
