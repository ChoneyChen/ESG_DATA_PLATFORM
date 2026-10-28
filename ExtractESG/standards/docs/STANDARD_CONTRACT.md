# 标准条款合同：唯一权威与消费链路

日期：2026-10-28
本文件取代外部目录 `标准要求包/`（含 `标准要求包制作规范.md`、`契约参考/`、旧示例包）作为唯一规范。
外部目录只是早期快照，本仓库不读取、不依赖；条款链路的全部文件都在本仓库内。

## 1. 分层

```text
Core Schema（跨标准通用记录）
        ↓
模块源包（十个 JSON 文件，按标准版本目录）
        ↓ 确定性编译
编译包（单文件 package.json，登记在 registry）
        ↓ 增量编译
条款包（每条条款一个目录，五个文件）        ← 条款合同
        ↓ 阅读
平台条款策略（比较/呈现，声明式）           ← 平台策略，不是第二份科学定义
```

| 层 | 位置 | 负责 | 不负责 |
|---|---|---|---|
| Core Schema | `core/<version>/core.json`、`schemas/` | 记录类型、通用字段与状态 | 板块语义 |
| 模块源包 | `authoring/esrs/2023-set1/<module>/<version>/` | 数据点、元素、概念、关系、维度、代码集、派生、校验、来源 | 检索、提示词 |
| 编译包 | `releases/<package_id>/<version>/package.json` | 不可变机器合同（源摘要、校验和） | 人工补充 |
| 条款包 | `clauses/<package_id>/<version>/<原编号>/` | `clause / retrieval / evidence / acceptance / provenance` | 另一套定义 |
| 平台策略 | `contracts/platform-policy.esrs-2023-set1.json` | 比较维度、约束、呈现 | 身份轴、固定口径、单位、聚合、排除 |
| 固定口径 | `profiles/esrs/2023-set1/clause-fixed-axes.json` | 条款固定取值 | 平台策略 |
| 包简述名 | `profiles/esrs/2023-set1/package-titles.json` | 目录显示名 | 合同语义 |

固定口径分两种视图：字典需要**完整**固定维度（含 `all`、`aggregation_role`），
条款包 `publication.fixed_axes` 只保留与输出范围有关的子集。完整值以
`clause-fixed-axes.json` 为唯一来源，条款包是它的编译结果，两者不重复撰写。

## 2. 一个条款的合同是什么

`clauses/<package_id>/<version>/<原编号>/clause.json` 是该条款的唯一合同，包含：

- 身份：`clause_id`、`package_id`、`package_version`、`source_datapoint_id`、`metric_id`
- 概念：`positive_definition`、`description`、`distinguish_from`、`exclusions`
- 取值：`value_type`、`value_origin`、`canonical_unit`、`unit_dimension`
- 输出口径：`output_scopes`（期间、主体、聚合、方法、固定轴）
- 发布：`publication`（聚合、比较轴、固定轴）
- 摘要：`contract_digest`

同目录其余四个文件：`retrieval.json`（检索词与结构锚点）、`evidence.json`（字段证据要求）、
`acceptance.json`（验收样例与最低报告数）、`provenance.json`（来源包摘要与编译器版本）。

## 3. 谁消费这份合同

| 消费者 | 读取 | 用途 |
|---|---|---|
| 标准目录 API | `clauses/catalog.json` + `clause.json` | 前端目录、条款详情 |
| 平台字典 | 编译包合同 + 固定口径 + 平台策略 | 观测身份、比较、单位、聚合 |
| 定向提取 | 条款包（检索、证据、口径） | 选页、检索、映射、guard |
| foundation 阅读适配 | `foundation_contracts()` 校验身份/维度 | 提示词用的英文阅读文案是适配层，不构成第二份定义 |
| 审核归一 | `database/scripts/review-evidence.mjs` | 边界、方法、维度值归一，导入与审核共用 |
| 数据库投影 | `clauses/` → `scientific-model.mjs` | `clause_versions`、观测维度与证据 |
| 真实验收 | 条款合同摘要 | 验收与条款合同绑定 |

## 4. 平台字典如何生成

`PlatESG/scripts/build-dictionary.py` 不再写任何条款：

```text
编译包 measurement_contract（身份轴、排除、比较轴、单位、聚合）
+ profiles/clause-fixed-axes.json（固定取值）
+ contracts/platform-policy.esrs-2023-set1.json（平台比较与呈现策略）
→ metric-dictionary.v1.json
```

构建期检查：

1. 平台策略必须覆盖平台主要清单的每个原编号；
2. 身份轴（`required_dimensions`、`fixed_dimensions`）必须由标准包声明，或属于
   `PLATFORM_IDENTITY_EXTENSIONS` 中登记且带原因的扩展；
3. 与上一版字典比较，科学字段变化必须显式放行；
4. 条款固定口径变化必须来自标准侧文件。

验证基线：以历史字典为输入重建，输出与封存版本**字节一致**（`c4d73f54…`）。

## 5. 旧任务的冻结

- 任务在创建时冻结 `runtime_digest`、包摘要与标准快照；
- 之后修改条款、字典或平台策略不会改写既有任务与已发布观测；
- 标准切换、字典重建只影响新任务；旧发布语义保持不变。

## 6. 已退役

| 退役项 | 说明 |
|---|---|
| `ExtractESG/standards/compat/packages`、`packages` 符号链接 | 旧包路径别名；测试改为直接读取 `history/authoring/` |
| `PlatESG/scripts/build-dictionary.py` 的条款字面量 | 迁入 `contracts/platform-policy.esrs-2023-set1.json` |
| 外部目录 `标准要求包/` 作为规范来源 | 由本文件取代；代码中没有对该目录的任何读取 |

保留的别名：`catalogs → catalog`、`dist → releases`、`placeholders → registry/planned`，
它们是运行时路径别名，不属于标准内容。
