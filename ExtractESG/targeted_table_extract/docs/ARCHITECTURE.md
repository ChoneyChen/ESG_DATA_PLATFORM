# 定向抽取架构

## 1. 系统边界

定向抽取只读消费两个版本化输入：一个可构建证据的 Document IR revision，以及一个已编译的标准
要求包。它不修改 OCR、PDF、Document IR、标准包源文件和 publication 数据库。

标准包是字段和语义的唯一来源：指标 ID、名称、说明、数据类型、元素、固定值、基数、绑定、概念
别名与排除关系均从包内编译。抽取引擎拥有检索权重、预算、模型路由、重试、Guard 和物化策略，
这些运行参数不写回标准包。

## 2. 高内聚组件

| 组件 | 唯一职责 | 不负责 |
|---|---|---|
| `ir` | 目录与只读 Document IR 加载 | 修复或改写 IR |
| `standards` | 加载编译包并生成指标查询意图 | 手写每个报告的搜索脚本 |
| `evidence.inventory` | 建立全报告 span、结构、坐标和视觉引用 | 判断最终字段语义 |
| `evidence.harvester` | 提取可见字面量与偏移 | 决定数值属于哪个指标 |
| `retrieval` | BM25、Embedding、RRF、FactPattern 与充分性 | 生成业务事实 |
| `evidence.selection` | 保留命中对象的完整可审计证据 | 按模型窗口丢弃表格 |
| `evidence.regions` | 形成连贯、受预算约束的实际模型输入 | 预先填写结果字段 |
| `models` | 让 NuExtract3 或七牛 VLM 直接输出多行语义填表决定 | 创建标准字段 |
| `grounding` | 校验合同、来源和结构一致性 | 重做 ESG 语义判断 |
| `results` | 按标准 binding 物化 Core records 和导出 | 修改模型决定 |
| `inspection` | 从不可变产物派生前端只读 ViewModel | 建立第二份权威事实 |
| `storage` | job、事件、心跳、checkpoint 和产物路径 | 调度其他重任务 |

## 3. 三层证据合同

### 3.1 Evidence Inventory

Inventory 是该 Document IR revision 的报告级证据索引。span 保留 `document_id`、`ir_run_id`、
revision、页码、对象 ID、bbox、结构上下文和 crop 路径。候选值保留原文、类型、偏移和来源 span。

### 3.2 Evidence Selection

Selection 是一个指标经过混合检索后选中的完整审计材料。命中一个综合表格对象时，选择层保留该表
中与指标介质、数值家族和单位维度相容的 row groups、cells、header paths 和视觉产物；空气任务不会
把水体或土壤污染行带入模型。选择层有高安全上限，但不受单次模型 prompt 的小预算支配。任务参数
`retrieval_object_top_n`（1–5，默认 3）先在对象层限流；同一对象内的行、cell 和 header 不按 span
数冒充多个 Top N 结果。

介质隔离由标准包概念关系编译，不靠条款 ID 或报告特例。当前介质的下位概念进入正向查询；互斥
介质及其专属下位概念进入负向查询。例如空气指标既不会正向扩展 COD/BOD，也会把只写 COD/BOD
而未写“废水”的行识别为冲突。检索对象作用域和模型 `excluded_scope_terms` 共用该合同，避免不同
阶段各自解释“空气/水体/土壤”。

FactPattern 不作为最终语义资格门槛。没有常规可用对象、但检索仍有高排名结构化数量对象时，选择层
允许一次有界 `semantic_model_review_fallback`；明确的互斥范围和普通质量/CO2e 量纲隔离仍保留。
本地充分性状态不能否决已经选出的送模材料。最终指标归属仍由模型判断。

### 3.3 Evidence Region

Region 是唯一允许按模型容量切分的层。一个 region 只能对应一张表、一个图或一个文本区域，并且
最多绑定一张 crop；不同视觉对象绝不在一次调用中混合。表格优先保持完整对象，确实超预算时才按
目标值列和连续 row groups 切分。模型读取完整原表 crop，Document IR 和权威 crop 保持不变。
每个 region 包含：

- 由标准包动态编译的 metric 与 element 合同；
- metric 固定适用范围及动态编译的互斥范围信号；
- 原始 span 文本、上下文、表格 cell 坐标和堆叠表头；
- 本地字面量候选；
- 实际发送图片对应的视觉来源；
- 本 region 允许输出的行组和最大事实行数。

## 4. 模型输出合同

模型直接输出：

```json
{
  "task_id": "task-...",
  "status": "found",
  "row_groups": [
    {
      "group": "G1",
      "metric_match": "match",
      "interpretation_note": null,
      "context_refs": [],
      "shared_fields": {
        "pollutant": "二氧化硫",
        "mass_unit": "千克",
        "additional_breakdown": "越秀服务"
      },
      "values": [
        {
          "target_cell": "T1",
          "fields": {
            "emission_amount": 0.52,
            "reporting_period": "2024"
          }
        }
      ]
    }
  ],
  "uncertainty_code": "none"
}
```

