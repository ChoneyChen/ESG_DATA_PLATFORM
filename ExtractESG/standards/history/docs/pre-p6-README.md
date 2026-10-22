# ExtractESG Standard Packages

项目级现状、跨模块关系和 E1-5/E1-6 验证计划见仓库根目录
[`../../README.md`](../../README.md)。本文只维护标准要求包本身的规则。

本模块只负责定义、版本化、编译和校验 ESG 标准要求包。它不读取 Document IR，
不检索报告，不调用模型，也不生成提取结果。

2026-10-04 当前新任务使用格式 `2.1`、版本 `2.1.1 draft` 的 **41 个包**：E1–E3全部25个披露小号包（322条），ESRS 2共用12包（146条）及MDR共用4包（44条）。完整原编号、包内核心条款、平台主要条款、共享引用及使用接口见 [建设记录](../docs/ESRS_STANDARD_PACKAGE_BUILD.md)。编译不代表真实抽取质量或业务批准。
下一版保留语义关系图，以报告级证据簇连接主披露/独立延伸/方法/计算/视图；模型角色和执行计划不进入标准包。
系统需求及迁移计划见 系统重塑方案（已归档到 Git 历史），不因换引擎机械升级标准版本。

## 边界

```text
Core Schema                    跨标准稳定的结果记录契约
  +
Standard Module Source         某一标准板块的数据点和语义要求
  ↓ deterministic compile
Compiled Standard Package      后端可直接加载的单文件机器契约
```

当前新增：

- `core/1.2.0/core.json`：补充体积、ratio、计数、水强度、碳价单位；六类结果记录保持；
- `catalogs/esrs/2023-set1/ig3-original.json`：完整1,184条原始目录记录；
- `registry.json`：99个独立披露小号的建设状态，41个编译draft，58个未来占位；
- 每个已建包的 `manifest.core_source_datapoint_ids`：包内核心条款及理由；
- `profiles/.../platform-major.json`：用户注释二指定的49项主要条款，必须属于对应包核心；
- `profiles/.../package-core/`：41个包的核心范围；`environment-all.json`：E1–E3全部322项；
- `scope.py`：固定版本的ESRS2/MDR上下文及分支解析、范围编译与目录完整性校验；
- `placeholders/`：未来主题、ESRS1原则及其他框架的非执行占位；
- `scripts/build_esrs_environment.py`：一次性完整建设与可重复重建入口，使用冻结目录和人工语义数据。

核心条款属于包，主要条款属于平台选型；核心不自动全部成为主要。法定义务、语义角色与两种产品选择层级独立。未建主要条款不被静默删除；显式选择E1/E2/E3后可输出16项。

历史版本与基础建设记录：

- `extractesg.core@1.0.0`：E2-4 使用的平台 Core Schema；
- `extractesg.core@1.1.0`：向后兼容补充能源、GHG、货币和强度单位的 Core Schema；
- `esrs.2023-set1.e2-4@1.0.0`：ESRS E2-4 模块源包，状态为 `draft`；
- `esrs.2023-set1.e1-5@1.0.0`：ESRS E1-5 模块源包，状态为 `draft`；
- `esrs.2023-set1.e1-6@1.0.0`：ESRS E1-6 模块源包，状态为 `draft`；
- `esrs.2023-set1.e1-5@1.1.0`：增加能源载体层级、互斥语义、报告实体与指标聚合角色；
- `esrs.2023-set1.e1-6@1.1.0`：增加 Scope/繁简/公式别名、Scope 1 专用概念、报告实体与指标聚合角色；
- `esrs.2023-set1.e1-5@1.2.0`：历史能源运行时版本；
- `esrs.2023-set1.e1-6@1.3.0`：历史 GHG 版本，结构清单成员改为可独立举证的定性事实；
- `esrs.2023-set1.e1-6@1.4.0`：历史 GHG 版本，逐指标补充事实粒度、语义判别轴、易混淆指标族、证据形态和抽取/派生策略；
- `esrs.2023-set1.e1-6@1.5.0`：历史 GHG 运行时版本，补充证据角色、指标族、依赖/派生策略并统一定性 assertion 粒度；
- E1-5/E1-6/E2-4 `2.0.0`：历史draft包，采用格式 `2.0`；新2.1包保留其元素、关系和派生合同，旧编译产物保存在`.retired`；
- `module format 1.1`：平台级语义概念、别名、缩写、公式、上下位和排除关系；
- 确定性编译器、JSON Schema 和本地契约测试。

