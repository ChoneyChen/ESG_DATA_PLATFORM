# 定向抽取 Result Bundle 规范 v1

## 1. 规范定位

每次定向抽取产生一个不可变的 Result Bundle。它同时服务三类消费者：

1. 后续 publication / 数据库写入模块读取 `results/*.jsonl`；
2. 外部交换或人工抽查读取 `exports/`；
3. 成果检查网页通过只读 API 聚合上述文件，不建立第二份业务事实。

权威事实是六类 Core records。`machine-fill.xlsx`、网页中文表格和检查 ViewModel 都是派生视图，
不能反向覆盖 JSONL、Document IR 或模型决定。

## 2. 版本

| 合同 | 当前版本 | 作用 |
|---|---|---|
| Bundle 目录合同 | `targeted-result-bundle-v1` | 目录、文件名、ID 和关联规则 |
| 抽取结果合同 | `targeted-extraction-result-v1` | `manifest`、summary 和完整 JSON 包 |
| 平台 Core Schema | 由 `input/standard-package.json` 固定 | 六类业务记录的字段、类型、代码集和通用单位 |
| 成果检查合同 | `targeted-result-inspection-v1` | 只读 API 给前端的聚合 ViewModel |

读取器必须先检查 `schema_version`。同一大版本允许新增可选 manifest 字段；删除字段、改变含义、
改变主外键或状态语义必须发布新大版本。

Core 版本是每个标准包的显式依赖，不由 Result Bundle 全局猜测。当前 E2-4 使用
`extractesg.core@1.0.0`，E1-5/E1-6 使用向后兼容的 `extractesg.core@1.1.0`。任务必须快照
完整编译包，因此后续模块从快照中读取确切 Core 版本。

机器可读的 Bundle 合同由后端资源
`esg_targeted/resources/result-bundle-contract-v1.json` 保存，并通过
`GET /api/contracts/result-bundle` 提供。

## 3. 目录与权威性

```text
targeted_extract_output/<job_id>/
├── manifest.json
├── failure.json                         # 仅失败时存在
├── input/
│   ├── request.json
│   ├── ir-manifest.json
│   └── standard-package.json
├── inventory/
│   ├── index.json
│   ├── spans.jsonl
│   └── candidates.jsonl
├── retrieval/
│   ├── semantic-index.json              # 启用语义检索时存在
│   └── <metric_id>.json
├── packets/
│   ├── <metric_id>.json                  # 完整 Evidence Selection
│   └── <metric_id>.regions.json          # 实际模型输入区域
├── decisions/<metric_id>.json
├── guards/<metric_id>.json
├── checkpoints/<metric_id>.json          # 断点状态；未收敛项标记 reusable=false
├── results/
│   ├── summary.json
│   ├── reporting-tasks.jsonl
│   ├── quantitative-observations.jsonl
│   ├── qualitative-assertions.jsonl
│   ├── attribute-values.jsonl
│   ├── dimension-values.jsonl
│   └── evidence-references.jsonl
└── exports/
    ├── extraction-result.json
    ├── machine-fill.xlsx
    └── task-summary.csv
```

- `manifest.json` 是运行入口和目录，包含固定输入、统计、合同校验和相对导出路径。
- `input/` 是本次运行所用输入快照，不依赖标准包随后是否更新。
- `inventory` 到 `guards` 是审计层，可解释“怎样得到结果”，不作为发布事实表。
- `packets/<metric>.json` 保存完整指标证据选择；`*.regions.json` 保存真正送入模型的连贯区域、
  动态字段合同、短别名和预算。二者必须分开理解，不能用模型输入分区代替完整召回审计。
- `checkpoints` 只服务同一 job 的中断恢复；它不属于业务事实，不能代替 `results`。
- `results/*.jsonl` 是数据库就绪的规范化事实。
- `exports/extraction-result.json` 是包含 summary 与六类记录的自包含交换包。
- XLSX 与 CSV 是便利导出，不作为机器回读的唯一权威来源。

所有 manifest 路径均使用 Bundle 内相对路径，禁止写入本机绝对路径。

## 4. 编号规则

所有哈希型稳定 ID 使用规范化输入的 SHA-256 前 24 位，并保留类型前缀。前缀使日志、数据库和
网页中不需要读取记录内容便能辨认对象类型。

| ID | 格式 | 生成输入与稳定范围 |
|---|---|---|
| `job_id` | `tx-<UTC时间>-<10位随机hex>` | 每次执行唯一；即使输入相同也创建新 job |
| `metric_id` | 标准包命名空间 ID | 由标准包拥有；版本内不可改变语义 |
| `task_id` | `task-<24hex>` | `document_id + ir_revision + package_id + package_version + metric_id` |
| `observation_id` | `obs-<24hex>` | `task_id + accepted group_ref_id + group sequence` |
| `assertion_id` | `assert-<24hex>` | `task_id + accepted group_ref_id + group sequence` |
| `attribute_id` | `attr-<24hex>` | `parent_record_id + element_id + sequence` |
| `dimension_value_id` | `dim-<24hex>` | `parent_record_id + element_id + sequence` |
| `evidence_set_id` | `evset-<24hex>` | 被支持的事实 ID |
| `evidence_id` | `evidence-<24hex>` | `fact_id + span_id + sequence` |
| `inventory/span/candidate/packet` | 对应类型前缀 + `24hex` | 固定 Document IR revision 和对应结构输入 |

`task_id` 可跨相同输入的重跑保持一致；`job_id` 用于区分每次执行。事实 ID 只有在模型选择的
证据分组与顺序一致时保持一致，因此数据库去重必须同时考虑 `job_id`、审核状态和发布批次，
不能把事实 ID 当作跨版本永久主键。