`shared_fields` 与 `values[].fields` 的键完全来自当前标准包。模型先对物理源行判断一次
`metric_match`，再展开该行各个适用的实体/期间值。字段没有明示证据时为 `null`，不得推断、计算、
换算或借用其他行的数据。定性事实必须给出可读 statement 或清单成员，不能把期间或纯数字回填为断言。

模型只返回标准字段的直接标量或 `null`，不负责生成来源 ID。Adapter 在当前 G 组内自动绑定来源：
`candidate` 是本地 literal，`span` 是 IR 可见字符串，`visual` 是本次实际发送图片的视觉读取。
`target_value_cells` 非空时，主值必须来自该清单；组合量允许拆成数值和单位两个字段，但不能借用邻列。
全部 region 完成并通过 Guard 后，Results 层只按“相同物理语义组 + 相同 element 集合 + 相同可见值”
合并重叠 region 的完全重复事实；不同物理单元格即使同值也不会被折叠，不跨行拼字段。

## 5. Guard：放权与底线

模型拥有以下决定权：指标相关性、污染介质和污染物归类、行列表头组合、视觉读数、统计范围、期间、
维度、多事实拆行，以及 `found/partial/not_found/ambiguous`。

Guard 不对这些语义决定二次打分。它只把以下确定性缺陷作为 error：

- 任务、行组、element 或来源不存在；
- 模型输出标准包固定字段或未知字段；
- `C` 与候选原文不同，或候选类型与目标类型确定冲突；
- `S` 不是所引 span 的可见子串；
- `V` 不属于本次 region 的视觉来源，或本次没有发送图片；
- 主值与字段证据跨 row group，或同一事实明确混合不同表格/错误表头锚点；
- 表格主值不属于当前目标值 cell；
- 定量事实没有且仅有一个主值；
- 完全重复且未被 Adapter 无损去重的事实。

标准要求的上下文字段未披露、视觉值形状异常但仍可读等情况只产生 warning。结果保留模型已找到的
主值和全部已有字段，空元素继续为空，不因“填不满”而反复调用模型。
清单中的目标 cell 未输出也只作覆盖提醒，不强迫模型把不适用行填入指标。对于结构上已知错位的表头，
保留模型的图片判读并绑定当前视觉证据，不把错位年份/实体强制写回。这个标记不增加 Guard 门槛。

## 6. Provider 与收敛

本地和云端共享动态字段合同、Response Adapter、Normalizer、Guard 和 Materializer；上下文与输出
预算按 provider 能力独立配置。表格或图表有 crop 时两条通道均可视觉优先。

每个 region 独立执行和校验。语法/Schema 错误使用紧凑反馈重试；服务故障按服务策略重试；Guard
只反馈确切合同缺陷。多个 region 的通过结果合并、无损去重：存在事实时，其他 region 的
`not_found` 不会覆盖事实；存在未解决 region 时状态为 `partial`，但已经通过的事实仍保留。

## 7. 结果与可观测性

每次调用记录 provider、model、region、group/span/candidate/image 数量、输入字符/token、输出 token、
finish reason、模型加载/prefill/generation 耗时、原始输出、适配结果和 Guard。Selection 与 regions
分别持久化，便于区分“完整召回到了什么”和“模型实际看到了什么”。

通过 Guard 的决定按标准包 `binding` 物化为六类 Core records。固定字段由标准包写入；视觉或文本
值在物化时只进行 Decimal/年份等类型解析，不创造语义或单位换算。

固定值是请求口径，不能证明报告口径。模型的 `metric_match` 将待确认或其他口径测量保留在
审计决定中；Materializer 只将 match 行投影到标准记录。这不是额外的语义 Guard。
正常结束不再以 worklist 未填完触发催补；真实 length 截断仍可续写。

验证状态分为来源、结果合同、模型语义决定和展示就绪度。Grounding 通过的新事实仍是 `pending`，
不能显示成“自动验证了 ESG 语义”。跨指标审计只处理一个窄而确定的矛盾：同一物理源单元格被互斥
包固定维度同时使用。它保留模型决定并要求联合复判，不删除事实、不扩大 Guard。

`FactOrganizer` 是后端唯一的行/列展示顺序来源。原始六类记录不删除；相同语义身份下的重复披露
及精度区间兼容的替代单位展示用 `logical_measurement_id` 关联，证据集合保留。身份缺失时不
贸然跨来源合并。输出 `results/fact-organization.json`、JSON organization、XLSX 和 machine-fill.csv；
检查 API 对历史包也能即时计算相同视图，不改写旧包。前端不再独立做业务排序。

受限 IR 按 manifest 排除问题页面与对应证据对象；所有该次指标结果标为 partial，不能宣称
全文扫描完毕且未披露。来源缺失/全局结构损坏仍阻止加载。

## 8. 控制面边界

统一 `18081` 前端和 Document 后端的队列只负责选择、排队、排序、取消、健康和展示。定向抽取
后端 `18180` 仍可由 API/CLI 独立运行。统一控制面不读取模型私有状态，不改写 Result Bundle。
