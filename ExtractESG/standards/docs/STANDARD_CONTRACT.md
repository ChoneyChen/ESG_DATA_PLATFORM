# 标准条款合同：唯一权威与消费链路

日期：2026-10-29
本文件取代外部目录 `标准要求包/`（含 `标准要求包制作规范.md`、`契约参考/`、旧示例包）作为唯一规范。
外部目录只是早期快照，本仓库不读取、不依赖；条款链路的全部文件都在本仓库内。

## 1. 分层

```text
Core Schema（跨标准通用记录）
        ↓
模块源包（十个 JSON 文件，按标准版本目录）
        ↓ 确定性编译
编译包（单文件 package.json，登记在 registry）      ← 标准科学源
        ↓ 条款作者源（每条一个文件）+ 共享词表 + 选择清单
条款作者源（clause-authoring/<clause_key>.json）
        ↓ 按条编译
条款包（每条一个目录，五个文件）                    ← 条款合同，链路唯一接口
        ↓ 阅读
提取引擎（foundation 与 standard 两条路线）          ← 只读条款合同
```

| 层 | 位置 | 负责 | 不负责 |
|---|---|---|---|
| Core Schema | `core/<version>/core.json`、`schemas/` | 记录类型、通用字段与状态 | 板块语义 |
| 模块源包 | `authoring/esrs/2023-set1/<module>/<version>/` | 数据点、元素、概念、关系、维度、代码集、派生、校验、来源 | 检索、提示词、阅读规则 |
| 编译包 | `releases/<package_id>/<version>/package.json` | 不可变机器合同（源摘要、校验和） | 人工补充 |
| 条款作者源 | `clause-authoring/<clause_key>.json` | 数据种类、阅读规则、分组、关系、固定展示名 | 包科学定义 |
| 共享词表 | `profiles/esrs/2023-set1/clause-lexicon.json` | 条款规则引用的词表（正则/短语） | 条款专属规则 |
| 选择清单 | `profiles/esrs/2023-set1/metric-profiles.json` | base16/priority32/quantitative/universal 等集合 | 任何单条条款语义 |
| 条款包 | `clauses/<package_id>/<version>/<原编号>/` | `clause / retrieval / evidence / acceptance / provenance` | 另一套科学定义 |
| 条款索引 | `clauses/catalog.json` | 包（来源）与扁平的 `clauses`（可选择单位） | 选择策略 |
| 平台策略 | `contracts/platform-policy.esrs-2023-set1.json` | 平台比较维度、约束、呈现 | 条款阅读规则、身份轴、单位、聚合、排除 |
| 固定口径 | `profiles/esrs/2023-set1/clause-fixed-axes.json` | 条款固定取值（完整视图） | 平台策略 |

**一条条款就是一个单位。** 包只是原著录的整合名与版本来源；选择、检索、提取、审核、验收都以
`clause_key`（原编号）为键。`clause_digest` 只描述该条条款自身，包版本变化不会改变未改动条款的摘要。

## 2. 一个条款的合同是什么

`clauses/<package_id>/<version>/<原编号>/clause.json` 是该条款的唯一合同，包含：

- 身份：`clause_key`（稳定原编号）、`clause_digest`（条款自身摘要）、`clause_id`（含包版本，供来源追溯）、
  `package_id`、`package_version`、`source_datapoint_id`、`metric_id`
- 种类：`data_kind`（`quantitative` / `qualitative`）与 `value_type`（`numeric` / `statement`）
- 概念：`positive_definition`、`description`、`distinguish_from`、`exclusions`、`labels`、`display_label`、`display_definition`
- 取值：`canonical_unit`、`unit_dimension`、`value_origin`
- 分组与关系：`group`、`text_group`、`group_terms`、`relationships`（父条款、占比伙伴、同族条款、子项词、子项排除）
- 精确行名：`exact_labels`
- **阅读规则**：`reading`（算子、规则列表、解析后的词表、是否允许监管上限）
- 输出口径：`output_scopes`（期间、主体、聚合、方法、固定轴）
- 发布：`publication`（聚合、比较轴、固定轴）
- 摘要：`contract_digest`（兼容字段）

同目录其余四个文件：`retrieval.json`（检索词与结构锚点）、`evidence.json`（字段证据要求）、
`acceptance.json`（验收样例与最低报告数）、`provenance.json`（来源包摘要与编译器版本）。

`reading.operator` 取值：`numeric_label`、`ghg_scope`、`pollutant`、`water`、`waste`、`statement`。
规则种类：`require_any`、`require_all`、`forbid_any`、`forbid_any_unless`、`forbid_if_all`、
`reject_component`、`flag_review`、`unit_dimension`、`ratio_required`、`ghg_unit`、`ghg_scope`、
`ghg_scope_composition`、`pollutant_medium`、`water_concept`、`waste_concept`。
词表项以 `re:` 开头表示正则，其余为折叠后的子串。