现有 `ESRS_E2-4_填写规范与字段说明_v1.1.xlsx` 只作为本次 E2-4 内容整理的输入样本，
不是运行时标准包。新的运行时权威产物是编译后的 `package.json`。

v2 内容生成入口为 `scripts/build_v2_packages.py`。它生成新的 `2.0.0` 源目录，不改历史源包；随后使用现有编译器发布到 `dist/<package_id>/2.0.0/package.json`。成功发布后，旧编译版本自动移入 `.retired`，历史任务快照保持可读。

`disclosure_topic` 说明适合共同理解的披露主题，`disclosure_role` 区分主指标、延伸、说明、派生或展示。关系新增 `decomposes/method_variant/separate_disclosure/calculation_dependency/explains/presentation_of` 和 `identity_axes`。这些是标准语义，不是模型调用命令。编译器检查引用、关系结构和依赖/分解循环；模型、Top、prompt 仍由通用运行引擎管理。

## 目录

```text
standard_packages/
├── core/{1.0.0,1.1.0,1.2.0}/core.json
├── catalogs/esrs/2023-set1/{ig3-original,registry}.json
├── profiles/esrs/2023-set1/{platform-major,environment-all}.json
├── profiles/esrs/2023-set1/package-core/<小号>.json
├── placeholders/<framework>/<version>/<小号>/placeholder.json
├── packages/esrs/2023-set1/<小号>/2.1.1/  # 共41包，下方为历史结构
├── packages/esrs/2023-set1/e2-4/{1.0.0,2.0.0}/
├── packages/esrs/2023-set1/e1-5/{1.0.0,1.1.0,1.2.0,2.0.0}/
├── packages/esrs/2023-set1/e1-6/{1.0.0,1.1.0,1.2.0,1.3.0,1.4.0,1.5.0,2.0.0}/
│   ├── manifest.json
│   ├── metrics.json
│   ├── elements.json
│   ├── concepts.json
│   ├── relations.json
│   ├── code_sets.json
│   ├── dimensions.json
│   ├── derivations.json
│   ├── validation_rules.json
│   └── sources.json
├── dist/<package_id>/2.1.1/package.json
├── dist/.retired/<package_id>/<old_version>/package.json
├── schemas/
├── scripts/build_e1_packages.py
├── scripts/build_v2_packages.py
├── scripts/build_esrs_environment.py
├── scripts/esrs_semantics.py
├── src/esg_standard_packages/
├── tests/
└── docs/AUTHORING_GUIDE.md
```

编译发布采用临时文件校验后原子替换。发布内容不同必须升版本；不能原地覆盖已有版本。
标准目录下新版本成功发布后，自动移走同包的更低语义版本编译目录至 `.retired`，
新任务目录不再列出旧版。源包、已排队快照和历史结果不删除；退役目录可恢复，历史任务可按版本加载。
失败编译不触碰旧包；其他标准包、非版本目录和符号链接目录不作为清理目标。

## Core Schema

Core 只定义所有标准都能使用的六类记录：

| Record type | 记录粒度 |
|---|---|
| `reporting_task` | 一份报告、一个固定 IR revision、一个标准数据点 |
| `quantitative_observation` | 一个数值、期间、边界和维度组合 |
| `qualitative_assertion` | 一个可独立举证的定性断言或清单项 |
| `attribute_value` | 挂在任务、量化事实或定性断言上的类型化标准专属属性 |
| `dimension_value` | 一个父记录和一个分解维度值 |
| `evidence_reference` | 一个结果记录到 Document IR/PDF 的可复现证据引用 |

