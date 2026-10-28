# 阶段 4：科学数据产品

日期：2026-10-28

## 已交付

- 新增只读科学视图：`observations`、`observation_dimensions`、`observation_evidence`、
  `observation_sources`、`metric_definitions`、`report_publication_members`、`coverage_decisions`。
- 维度/字段证据/来源不再只藏在 JSON 单元格；保留原值、原单位、主体/期间/边界、IR 身份和物理证据。
- `neutral` 来源没有 Core 绑定时明确 `unmapped`，不把“已归档”当“已映射”。
- Plat 后端提供只读 `/v1/scientific/tables` 与 `/v1/scientific/{table}?format=json|csv|parquet`，
  通过既有数据库 worker，不新增写接口、第二数据库连接或自动发布。
- Parquet 使用字符串保存精确数值；CSV 保留精度并防止表格公式注入。

## 真实验收

- 真实中国石油中立事实：180 条科学覆盖记录全部 `unmapped`，历史期来源不自动成为条款观测。
- 隔离批准/发布：1 条正式观测、2 条维度、1 条期间字段证据、1 条来源与 1 条发布成员；
  字段证据指向真实单元格与报告期。
- 实际生成并读取 Parquet 文件；生产依赖未改变，验证使用独立 `.local` 环境。
- 平台与数据库 118 通过、12 跳过、0 失败；历史发布和现有 API 未迁移。

## 边界

- 科学视图/导出只读；`0015_scientific_projections.sql` 仅在隔离库应用，正式库未迁移。
- 科学覆盖表表达当前来源绑定状态，不把证据不足的缺失升级为“确认未披露”。
- 阶段 5 条款中心将沿用同一科学合同，支持草稿、版本差异、依赖、真实验收、发布与退役。