## 3. 谁消费这份合同

| 消费者 | 读取 | 用途 |
|---|---|---|
| 标准目录 API | `clauses/catalog.json` + `clause.json` | 前端目录、条款详情、按条选择 |
| foundation 提取 | 条款包：`data_kind`、`reading`、`retrieval`、`relationships`、`exact_labels` | 指标目录、提示词定义、guard、行名恢复、占比/同族行 |
| standard 提取 | 条款包 + 编译包 | 查询编译加入条款检索词/定义/排除；包元素仍提供结构语义 |
| 选页（Document） | 条款包 `retrieval.json` + `metric-profiles.json` | 按所选条款词表选页（`page_selection=clauses`） |
| 平台字典 | 编译包 `measurement_contract` + 固定口径 + 平台策略 | 观测身份、比较、单位、聚合（平台层） |
| 审核归一 | `database/scripts/review-evidence.mjs` + 字典 | 边界、方法、维度值归一，导入与审核共用 |
| 数据库投影 | `clauses/` → `scientific-model.mjs` | `clause_versions`、观测维度与证据 |
| 真实验收 | 条款合同（包科学摘要） | 验收与条款科学合同绑定 |

**提取链路不再保留任何条款清单或条款分支。** `foundation/catalog.py` 从条款包构建指标；
`foundation/guards.py` 是通用规则求值器；`foundation/kernel.py` 的占比伙伴、同族行、精确行名
全部来自条款包；`page_selection.py` 没有固定关键词表；`foundation/specs.py` 已移除。
唯一仍保留的条款文本是 `clause-authoring` 中的作者源，它属于标准树，不属于提取链路。

## 4. 数据种类合同

提取输出只有两类，各自有独立合同（`ExtractESG/contracts/src/esg_targeted/data_contracts.py`）：

- **定量**：`value_raw` 必填；`comparator` 决定 `value_numeric` / `value_lower` / `value_upper` 的组合；
  上下界有序，数值不得越界。
- **定性**：`statement_raw` 必填，不携带数值或单位。

`ResultContractValidator` 在成果包校验时对两类记录逐一执行合同；不匹配的记录使成果包拒绝导出。
条款的 `data_kind` 决定它应产出哪一类记录。

## 5. 条款身份与变更

- 修改一条条款：改 `clause-authoring/<clause_key>.json`（或编译包科学源）后重建条款目录，
  只有该条的 `clause_digest` 变化；同包其它条款保持原摘要。
- `clause_id` 仍含包版本，用于追溯具体来源包；链路的稳定选择键是 `clause_key`。
- 任务在创建时冻结 `runtime_digest`、包摘要与标准快照；之后修改条款、字典或平台策略不会改写
  既有任务与已发布观测；标准切换、字典重建只影响新任务。
- 验收绑定包 `measurement_contract` 的科学摘要（`original_contract_digest`）：包科学变化会使旧验收失效；
  仅阅读规则/展示文本变化不改变验收。

## 6. 字典如何生成

`PlatESG/scripts/build-dictionary.py` 不写任何条款：

```text
编译包 measurement_contract（身份轴、排除、比较轴、单位、聚合）
+ profiles/clause-fixed-axes.json（固定取值）
+ contracts/platform-policy.esrs-2023-set1.json（平台比较与呈现策略）
→ metric-dictionary.v1.json
```

构建期检查：平台策略覆盖平台主要清单；身份轴由标准包或登记扩展声明；科学字段变化需显式放行；
固定口径只来自标准侧。验证基线：以历史字典重建，输出与封存版本**字节一致**（`c4d73f54…`）。

## 7. 已退役

| 退役项 | 说明 |
|---|---|
| `foundation/catalog.py` 的 39 条指标字面量 | 改为从条款包构建 |
| `foundation/specs.py` 的条款 spec 表 | 迁移为 `clause-authoring` 规则；运行时不导入 |
| `foundation/guards.py` 的 31 个条款分支 | 改为通用 reading 求值器 |
| `page_selection.py` 的 17 组固定关键词 | 改为条款检索词；`GROUPS` 已删除 |
| 跨条款中立理解缓存（`report_cache.py`、共享 `sources/`） | 按用户要求清除，不再复用 |
| `ExtractESG/standards/compat/packages`、`packages` 符号链接 | 旧包路径别名；测试改为直接读取 `history/authoring/` |
| 外部目录 `标准要求包/` 作为规范来源 | 由本文件取代；代码中没有对该目录的任何读取 |

保留的别名：`catalogs → catalog`、`dist → releases`、`placeholders → registry/planned`，
它们是运行时路径别名，不属于标准内容。

## 8. 构建命令

```sh
# 条款作者源 → 条款包
python ExtractESG/standards/scripts/build_clause_catalog.py
python ExtractESG/standards/scripts/build_clause_catalog.py --check

# 平台字典
python PlatESG/scripts/build-dictionary.py
```
