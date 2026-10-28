import type {Row} from '../api';
import {Notice,Section} from './ui';

const AXIS_LABELS:Record<string,string>={period:'期间',entity:'主体范围',value_role:'值角色',aggregation:'聚合',method:'方法',qualifier:'限定语'};
const CODE_LABELS:Record<string,string>={annual:'年度',fiscal_year:'财年',cumulative:'累计',quarter:'季度',instant:'期末时点',target_period:'目标期',baseline_period:'基准期',restated_period:'重述期',multi_year:'多年',unknown:'未识别',group:'集团合并',consolidated:'合并口径',parent:'母公司',subsidiary:'子公司',business_unit:'事业部',segment:'业务分部',site:'基地/工厂',plant:'生产厂',project:'项目',region:'地区',office:'办公',unclassified_subset:'未分类子范围',actual:'实际值',target:'目标值',baseline:'基准值',forecast:'预测值',restated:'重述值',estimate:'估计值',context:'背景',total:'总量',subtotal:'小计',member:'成员',single_line:'单行',intensity:'强度',share:'占比',capacity:'容量',change:'变化量',location_based:'位置法',market_based:'市场法',mixed:'混合',approx:'约',at_least:'≥',at_most:'≤',range:'区间',including:'含',excluding:'不含',of_which:'其中'};

export function clause(profile:Row){
 return <details className="clause-profile" key={profile.clause_id}>
  <summary><strong>{profile.source_datapoint_id}</strong> · {profile.labels?.zh || profile.labels?.en} · {profile.output_scopes.length} 个口径</summary>
  <dl className="facts"><dt>值类型</dt><dd>{profile.value_type==='numeric'?`数值 · ${profile.canonical_unit || '单位由合同定义'}`:'陈述'}</dd><dt>值来源</dt><dd>{profile.value_origin==='reported'?'报告披露':profile.value_origin==='derived'?'派生计算':'披露或派生'}</dd><dt>正向定义</dt><dd>{profile.positive_definition || profile.description || '见条款合同'}</dd><dt>排除条件</dt><dd>{(profile.exclusions || []).join('；')}</dd></dl>
  <div className="table-scroll"><table><thead><tr><th>口径</th><th>期间</th><th>主体</th><th>聚合</th><th>方法</th><th>其他轴</th><th>必需</th></tr></thead><tbody>{profile.output_scopes.map((scope:Row)=><tr key={scope.scope_id}>
   <td>{scope.label_zh}</td><td>{CODE_LABELS[scope.period] || scope.period}</td><td>{CODE_LABELS[scope.entity] || scope.entity}</td>
   <td>{CODE_LABELS[scope.aggregation] || scope.aggregation}</td><td>{scope.method?(CODE_LABELS[scope.method] || scope.method):'不限定'}</td>
   <td>{Object.entries(scope.axes || {}).map(([key,value])=>`${AXIS_LABELS[key] || key}=${value}`).join('、') || '—'}</td>
   <td>{scope.required?'必需':'可选'}</td></tr>)}</tbody></table></div>
  <details><summary>检索词与证据要求</summary><p>正向词：{(profile.retrieval?.positive_terms || []).join('、')}</p><p>排除词：{(profile.retrieval?.negative_terms || []).join('、') || '无'}</p><ul>{(profile.evidence || []).map((item:Row)=><li key={item.field}>{item.field} · 证据层级 {item.level}{item.required?' · 必需':''}{item.note?` · ${item.note}`:''}</li>)}</ul></details>
  <details><summary>完整 profile</summary><pre className="mono">{JSON.stringify(profile,null,2)}</pre></details>
 </details>;
}

export function ClauseProfiles({profiles,skipped=[]}:{profiles:Row[];skipped?:Row[]}){
 if(!profiles.length&&!skipped.length)return null;
 const required=profiles.reduce((sum,profile)=>sum+profile.output_scopes.filter((scope:Row)=>scope.required).length,0);
 const total=profiles.reduce((sum,profile)=>sum+profile.output_scopes.length,0);
 return <Section title="输出口径（条款 profile）" action={<span className="count">{profiles.length} 条条款 · {total} 个口径</span>}>
  <Notice>每条条款声明它能产出哪些口径。运行只对这些口径负责；未覆盖的必需口径会单独记为缺口，而不是笼统的“未找到”。</Notice>
  {profiles.map(profile=>clause(profile))}
  {skipped.length>0&&<details><summary>未生成 profile 的条款 · {skipped.length}</summary><ul>{skipped.map((row:Row)=><li key={`${row.package_id || ''}-${row.source_datapoint_id}`}>{row.source_datapoint_id} · {row.reason}</li>)}</ul></details>}
  <p className="muted">必需口径 {required} 个，可选口径 {total-required} 个。</p>
 </Section>;
}