每指标审计文件使用 `safe_filename(metric_id)`；当前合法标准 ID 会原样成为文件名，例如
`esrs.2023-set1.e2-4.dp02.json`。

## 5. 六类 Core 记录

### 5.1 `reporting_task`

一份报告的一个标准指标对应一条任务。主键是 `task_id`。它保存固定的 Document IR、标准包、
指标、是否找到、结果数量、不确定原因、审核状态和执行状态。

“未找到”也是一个有效任务结论，不应伪造零值 observation。

### 5.2 `quantitative_observation`

一条记录代表一个可独立比较的“指标 + 数值 + 单位 + 期间 + 边界 + 维度组合”。主键是
`observation_id`，外键 `task_id` 指向任务。原文值保存在 `value_raw`，规范数值保存在
`value_numeric/value_lower/value_upper`，单位同时保留 `unit_raw` 和受控 `unit_id`。

同一指标在不同地区、工厂、人群、年份或污染物下出现多个值时，必须生成多条 observation，
不能塞进一个字符串单元格。

### 5.3 `qualitative_assertion`

一条记录代表一个可由证据独立支持的定性陈述或列表项。`statement_raw` 保存证据约束的原始陈述，
`statement_summary` 只允许保存后续受控归纳；当前抽取不能用摘要替换原文。

### 5.4 `attribute_value`

保存不适合进入固定 Core 列、但由标准包 element 明确定义的类型化属性。通过
`parent_record_type + parent_record_id` 连接 task、observation 或 assertion，通过
`element_id` 解释语义。只允许 `value_text/value_numeric/value_boolean/value_date/value_code`
中与 `value_type` 对应的一列非空。

### 5.5 `dimension_value`

保存污染物类型、地理区域、设施、业务板块等比较维度。`value_raw` 保留报告原文，
`value_code` 保存命中标准代码集后的值。多个维度值使用多行和 `sequence`，不在单字段中拼接。

### 5.6 `evidence_reference`

每条 observation 或 assertion 至少有一条 evidence。它保存物理 PDF 页索引、印刷页码、
Document IR 对象、bbox、原文摘录与摘录哈希。

- `pdf_page_index` 永远是从 0 开始的物理页索引；
- 网页显示的“PDF 第 N 页”是 `pdf_page_index + 1`，不回写原记录；
- `printed_page_label` 是报告版面上印刷的页码，两者不能混用；
- `quote_sha256` 对规范化证据摘录计算，用于发现证据文本被意外修改。

## 6. 记录关系

```text
reporting_task
  ├── quantitative_observation
  │     ├── attribute_value
  │     ├── dimension_value
  │     └── evidence_reference
  ├── qualitative_assertion
  │     ├── attribute_value
  │     ├── dimension_value
  │     └── evidence_reference
  ├── attribute_value
  └── dimension_value
```

`evidence_set_id` 把一条事实与其主证据、辅助证据组织成集合；`source_record_id` 必须指向真实存在的
observation 或 assertion。Guard 未通过的模型 assignment 不允许进入上述关系图。

## 7. 状态语义

| 层 | 状态 | 含义 |
|---|---|---|
| 决策 | `found` | 形成一条或多条通过 Guard 的事实 |
| 决策 | `partial` | 仅满足部分标准元素，保留明确缺口 |
| 决策 | `not_found` | 完整检索后未发现支持信息，不等于数值 0 |
| 决策 | `ambiguous` | 候选冲突、范围/期间歧义或模型输出无效 |
| 审核 | `auto_verified` | 通过确定性 Grounding Guard |
| 审核 | `human_required` | 自动链路未能形成可发布结论 |

作业只有所有指标 Guard 通过且无 ambiguous 时为 `completed`，否则可以是 `partial`。
`partial` 作业中已通过的事实仍可查看，但 publication 模块必须按记录审核状态决定是否接收。
`interrupted` 表示原 worker 已消失但 job 可从 checkpoint 恢复，不等于抽取结论失败。

## 8. 类型与空值

- JSON/JSONL 使用 UTF-8；每个 JSONL 行是一条完整记录。
- 精确十进制在 JSON 中使用规范十进制字符串，避免 IEEE-754 浮点误差。
- 年份使用四位整数，日期使用 ISO `YYYY-MM-DD`。
- 未识别到的可选字段使用 `null`，不能填入“未知”“N/A”一类展示文本。
- `value_raw`、`statement_raw` 和 `evidence_excerpt` 保留来源文字，不做单位换算或改写。
- 受控代码、单位和维度使用命名空间 ID；中文名称只属于标准包 labels 或前端显示映射。

## 9. 强制校验门

导出前 `ResultContractValidator` 必须完成：

1. 六类集合和 Core Schema 对齐；
2. 必填字段齐全、无未声明字段；
3. 每种主键在集合内唯一；
4. observation/assertion 的 `task_id` 存在；
5. attribute/dimension 的父记录存在且类型一致；
6. evidence 的来源事实存在；
7. 每条事实至少有一条 evidence。

校验通过记录写入 `manifest.contract_validation`。失败时整份作业失败，不输出貌似成功但关联断裂的
正式成果包。

## 10. 成果检查解耦

`GET /api/jobs/{job_id}/inspection` 每次从 immutable artifacts 读取并临时组装：

- 指标级结论和数量；
- 量化/定性事实及属性、维度；
- 页码、章节、bbox 和证据原文；
- Guard 问题和模型尝试统计；
- Top retrieval 命中和结果文件下载地址。

该 API 不创建检查数据库、不写审批字段、不改变原始 JSONL/XLSX。中文表头、状态中文名和
`pdf_page_index + 1` 仅由前端展示层负责。未来更换前端或直接调用后端时，抽取输出保持不变。
