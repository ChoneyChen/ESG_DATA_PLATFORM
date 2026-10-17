# 标准模块包制作规范

本规范用于制作 ESRS E1、E2、S1、G1 或其他框架的标准模块包。标准模块只描述
“标准要求什么以及结果应形成什么结构”，不描述报告检索、模型提示词或执行顺序。

## 1. 固定文件集合

格式 `1.1` / `2.0` / `2.1` 的每个模块版本必须完整提供以下十个文件：

```text
<framework>/<framework-version>/<module>/<package-version>/
├── manifest.json
├── metrics.json
├── elements.json
├── concepts.json
├── relations.json
├── code_sets.json
├── dimensions.json
├── derivations.json
├── validation_rules.json
└── sources.json
```

不得增加只有某个模块编译器才认识的私有文件。新增语义必须先扩展统一 Pydantic/JSON
Schema 契约并发布新格式版本。

## 2. 包身份与版本

- `package_id`：`<framework>.<framework-version>.<module>`，全局稳定。
- `package_version`：语义化版本 `MAJOR.MINOR.PATCH`。
- `core_schema_id/core_schema_version`：声明唯一 Core 依赖。
- `framework_version`：必须绑定正式标准版本或发布日期，不能只写“最新版”。
- `effective_from/effective_to`：描述该标准版本适用时间。
- `status`：未经第二模块迁移验证和业务复核前保持 `draft`。

修改规则：

- 修正文案且不改变机器含义：PATCH；
- 新增兼容的数据点、元素或枚举：MINOR；
- 删除、改名、改变绑定或改变结果语义：MAJOR。

已发布版本不得原地修改；新版本建立新目录。

## 3. 指标定义 `metrics.json`

一条 metric 表示标准中一个可独立判断、独立举证的数据点，不表示数据库中的一行结果。

每条必须包含：

- 命名空间化 `metric_id`；
- 标准原始数据点 ID `source_datapoint_id`；
- DR、paragraph、AR；
- 中英文标签；
- `data_class`：`structure/quantitative/qualitative/mixed`；
- `obligation_class`；
- 允许的值来源；
- 结果基数；
- 允许或要求的维度；
- 标准含义、填报要求和来源引用。

格式 `1.1` 的 metric 还必须声明：

- `subject_concept_groups`：外层组之间为 AND，同一组内概念为 OR；
- `context_concept_ids`：期间、范围、方法等辅助语义，不作为主体概念；
- `excluded_concept_ids`：标准明确排除或容易混淆的概念。

需要多行事实或存在相近指标族时，建议再声明：

- `fact_grain`：一条结果代表源行、源单元格、清单成员还是整段断言；
- `measurement_kind`：数量、比例、强度、定性结构等测量类别；
- `required_semantic_discriminators`：期间、实体、范围、方法、类别等必须由结果区分的语义轴；
- `confusable_metric_ids`：可能命中同一证据、需要联合检查的兄弟指标；
- `evidence_form`：期望的表格行、文本说明、脚注/方法上下文等证据形态；
- `extraction_strategy`：`reported`、`derived` 或 `reported_or_derived`；
- `identity_axes`：后台排序、组织和重复判定所需的事实身份轴。

这些字段表达结果语义，不得写入 Top N、provider、prompt 文本或报告专属规则。

这里表达的是标准语义关系，不是 BM25 权重、查询模板或模型执行配置。

不得在 metric 中预设“报告一定有值”，也不得用空事实记录表示未披露。

格式 `2.0` 还要求 `disclosure_topic` 与 `disclosure_role`。主题应是一个可以连贯理解的小披露族，不能把整个标准的几十项塞进同一主题。角色使用 `anchor/detail/context/derived/presentation`，不表达执行顺序。关系使用分解、并列方法、独立披露、计算依赖、方法说明、展示投影；计算/分解/展示依赖必须无环，说明关系允许双向。

独立披露分支不得从主指标缺失推导为未披露。并列方法不得因同值自动等同。展示投影引用已有源事实，不能用数字合成无证据的断言。总量与成员同时披露时，应允许各自的事实粒度与聚合角色；计算只消费口径匹配的总量，不将其与成员重复相加。历史包必须通过新版本迁移，不能原地改写。

