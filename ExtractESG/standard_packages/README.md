# ExtractESG Standard Packages

项目级现状、跨模块关系和 E1-5/E1-6 验证计划见仓库根目录
[`../../README.md`](../../README.md)。本文只维护标准要求包本身的规则。

本模块只负责定义、版本化、编译和校验 ESG 标准要求包。它不读取 Document IR，
不检索报告，不调用模型，也不生成提取结果。

## 边界

```text
Core Schema                    跨标准稳定的结果记录契约
  +
Standard Module Source         某一标准板块的数据点和语义要求
  ↓ deterministic compile
Compiled Standard Package      后端可直接加载的单文件机器契约
```

当前已经建设：

- `extractesg.core@1.0.0`：E2-4 使用的平台 Core Schema；
- `extractesg.core@1.1.0`：向后兼容补充能源、GHG、货币和强度单位的 Core Schema；
- `esrs.2023-set1.e2-4@1.0.0`：ESRS E2-4 模块源包，状态为 `draft`；
- `esrs.2023-set1.e1-5@1.0.0`：ESRS E1-5 模块源包，状态为 `draft`；
- `esrs.2023-set1.e1-6@1.0.0`：ESRS E1-6 模块源包，状态为 `draft`；
- `esrs.2023-set1.e1-5@1.1.0`：增加能源载体层级、互斥语义、报告实体与指标聚合角色；
- `esrs.2023-set1.e1-6@1.1.0`：增加 Scope/繁简/公式别名、Scope 1 专用概念、报告实体与指标聚合角色；
- `esrs.2023-set1.e1-5@1.2.0`：当前能源运行时版本；
- `esrs.2023-set1.e1-6@1.3.0`：当前 GHG 运行时版本，结构清单成员改为可独立举证的定性事实；
- `module format 1.1`：平台级语义概念、别名、缩写、公式、上下位和排除关系；
- 确定性编译器、JSON Schema 和本地契约测试。

现有 `ESRS_E2-4_填写规范与字段说明_v1.1.xlsx` 只作为本次 E2-4 内容整理的输入样本，
不是运行时标准包。新的运行时权威产物是编译后的 `package.json`。

## 目录

```text
standard_packages/
├── core/{1.0.0,1.1.0}/core.json
├── packages/esrs/2023-set1/e2-4/1.0.0/
├── packages/esrs/2023-set1/e1-5/{1.0.0,1.1.0,1.2.0}/
├── packages/esrs/2023-set1/e1-6/{1.0.0,1.1.0,1.2.0,1.3.0}/
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
├── dist/esrs.2023-set1.e2-4/1.0.0/package.json
├── dist/esrs.2023-set1.e1-5/1.2.0/package.json
├── dist/esrs.2023-set1.e1-6/1.3.0/package.json
├── dist/.retired/<package_id>/<old_version>/package.json
├── schemas/
├── scripts/build_e1_packages.py
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

两个包的十个源 JSON 由 `scripts/build_e1_packages.py` 确定性构建；生成脚本是可审查的
内容维护器，运行时仍只读取 `dist/.../package.json`。

## 编译与校验

在本目录执行：

```bash
python -m pip install -e '.[dev]'

esg-standard-package compile \
  --core core/1.0.0/core.json \
  --module packages/esrs/2023-set1/e2-4/1.0.0 \
  --output dist/esrs.2023-set1.e2-4/1.0.0/package.json

esg-standard-package check \
  --package dist/esrs.2023-set1.e2-4/1.0.0/package.json

pytest
```

E1 编译时把 `--core` 换为 `core/1.1.0/core.json`，当前任务选择对应的
`packages/esrs/2023-set1/e1-5/1.2.0` 或 `e1-6/1.3.0`；旧源包与
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
