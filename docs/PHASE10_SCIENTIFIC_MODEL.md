# 阶段 10：数据库科学模型与双写对账

日期：2026-10-28  
范围：`database/migrations/0016_scientific_model.sql`、`database/scripts/scientific-model.mjs`、Plat 后端与科学产品接口。

## 1. 目标

候选层的结构是**提取过程的镜像**（source_fact → disclosure → mapping → context）。
阶段 10 建立与提取解耦的科学模型：**条款版本**与**观测**是一等行，维度、字段证据、来源、覆盖
各自独立存放；旧候选表在对账通过前仍是权威。

## 2. 新表（migration 0016）

```text
esg.clause_versions            clause_version_id, package, source_datapoint, metric, contract_digest,
                               value_type, canonical_unit, scopes, evidence, status, acceptance_state
esg.scientific_observation_model  observation_id, entity_ref, clause_version_id, report_ref,
                               period_start/end/kind, value_numeric/lower/upper/text, canonical_unit,
                               value_role, measurement_method, aggregation_role,
                               comparison_eligibility, publication_state, publication_version,
                               source_observation_id（指回旧观测）
esg.observation_dimensions     (observation_id, axis) → code, printed_label
esg.observation_evidence       field, ir_object_id, page_index, bbox, quote, source_fact_ref
esg.observation_model_sources  raw_value/raw_unit/raw_label, source_fact_ref, disclosure/mapping/context
esg.coverage_decisions         覆盖状态（覆盖/未确认/其他口径/缺失…），按条款版本与报告
```

科学产品接口新增六个只读视图：`clause_versions`、`observations_v2`、`observation_dimensions_v2`、
`observation_evidence_v2`、`observation_sources_v2`、`coverage_decisions_v2`。
旧视图保留，读取方可以逐表切换。

## 3. 投影与对账

```text
projectClauseVersions(tx, clauses)   条款目录 → clause_versions；同一 id 不同摘要直接拒绝
projectObservations(tx)              旧观测 + 来源快照 + 发布成员 → 观测/维度/证据/来源
reconcileScientificModel(tx)         逐项比较：数量、值/主体/期间、发布状态与版本
```

关键问题与解决：平台字典的 `metric_id`（如 `energy.total_consumption`）与标准包的
`metric_id`（`esrs.2023-set1.e1-5.dp01`）**不是同一套标识**。投影通过共同的
`source_datapoint_id` 解析条款版本，找不到就记为 `unmapped_metrics`，绝不臆造归属。

## 4. 真实对账（demo-data 副本，未改动原库）

| 项 | 结果 |
|---|---|
| 旧观测 | 64 |
| 已发布报告 | 4 |
| 条款版本 | 1,125 |
| 投影观测 | **64（0 未映射、0 缺来源）** |
| 观测维度 | 316 |
| 观测来源 | 64 |
| 字段证据 | 0（该批快照没有 `identity_evidence`，见边界） |
| 对账 | **ok = true**（数量/值/主体/期间/发布状态与版本一致） |

正式库 `PlatESG/.local/data` 也已应用 0016 并投影 1,125 条条款版本，对账 ok（该库当前 0 条观测）。

## 5. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/internal/v1/scientific-model/project` | 应用条款目录并投影观测，返回对账结果（admin） |
| GET | `/internal/v1/scientific-model/reconciliation` | 只读对账 |
| GET | `/v1/scientific/tables` | 表清单（改为由数据库 worker 提供；此前引用了不存在的 Python 模块，实际返回 500，本轮一并修复） |
| GET | `/v1/scientific/{table}` | JSON/CSV/Parquet，含新增六表 |

## 6. 测试

数据库 61 通过、2 跳过（新增 5 条：投影与对账、值漂移被发现、条款同 id 改内容被拒、
无条款版本的指标被报告而不是编造、条款投影的必填校验）。
科学产品、发布、导入等既有用例全部通过。

## 7. 边界

- **字段证据为空**是真实缺口：当前把 `source_snapshot.identity_evidence` 投影为证据行。
  demo 数据来自阶段 1 之前，快照没有该字段；阶段 2 之后导入的报告会有证据。
  这一点必须继续用新报告验证，不能把“空”当成“没有证据要求”。
- 覆盖决策表已建好，但尚未从成果包的 `scope_coverage/scope_gaps` 自动写入；阶段 11/12 接入。
- 读取仍以旧视图为主；新视图先并行发布，待真实报告全部对账通过后再切换默认读取。