运行时会把这些定义编译为模型语义卡：定义、填报要求、事实粒度、测量类别、身份轴、所请求范围、兄弟指标区别和局部关系。
因此 description 必须解释业务含义，不能只重复短标题；例如并列核算方法的区别、消费与生产的区别、直接披露与派生的区别应明确。
固定分类是标准请求，不等于报告已披露事实。这里不写模型工具步骤、行分块数量或 Top；这些属于通用运行引擎。

### 测量合同的语义分层

新2.1.2及后续2.1格式作者版本使用 `semantic_contract_version: partitioned-semantics-v1`。`positive_definition` 表达该条款的正向测量概念；`interpretation_guidance` 保存阅读和解释说明；`distinguish_from` 列出须辨别的其他概念；`exclusions` 仅写明确不能支持本条款的情况。不得把标题、定义或完整描述复制进排除条件。易混概念本身不是整页的禁用词，阅读说明也不是硬性排除规则。

编译器检查字段完整性与确定性的自排除；业务审核仍须判断边界、方法及原标准含义。历史已发布合同保持原样，新增字段的默认值不得改变旧序列化内容或摘要。修复阅读语义采用新内容修订，不覆盖旧包。原编号、出处和原条款描述必须保留。

## 4. 语义元素 `elements.json`

一个 element 是机器需要从披露中形成的最小有类型字段，例如数值、单位、期间、边界、
污染物、方法或原因。

每个 element 必须明确：

- `element_id` 和所属 `metric_id`；
- `primary_type` 与必要的 `fallback_types`；
- `minimum/maximum` 基数；
- `requirement_level` 与 `null_allowed`；
- 枚举、单位维度、固定值和数值范围；
- 唯一 Core 绑定；
- 标准来源。

element 可使用：

- `concept_ids`：说明字段自身的标准语义；
- `value_concept_root_ids`：说明其值来自哪个开放概念树，例如“污染物”。

开放概念树不得阻止报告出现的新值。未能规范化的值仍需保留原文，后续再进入概念治理。

不允许用一个无约束的 JSON 字段承载多个语义元素。

## 5. 元素绑定

绑定只有三种：

| storage | 用法 |
|---|---|
| `core_field` | 数值、原文、单位、期间、边界等跨标准稳定字段 |
| `attribute` | 方法、原因、法规依据等模块专属类型化属性 |
| `dimension` | 污染物、场地、员工类别等用于分解和横向比较的维度 |

板块专属内容不得通过给 Core 增加固定列解决。若新增板块需要一种新的专属属性，仍绑定
到 `attribute_value`；只有至少两个不同标准板块都需要且语义完全一致时，才讨论 Core
升级。

## 6. 语义概念 `concepts.json`

语义概念是跨报告识别同一业务含义的标准化语言层。它不等于 metric，也不等于数据库行。
例如 `空气污染物排放量` 是 metric；`污染物` 是值域根概念；`二氧化硫` 是其下位概念。

每个概念必须声明：

- 命名空间化 `concept_id`；
- 中英文首选名称；
- 按语言组织的别名；
- 必要时的简称与公式，例如 `SO2`、`SO₂`；
- `broader/related/excluded` 关系；
- 若某个下位概念只适用于特定介质、范围或业务上下文，声明
  `applicable_context_concept_ids`；查询编译器据此过滤不适用的下位词；
- 语义描述和标准来源。

当同一分类维度中的概念不能同时成立时，应在概念之间对称声明
`excluded_concept_ids`，例如空气、水体和土壤三种污染介质。统一查询编译器会把正向主体
概念的排除关系编译为冲突词；不得在抽取代码中为某个模块另写介质关键词特判。

统一编译器会拒绝未知概念引用、循环上下位关系、自引用和重复词形。不得在概念中加入
Top K、检索权重、模型名称、提示词或逐指标执行脚本。公司特有说法不能未经治理直接写回
已发布标准包，应进入独立候选词治理流程。

`applicable_context_concept_ids` 是标准语义关系，不是运行参数。例如 COD/BOD 可声明只适用于
水体污染上下文，SO2/NOx 可声明只适用于空气污染上下文。指标未限定这类上下文时，编译器
仍保留全部候选；指标明确限定上下文时，只展开匹配或未限定适用范围的概念。不得在查询代码
中按条款 ID 或词面硬编码删除列表。

## 7. 指标关系 `relations.json`

指标关系只能使用统一类型：

- `alternative_group`：标准允许替代披露，不代表求和；
- `conditional_activation`：满足结构化条件时激活目标指标；
- `requires`：目标指标成立需要另一指标或上下文；
- `contextualizes`：一个数据点为另一数据点提供解释性上下文。

