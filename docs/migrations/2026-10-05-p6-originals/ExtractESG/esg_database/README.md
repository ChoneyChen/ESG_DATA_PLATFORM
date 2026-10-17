# ExtractESG Result Database

项目级现状、跨模块关系和新对话交接见仓库根目录 [`../../README.md`](../../README.md)。
本文维护结果数据库、候选导入、审核和迁移方式。

这是 ExtractESG 最终提取结果数据库的本地雏形。它只保存从 ESG 报告中得到的
披露事实，不承担公司主数据、爬取文件索引、用户系统、同行基准、风险评分或
PlatESG 分析任务。

2026-10-02 已接入真实 Result Bundle 候选导入器、批次幂等、身份冲突回滚及事实/映射独立审核 CLI。中性 SourceFact 保存源身份/原值/物理来源，标准属性只属于映射投影；旧包没有可靠中性事实时按 job/record 保守入库，不凭同值合并。
执行 completed、合同通过和模型裁决仍不等于 approved。HTTP/UI 数据库流程、正式 publication 与权限服务尚未部署。详细边界见 [升级实施报告](../docs/UPGRADE_IMPLEMENTATION_REPORT.md)。

## 数据库方言

正式契约使用 PostgreSQL SQL：

- 迁移文件：`migrations/*.sql`
- 表和约束：PostgreSQL 17+ 兼容语法
- 本机运行：PGlite 0.5.4，使用项目目录内持久化的 PostgreSQL 数据文件
- 服务器运行：正式 PostgreSQL，可直接执行同一批迁移

PGlite 只是这台尚未安装 PostgreSQL/Docker 的 Mac 上的单用户开发运行时，不是
生产服务器，也不能代替生产 PostgreSQL 的并发、备份和运维能力。

## 数据边界

```text
source_report_refs
  -> esg_disclosures
       -> structured_values
       -> qualitative_claims
       -> disclosure_dimensions
       -> disclosure_sources
       -> disclosure_standard_mappings

canonical_concepts
  -> esg_disclosures

standard_frameworks
  -> framework_versions
  -> standard_requirements
```

核心原则：

- 一行 `esg_disclosures` 表示一条口径明确的报告披露，而不是“公司+年份+指标”。
- 报告原始名称、原始文本和原始值永远保留。
- 数值、布尔和枚举使用类型化列，不把所有值塞入文本或 JSON。
- 部门、地区、业务线、方法和边界进入可扩展维度表。
- 无法映射的公司特有披露可以用 `mapping_status=unmapped` 保存。
- 只有 `quality_status=approved` 的记录进入 `esg_api` 对外视图。

## 本地使用

本目录依赖 Node.js 20+ 和 pnpm。当前 Codex 工作区已经提供二者。

```bash
pnpm install
pnpm db:init
pnpm db:status
pnpm db:verify
```

`scripts/run-node.sh`会优先使用系统Node；若系统未安装，则自动寻找Codex工作区
提供的Node运行时。

载入独立演示数据：

```bash
pnpm db:demo
pnpm db:status
pnpm db:verify
```

重建本地库：

```bash
pnpm db:reset
pnpm db:init
```

本地数据库位于 `.local/pglite/`，已被 Git 忽略。`db:reset` 只删除这个目录，
不会触碰 OCR、Document IR 或其他项目文件。

## 候选导入与独立审核

先填写明确上游元数据 JSON，不从文件名猜测组织或报告版本：

```json
{
  "upstream_system": "your-report-registry",
  "organization_ref": "organization-id",
  "report_ref": "report-id",
  "report_version_ref": "immutable-report-version-id",
  "source_artifact_ref": "immutable-pdf-reference",
  "report_title": "报告标题"
}
```

可提供真实 PDF `source_sha256`；同报告版本已有 hash 时内容变更拒绝导入。不要把任何凭据写进该文件。

```bash
pnpm db:import --bundle /absolute/path/to/job-result --report-metadata /absolute/path/to/report-metadata.json
pnpm db:review --decision-file /absolute/path/to/decision.json
```

审核文件二选一填写 `disclosure_id` 或 `mapping_id`，并填写 `decision`（approved/rejected/review_required）、`operator_ref` 与 `reason`。每次审核单独记录；批准事实不会自动批准标准映射，也不会发布版本。导入返回批次 ID、数量、candidate 和 not_published。

导入要求六类 Core 权威 JSONL、通过合同的 manifest、不可变请求/标准快照和实际证据。来源绑定必须唯一、指向真实 Core/SourceFact并匹配包和指标。同来源 ID 的原始身份或解释发生漂移会回滚全批。中性事实可供多标准映射复用；范围值只保存上下界，定性事实保存可读 statement，不造主谓宾三元组。

迁移 `0005` 添加 import_batches/import_items、来源身份、关系和审核记录及范围/陈述读取视图。开发测试使用内存 PGlite；可设置 `ESG_DATABASE_DATA_DIR` 指向独立临时目录，不影响现有 `.local/pglite`。

## 迁移规则

