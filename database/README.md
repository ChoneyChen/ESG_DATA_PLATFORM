# 数据库合同与发布

本目录是**唯一**的存储与发布代码：表结构、候选导入、审核、单位换算、冻结数据版本。
HTTP 由 [PlatESG 后端](../PlatESG/README.md)集成，读取由公开 `/v1` 与受控 `/internal/v1` 提供。

## 1. 三层结构

```text
① 来源事实（原文）          ② 映射与审核                ③ 发布（正式数据）
esg_disclosures            disclosure_standard_mappings   curated_observations
structured_values          review_decisions               observation_sources
disclosure_sources         measurement_contexts           dataset_versions
disclosure_dimensions                                     dataset_membership
```

三层之间只能**向前流动**：发布不会改写来源；审核不会改写原披露；重新提取产生新的来源事实版本。
同一份 PDF 可以交付多次，每次交付是一个 `import_batches` 批次，候选按真实快照去重。

## 2. 表地图

| 层 | 表 | 作用 |
|---|---|---|
| 参考 | `source_report_refs`、`standard_frameworks`、`framework_versions`、`standard_requirements` | 报告来源与标准条目；`unit_definitions` / `canonical_concepts` / `concept_synonyms` 仅保留历史种子，代码不读取 |
| 来源 | `esg_disclosures`、`structured_values`、`qualitative_claims`、`disclosure_dimensions`、`disclosure_sources` | 一条披露的原值/陈述、维度、页码与引文 |
| 来源快照 | `report_source_fact_snapshots`、`import_source_facts` | 中立的整表行快照（含未映射行），按报告与 IR 版本保存 |
| 映射 | `disclosure_standard_mappings`、`disclosure_relations` | 披露到标准条款的映射；关系表已写入，尚未被读取接口使用 |
| 审核 | `review_decisions`、`measurement_contexts` | 事实/映射决定与测量口径（主体、期间、边界、方法、维度） |
| 导入 | `import_batches`、`import_items` | 一次交付的批次、逐项结果与幂等键 |
| 字典 | `dictionary_versions`、`metric_definition_versions`、`dictionary_membership` | 不可变字典快照与指标定义版本 |
| 单位 | `normalization_rule_versions` | 版本化换算规则（当前 `esg-units-1.5.4`） |
| 发布 | `curated_observations`、`observation_sources`、`publication_states` | 规范观测、其来源与发布判定 |
| 口径 | `curated_observations.boundary_code` | 同一指标在集团、基地、子公司、地区等不同范围下是**不同观测**，各自独立发布 |
| 版本 | `dataset_versions`、`dataset_membership`、`outbox_events`、`publication_control` | 冻结数据版本与发布事件 |
| 扩展 | `extension_*`、`derived_observation_lineage`、`derived_publication_states` | 延伸条款定义、计算血缘与派生发布 |
| 平台 | `plat.entities`、`plat.extraction_scopes`、`plat.import_receipts`、`plat.import_receipt_attempts`、`plat.import_stage_events`、`plat.coverage_decisions`、`plat.artifact_lifecycle_*`、`plat.import_contract_releases` | 企业主体、范围快照、交付收据与覆盖决定 |

## 3. 身份与不可变规则

- **观测身份**：主体 + 指标定义版本 + 期间 + 边界 + 值角色 + 方法 + 必要维度；`identity_key` 是它的哈希。
  同值不证明同一事实，不同 Scope 2 方法、集团/子公司、人头/FTE、取水/耗水分别保存。
- **原值优先**：`structured_values` 保存印刷值与单位，不做换算；规范值与换算依据写入 `curated_observations` 与 `observation_sources`，来源层不重复保存。
- **不可变**：字典快照、发布版本、换算规则一经登记不可修改（触发器与校验），内容变化必须新版本。
- **幂等**：交付按批次键与内容摘要去重；同一回执可安全重试。
- **候选状态**：刚交付、尚未运行发布处理器的候选返回 `blocked` + `publication_not_evaluated`；
  不返回空状态，也不把“尚未检查”当成“没有阻塞”。
- **发布门槛**：事实审核、映射审核、测量口径三者齐备且字典就绪，才产生观测；
  比较资格单独计算（缺维度记 `missing_comparison_dimension:*`，不默认相同）。

## 4. 产品化接口

| 接口 | 用途 |
|---|---|
| `POST /internal/v1/deliveries` | 交付一个成果包（幂等，返回持久收据） |
| `POST /internal/v1/reviews` | 事实或映射审核决定（带操作员与依据） |
| `POST /internal/v1/measurement-contexts` | 保存测量口径（主体、期间、边界、维度、原单位） |
| `POST /internal/v1/publication-process` | 兼容入口：处理待发布事件并生成观测；不再作为发布控制 |
| `GET /internal/v1/report-publications` | 按报告列出待发布 / 已发布 / 已删除状态与版本 |
| `POST /internal/v1/report-publications/publish` | 发布一份报告的当前已审核数据（报告级版本 +1） |
| `POST /internal/v1/report-publications/withdraw` | 撤下一份报告（公开站立即隐藏，保留候选与快照） |
| `POST /internal/v1/report-publications/delete` | 删除一份报告（隐藏报告与产物，保留审计快照） |
| `POST /internal/v1/report-artifacts/delete-report` | 删除整份报告产物并撤下公开数据 |
| `GET /internal/v1/candidates` | 候选与发布阻塞原因 |
| `POST /internal/v1/delivery-status/query` | 只读核对交付与发布状态（固定描述符） |
| `POST /internal/v1/entities` | 登记企业主体（发布前置） |
| `GET /v1/observations`、`POST /v1/comparisons`、`/v1/exports` | 公开读取与固定版本导出 |

命令行：

```bash
./ESG_DATA_PLATFORM/scripts/publish_session.py --session plan-... \
    --publisher "宁德时代新能源科技股份有限公司" --entity org:catl --industry C3841
```

它确认报告身份、登记主体、交付成果包，并列出每条候选“能否审核 / 是否缺口径 / 被什么阻塞”；
审核与发布仍在操作台完成，命令不替人做决定。

## 5. 运行与测试

本目录使用 Node.js 20+：

```bash
npm install
npm test          # 55 项：事务、幂等、单位、发布门槛、来源快照
npm run db:init   # 初始化所选库
npm run db:status
```

`ESG_DATABASE_DATA_DIR` 覆盖运行库；`PLATESG_DATABASE_URL` 指向受控 PostgreSQL。
测试使用隔离或内存库，不会打开活动库。科学存储合同（原值、口径、版本、公开白名单）见
[科学数据存储链路](../docs/SCIENTIFIC_DATA_STORAGE.md)。