污染物、微塑料、BAT、员工类别、温室气体范围等领域内容不得加入 Core。标准专属
元素通过 `attribute_value` 或 `dimension_value` 绑定，避免每新增一个板块就修改平台表。

## E2-4 模块

E2-4 源包包含：

- 20 个命名空间化指标；
- 127 个有类型、基数、必填性和 Core 绑定的语义元素；
- 36 个有来源、可校验的语义概念；
- 3 条指标关系：微塑料替代组、较低优先级方法条件、IED/BREF 条件组；
- 4 条只允许在后续数据处理阶段执行的比例派生规则；
- E2-4 专用枚举、维度、质量规则和来源。

指标 ID 使用 `esrs.2023-set1.e2-4.dpNN`。旧的 `E2-4_02` 保存在
`source_datapoint_id`，不能作为平台全局主键。

## E1-5 与 E1-6 模块

E1-5 `1.1.0` 源包包含 23 个指标、203 个元素、27 个概念、7 个维度、3 条关系、
7 条后续数据处理派生规则和 11 条校验规则。`E1-5_18` 按法规正文建模为
能源消费/净收入强度，不沿用 IG 3 中的 `percent` 标签。

E1-6 `1.1.0` 源包包含 35 个指标、211 个元素、35 个概念、10 个维度、4 条关系、
4 条派生规则和 11 条校验规则。其中分开 Scope 1/2/3、location/market-based、
biogenic CO2、contractual instruments 和 Scope 3 categories，并声明 GHG Protocol/ISO 替代组和
适用的 phase-in 条件。

E1-6 `1.5.0` 仍有 35 个指标，并将运行时事实合同补齐到 229 个元素。每个指标可以声明
`fact_grain`、`measurement_kind`、`required_semantic_discriminators`、`confusable_metric_ids`、
`evidence_form`、`extraction_strategy`、`identity_axes`、`metric_family`、`evidence_roles`、
`depends_on` 和 `derivation_policy`。这些是标准含义、证据需求与结果身份元数据，不是 Top N、模型名称
或提示词脚本。09/10 等互斥方法指标可由通用抽取器据此获得兄弟指标对照上下文，不需要条款号分支。

两个包的十个源 JSON 由 `scripts/build_e1_packages.py` 确定性构建；生成脚本是可审查的
内容维护器，运行时仍只读取 `dist/.../package.json`。

## 编译与校验

在本目录执行：

```bash
python -m pip install -e '.[dev]'

PYTHONPATH=src python scripts/build_esrs_environment.py --publish
esg-standard-package catalog-check --root .

esg-standard-package profile-compile --root . \
  --profile profiles/esrs/2023-set1/platform-major.json \
  --topics E1 E2 E3 --output /tmp/platform-major-e1-e3.json

pytest
```

本轮所有2.1包使用 `core/1.2.0/core.json`。单包仍可使用compile/check命令；旧源包与
`dist/.retired` 中的编译产物仅用于复现旧任务。

编译过程不写时间戳，也不依赖文件遍历顺序。相同 Core 和模块源文件必须产生字节完全
一致的 `package.json`。编译产物内保存所有源文件 SHA-256 和聚合 `source_digest`。

## 当前阶段

本阶段没有建设：

- Document IR 检索规则；
- BM25/Embedding 权重、查询模板或候选排序参数；
- 模型提示词与模型调用；
- 抽取任务执行器；
- 结果工作簿写入器；
- E1-5/E1-6 的真实报告业务验证与发布批准；
- S1 等后续模块。

后续模块必须遵守 `docs/AUTHORING_GUIDE.md`，不能通过新增板块专用 Python 代码接入。

标准包可以声明标准语义本身包含的同义词、简称、化学式和上下位概念，但不得声明检索
权重、Top K、模型提示词或执行顺序。抽取引擎统一把这些语义概念编译为具体检索请求。
