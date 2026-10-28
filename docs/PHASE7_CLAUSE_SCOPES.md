# 阶段 7：口径枚举与条款 profile

日期：2026-10-28  
范围：`ExtractESG/standards`（合同与编译）、定向提取（覆盖输出）、Document API、Console 标准中心。

## 1. 为什么先做口径

以前的链路只能回答"这条条款找没找到值"，回答不了"找到的是**哪个口径**的值"。
子公司、基地、事业部、历史年份、目标值、强度、方法，全靠词表和模型临场判断。
阶段 7 把这些变成**受控枚举**，让条款先声明"我能产出哪些口径"，报告事实再映射到同一套代码。

## 2. 受控词汇（`clause_scope.py`）

| 轴 | 代码 |
|---|---|
| 期间 | annual / fiscal_year / cumulative / quarter / instant / target_period / baseline_period / restated_period / multi_year / unknown |
| 主体 | group / consolidated / parent / subsidiary / business_unit / segment / site / plant / project / region / office / unclassified_subset / unknown |
| 值角色 | actual / target / baseline / forecast / restated / estimate / context / structure / unknown |
| 聚合 | total / subtotal / member / single_line / intensity / share / capacity / change / unknown |
| 方法 | 条款自定义（Scope 2 固定为 location_based / market_based） |
| 限定语 | approx / at_least / at_most / range / including / excluding / of_which |

报告侧归一：`period_code`、`entity_code`（含打印边界代码到主体代码的显式映射）、`fact_scope`、`qualifier_codes`。
**未知不猜**：无法判定的轴记 `unknown`，匹配结果是 `unknown`（未确认），不是匹配，也不是缺失。

## 3. 条款 profile（`clause_profile.py`）

```text
clause_id / package / metric
labels, positive_definition, distinguish_from, exclusions
value_type, canonical_unit, unit_dimension, value_origin
output_scopes[]         # 每个口径一条：period×entity×aggregation×method×axes，required 标记
evidence[]              # 字段级证据要求（value/row/column_header/unit/...）
retrieval               # 正向词、排除词、结构锚点
acceptance              # 真实运行样例（值或未披露），不是值来源
publication             # 聚合、比较轴、固定轴
contract_digest         # 内容寻址，改动即变
```

编译规则（从不可变包 + 平台字典）：

- 默认口径 = 年度 × 集团 × 该条款的值角色 × 聚合角色；
- 值角色 target/baseline 自动映射到目标期/基准期；
- 比较轴含 `scope_2_method` 时，额外生成两个**可选**方法口径（位置法/市场法）；
- 字典里的固定维度进入 `axes`（如 `energy_carrier=renewable`）；
- `do_not_sum` **不再**被误读成"小计"，聚合统一按条款自身取值判定为 total/share/intensity；
- 金额、计数、比值、面积、无量纲数字都有单位回退，不再因为缺 `canonical_unit_id` 而丢失条款。

## 4. 真实数据

```bash
cd ExtractESG/standards && PYTHONPATH=src python scripts/build_clause_profiles.py
packages=99 profiles=1125 numeric=272 statement=853
scopes_per_clause: {1: 1120, 3: 5}
skipped=1  (E1-5_19 无法定单位)
```

产物在 `ExtractESG/standards/clause-profiles/<package>@<version>.json`，随包版本固定；`--check` 用于校验是否过期。

## 5. 提取侧消费

`BundleWriter._scope_coverage` 对每条条款计算本次运行的口径覆盖：

| 状态 | 含义 |
|---|---|
| covered | 有事实匹配该口径 |
| unconfirmed | 有事实，但期间/主体/方法还不足以确认 |
| out_of_scope | 报告只在其他口径下披露（例如只有子公司值） |
| missing | 本次运行没有覆盖 |

必需口径未覆盖会写一条 `scope_not_covered` 缺口，写明口径与原因；不再混进笼统的"未找到"。
覆盖结果写入成果包 `results/source-facts.json` 的 `scope_coverage` / `scope_gaps`，并在 manifest 给出汇总计数。

真实成果包验证（e2-4，2 条条款、12 条观测）：两条必需口径都是 `unconfirmed`——
因为该批次观测没有记录报告边界。这正是阶段 7 想要的诚实结论：**有值 ≠ 口径已确认**。

## 6. 接口与界面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/clause-profiles/{package_id}/{package_version}` | 一个包的条款 profile 与编译跳过项 |
| GET | `/api/standards/{package_id}/{package_version}` | 详情中带 `profiles`、`profile_skipped` |
| GET | `/api/pipeline/clause-profiles/{package_id}/{package_version}` | 控制台同源代理 |

标准中心新增"输出口径（条款 profile）"：每条条款列出期间/主体/聚合/方法/其他轴/是否必需，
并展示检索词与证据要求。

## 7. 测试

标准包 145 通过（新增 11 条口径与 profile 测试）；定向提取 780 通过、6 跳过（新增 5 条覆盖测试）；控制台 240 通过。

## 8. 边界

- 29 条早期跳过项已收敛到 1 条；其余因缺少单位定义而跳过，会随包更新补齐。
- 主体代码仍以打印边界词表为主，尚未引入"主体层级图"（母公司→子公司→基地）；阶段 8 的条款包里预留该字段。
- 口径覆盖只统计数值观测；陈述型条款的覆盖仍由生命周期验收判断。
