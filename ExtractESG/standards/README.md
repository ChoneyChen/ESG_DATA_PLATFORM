# 标准系统：唯一作者源与显式发布身份

本目录是标准系统当前唯一源。旧 `ExtractESG/standard_packages` 是兼容符号链接，不维护第二套代码。项目总入口是 [根 README](../../README.md)，物理结构与回滚依据见 [结构迁移记录](../../docs/STRUCTURE_MIGRATION.md)。

当前登记99个草稿包，覆盖原表全部1,126个独立原编号：48个原机械生成包已在 `2.3.0` 完成509条中文标签与正向定义撰写，11个重点条款包为 `2.2.0`，E1-5、E2-4为 `2.1.2`，其余38个为 `2.1.1`。99个包全部标为 `authored_draft`，中文标签、领域指导与核心选择均已人工确认；结构可编译、真实报告验收与业务批准仍分别表达，三者不能互相替代。完整原表保留1,184行、1,126个独立原编号和58条共用MDR引用。

## 修改位置

| 目录 | 职责 | 修改规则 |
|---|---|---|
| `catalog/esrs/2023-set1/ig3-original.json` | 用户原表快照及原始元数据 | 保留原编号，不静默换成其他版本 |
| `authoring/esrs/2023-set1/<小号>/<版本>/` | 当前活动作者源 | 一小号一包；已发布内容修改须新版本 |
| `profiles/` | 平台主要、包内核心、完整及自定义范围 | 只定义选择，不复制条款语义 |
| `schemas/core/`、`schemas/*.json` | Core及机器合同 | 包格式/内容/Core/字典分别版本化 |
| `registry/esrs/2023-set1/registry.json` | 活动身份、源摘要、建设状态和共用依赖 | 固定版本，不扫描目录猜最新 |
| `registry/releases.json` | 全部98个发布版本路径和SHA256 | 发布器登记，既有摘要不改写 |
| `registry/planned/` | 其他框架（GRI、SASB、IFRS S1/S2）与ESRS 1占位 | 不伪造可执行包 |
| `releases/` | 99活动与57退役编译版本，共156个身份 | 不手改；按历史身份读取 |
| `history/authoring/` | 历史作者快照 | 只读审计 |
| `history/builders/` | 原阶段生成脚本及语义常量 | 不作为活动入口 |
| `src/esg_standard_packages/` | 唯一编译、验证、范围解析 | 生产构建仅读作者源 |
| `compat/` | 旧路径符号链接 | 不新增可编辑源 |

核心条款属于包，主要条款属于平台范围。主要条款必为所属包核心，包内其他核心不会自动成为平台主要。`platform-major.json`完整保留用户49个原编号；E1–E3显式范围16项就绪。其余33项已编译，但在平台字典中仍为 `planned`，直到逐条人工复核完成（`PlatESG/scripts/build-dictionary.py` 只把 `authored_draft` 标为 ready）。身份、方法、计算操作数等依赖不扩充输出及覆盖分母。

## 唯一构建入口

项目根目录，按项目Python环境配置 `PYTHONPATH=ExtractESG/standards/src`：

```bash
python -m esg_standard_packages build
python -m esg_standard_packages catalog-check --root ExtractESG/standards
python -m esg_standard_packages profile-compile \
  --root ExtractESG/standards \
  --profile ExtractESG/standards/profiles/esrs/2023-set1/platform-major.json \
  --topics E1 E2 E3 --output /tmp/esrs-major-scope.json
```

`build`只读重编译并核对注册、原表、既有内容及全部发布摘要。新作者版本须显式更新活动登记；`build --publish`创建缺失版本并生成发布登记，不覆盖既有文件。登记失败时未登记文件不能进入新任务，重试先核对内容。`compile`是低层离线合同/夹具工具，不是活动版本发布入口。

旧 `build_esrs_environment.py`、`build_e1_packages.py`、`build_v2_packages.py`均转入同一作者源构建器；历史原脚本保存在 `history/builders/`。旧 `packages/`、`dist/`、`catalogs/`、`core/`、`placeholders/`路径仍能定位同一实体。

## 语义合同与历史读取

新修订把 `positive_definition`（目标含义）、`interpretation_guidance`（阅读说明）、`distinguish_from`（易混概念）和 `exclusions`（确切排除条件）分别保存。E1-5、E1-6、E2-4共78条已建立新2.1.2修订。编译入口拒绝将条款自身定义放入排除条件；检测范围是确定性的词面自排除与字段完整性，不能代替业务语义审核。旧2.1.1不可变成果继续按原字节和摘要解析，详情接口投射只读 `semantic_diagnostics` 提醒，绝不静默修正历史源或批准记录。平台49个主要原编号及E1–E3的16项选择保持不变。

## 验证与后续建设

验证覆盖原序号、全目录、主要与核心关系、ESRS 2/MDR固定接口、依赖/输出分离、不可变发布和历史解析。结构迁移测试另覆盖未登记新目录不自动选中、摘要损坏失败关闭、只读重编译、中国石油原固定标准身份。

后续逐条分析语义及核心选择，建设作者源、身份/证据/单位/方法合同与测试，再登记新版本并独立验收。占位和结构草稿不能标为业务成熟。迁移前完整说明留在 [历史手册](history/docs/pre-p6-README.md)，旧现状仅用于审计。