- 已提交的迁移不得原地修改；结构变化新增下一个编号迁移。
- 数据库迁移只操作 `esg` 和 `esg_api` schema。
- `examples/` 不属于正式迁移，生产环境不得自动载入。
- 将来接入正式 PostgreSQL 时使用 Alembic 或部署工具按文件顺序执行这些 SQL。
- 向量、全文检索、分区和物化分析视图待真实数据规模确认后再添加。

## 对外视图

| 视图 | 内容 |
|---|---|
| `esg_api.v_quantitative_observations` | 审核通过、可计算的数值披露 |
| `esg_api.v_categorical_observations` | 审核通过的布尔和枚举披露 |
| `esg_api.v_qualitative_disclosures` | 审核通过的定性原子主张 |
| `esg_api.v_standard_disclosures` | 披露事实到标准条款的审核映射 |
| `esg_api.v_quantitative_ranges` | 通过审核的范围/不等式值 |
| `esg_api.v_qualitative_statements` | 通过审核的可读陈述 |

这些视图返回上游 `organization_ref` 和 `report_ref`。PlatESG 使用这些引用连接
自己的企业数据库，我们不在本库复制企业详细信息。

## 规范观测与正式发布（0006）

`scripts/publication.mjs` 在现有候选事实之上提供四个接口：

```javascript
await seedMetricDictionary(db, dictionary);
await saveMeasurementContext(db, reviewedContext);
await processOutbox(db, { limit: 100 });
const snapshot = await readDataset(db, datasetVersion); // null 读取最新版本
```

字典保留 49 个主要条款的原 ESRS 编号，当前 E1–E3 的 16 项标记为 `ready`，其他 33 项保留 `planned`。字典定义、审核后的测量上下文、规范观测、来源快照及已封存数据集不可原地修改。上下文修订使用追加版本；相同上下文请求重试复用当前版本。

正式发布要求事实审核 `approved`、映射独立审核 `approved` 且语义关系 `exact` / `supports`、匹配的标准包版本与摘要、人工确认的主体、期间、报告边界及条款所需维度。单位必须由审核人明确确认。固定范围、能源来源、取水／耗水、污染介质等约束来自字典，但不会自动填入来源事实。方法字段单独保存；范围 2 的地点法与市场法必须满足相应字典合同。

映射审核可在现有审核 JSON 中明确填写：

```json
{
  "mapping_id": "实际映射 UUID",
  "decision": "approved",
  "mapping_relation": "exact",
  "satisfaction_status": "supports",
  "operator_ref": "审核人引用",
  "reason": "核对原文、身份和定义的审核说明"
}
```

审核决定与待发布事件在同一事务保存。发布处理器使用最新审核状态，旧批准事件不会在撤回之后重新发布记录。冲突来源撤出当前数据集，保留历史快照；相同身份与相同值合并来源引用。重述必须明确引用被替代的观测，且主体、定义版本、期间、边界、方法和维度一致。快照保留发布时的原文、原单位、证据及审核引用，后续工作状态不改变历史数据集。

量值使用 PostgreSQL `numeric`，读取接口统一输出十进制字符串，显式零会保留。单位别名、倍率和精度策略统一保存在 `scripts/unit-conversions.v1.json`，当前规则版本为 `esg-units-1.1.0`。明确支持万／百万立方米、吨／万吨、万吨二氧化碳当量、千瓦时／万千瓦时／亿千瓦时及其他所列别名；符号匹配保留大小写，避免把 SI 前缀 `M` 与 `m` 视为同一尺度。有限单位换算使用精确十进制乘法，避免 JavaScript 浮点数及 PostgreSQL 默认除法精度造成大数的小数丢失。GJ→MWh 的因子为 `1/3.6`，非终止十进制保留至少 40 位小数计算精度，原披露值保持不变。

原始单位缺失或未匹配规则时，记录返回 `source_unit_unresolved`，不能通过审核人选择 MWh、m3 等规范单位将未知倍率当作 1。审核单位与原始单位必须使用相同已声明换算因子，否则阻止发布。换算规则版本、摘要及实际使用倍率保存在来源冻结快照中。`0007` 迁移登记不可变规则；规则升级生成唯一 outbox 事件，重新核查当前记录并建立新观测版本，历史数据集保持不变。非有限量值、违反指标定义边界的量值及与原单位矛盾的审核值阻止发布。比例单位本身不强制 `[0,1]`，可再生能源占比的 `[0,1]` 边界由该指标定义规定，其他指标可以有不同范围。范围／不等式保留上下界，不生成点值。

事实审核、映射审核、正式发布与比较资格四个状态分开表达。缺少必要身份维度会阻止发布；缺少仅用于比较的维度允许展示，但返回不可比较的明确原因。`E2-4_09` 作为方法陈述发布，值角色为 `context`，不参与数值排名。

独立数据平台读取 `dataset_versions` / `dataset_membership` 及规范观测，不能把旧 `esg_api` 的“审核通过”视图当作正式发布集合。企业注册表由 PlatESG 的 `plat` schema 管理，公共 HTTP / OpenAPI 和候选审核界面见 [`PlatESG`](../../PlatESG/README.md)。

V1 每批事件按当前映射集合核查冲突、重述和撤回，适用于首批少量企业的验证。大规模生产仍需按受影响测量身份增量处理与真实负载测试；现有身份、版本及事件合同为此保留了边界。