关系必须引用存在的 metric ID。条件必须使用结构化 predicate，不能写成供开发者猜测的
自然语言。自然语言只用于 `description`。

## 8. 枚举、维度和单位

- 通用状态、证据角色和通用单位引用 Core；
- 只属于一个标准板块的代码放在模块 `code_sets.json`；
- 维度 ID 必须表达稳定语义，报告原始值与规范化代码后续分别保存；
- 模块不得重新定义或覆盖 Core 中已有的 code set、dimension 或 unit ID。

## 9. 派生规则

只有标准明确允许，或平台明确标注为后续分析规则的计算才能进入 `derivations.json`。

规则必须声明：

- 目标指标和操作；
- 命名操作数；已有 metric 作为操作数时必须写 `source_metric_id`；
- 必须匹配的期间、边界和维度；
- 单位策略；
- 分母等安全条件；
- 执行阶段；
- 报告直接值是否优先。

抽取阶段不得静默执行 `data_processing` 规则。

当前统一操作只有 `ratio` 和 `sum`。`ratio` 必须有非零分母 guard；`sum` 的所有
操作数必须在期间、边界和适用维度上匹配，并通过 `unit_policy` 声明可兼容单位。
两种操作都只是后续数据处理合同，报告直接值优先。

## 10. 来源和权威等级

`sources.json` 只保存支撑标准含义的来源，并区分：

1. 法律或正式标准；
2. 官方数字分类法；
3. 官方复现或官方解释；
4. 非权威实施指引。

内部讨论表、提示词和模型生成内容不能成为标准要求的权威来源。

## 11. 编译准入

统一编译器必须拒绝：

- 重复或越界 ID；
- 未知 metric、element、source、code set、dimension 或 Core field 引用；
- 未知 concept 引用、重复词形或循环概念层级；
- 格式 `1.1` metric 缺少 `subject_concept_groups`；
- enum 没有 code set；
- metric 没有任何 element；
- alternative group 少于两个成员；
- conditional relation 没有 predicate；
- 派生规则操作数不足；
- 模块覆盖 Core 定义；
- Core 版本不兼容。

编译成功只表示结构和引用合法，不表示标准内容已经得到业务批准。业务批准通过
`manifest.status` 和新版本发布管理。

## 12. 格式2.1：原编号、核心条款与主要条款

严格一披露小号一包，例如E1-5、E1-6、E2-4；不能按产品优先级拆散或合并标准包。
每个包完整保留原始条款，不重新排列原编号，不将MDR范围引用复制为主题指标。

- `manifest.core_source_datapoint_ids`：该包核心条款原编号，必须存在于本包，另有`core_selection_reason`说明价值与区分依据。
- `profile.selection_kind=platform_major`：平台当前主要条款，必须属于对应包核心；核心不自动全部进入主要范围。
- `provenance`：保存冻结底表身份、工作簿hash、页、行、全部原始值与列头、原类型、条件、May、法规与阶段性字段、行hash。
- `measurement_contract`：六类模板、值属性、单位/转换资格、身份与比较轴、聚合限制、排除与比较条件。修正类型解释必须明确记录，不改原表。

类型化事实字段仍使用element，不将测量合同塞入无约束结果JSON。身份必须来自报告证据；定义、规范单位和采纳状态不能当作报告已经披露的事实。ESRS2通用披露的义务不统一套成mandatory_if_material；MDR仅随适用主题、对象及重要性使用。

`shared_requirements`固定MDR目标版本和原编号范围；空DR引用在注册表保留，由主题/用途解析，不从相邻条款猜DR。`context_requirements`固定ESRS2/MDR-M目标版本、原编号、适用源条款、用途及context_only输出策略。目标包、版本、原编号、digest均由注册校验；未知采纳状态不得激活政策/行动/目标分支。

`scope.compile_profile`分别返回所选输出、显式计算依赖闭包、解释上下文和跨包引用。只有所选输出进入结果范围及覆盖分母；未建模块必须明确报错，只有用户显式过滤主题才可缩小范围。

未来占位不得携带可执行源目录、版本、digest或runtime_path。先逐条分析全包并选择核心，再填写类型、身份、证据、比较及依赖合同，经离线和真实报告验收后成熟。示例和共用调用接口见[本阶段建设记录](../../docs/ESRS_STANDARD_PACKAGE_BUILD.md)。
