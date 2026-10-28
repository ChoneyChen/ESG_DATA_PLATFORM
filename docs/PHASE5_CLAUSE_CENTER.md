# 阶段 5：条款中心（统一生命周期与真实验收）

日期：2026-10-28  
范围：`ExtractESG/standards`、定向提取后端、Document API、`PlatESG` 后端与 Console 标准中心。  
原则：条款定义只有一份；验收由真实运行计算，不由操作人声明；未通过真实验收的条款不标为可发布。

## 1. 状态模型

原标准条款与项目延伸共用同一套生命周期，状态由账本推导，不单独存储：

```text
draft → compiled → trialed → accepted → publishable
                                      ↘ retired（任意时点，按版本终止）
```

| 状态 | 含义 | 进入条件 |
|---|---|---|
| `draft` | 作者草稿，未编译 | 无 |
| `compiled` | 已编译登记，没有真实验收 | 存在发布版本 |
| `trialed` | 试提取未通过 | 最近一次验收为 `rejected`，且合同未变 |
| `accepted` | 通过真实验收，缺业务批准 | 验收通过且绑定当前合同摘要 |
| `publishable` | 可发布 | 验收通过 + 业务批准 + 未退役 |
| `retired` | 退役 | 有退役记录；冻结任务与已发布含义不变 |

关键约束：

- 验收绑定条款自身的**科学合同摘要**（标准包 `measurement_contract` 或延伸 `definition`）。
  修改条款后旧验收自动失效，状态回落到 `compiled` 并给出 `contract_changed_since_acceptance`；
  不会静默继承一个已不再描述该条款的结论。
- 平台显示策略（单位展示、比较配置）不进入摘要：改变展示方式不推翻已完成的提取验收。
- 同一合同、同一次运行的验收不允许被不同结论覆盖；要推翻只能重新试提取（`record_acceptance` 拒绝覆盖）。

## 2. 真实验收的计算

`evaluate_run` 从冻结成果包 `exports/extraction-result.json` 计算五项检查：

| 检查 | 通过条件 |
|---|---|
| `task_executed` | 存在该条款的报告任务且全部 `completed` |
| `facts_bound` | 判定已披露时绑定了印刷原值；判定未披露时必须由**已覆盖**来源对象确认未披露 |
| `evidence_anchored` | 每条事实都有 IR 对象与页码锚点 |
| `unit_and_period` | 每条事实保留印刷单位与报告期间 |
| `gold_match` | 提供独立对照期望时，原值或未披露结论一致（未提供为 `not_applicable`） |
| `single_interpretation` | 同一印刷原值没有被映射到多个未区分边界 |

`facts_bound` 的判定是这一阶段最重要的取舍：

- `confirmed_not_disclosed`（覆盖充分且确认未披露）→ 通过；
- `not_covered`、`unmapped`、`insufficient_evidence` → 不通过。
  **没有读过的页面不能作为“未披露”的证据**，这与阶段 3 的覆盖决策一致。

## 3. 真实基线（2026-10-28）

`scripts/phase5_clause_acceptance.py` 扫描生产成果目录，按“报告 × 包版本”取最新一次真实运行，
对每个实际执行的条款计算验收并写入账本（`ExtractESG/standards/lifecycle/ledger.json`，只追加）：

| 项目 | 结果 |
|---|---|
| 参与运行 | 117 个真实成果包（同一报告同一包只取最新一次） |
| 验收记录 | 252 条 |
| 覆盖条款 | 38 条（真实运行实际执行过的条款） |
| 通过 | 12 条 |
| 未通过 | 26 条（全部卡在 `facts_bound`：任务判定不确定，或缺覆盖决策） |
| 全目录状态 | 1,126 条条款：`compiled` 1,088、`trialed` 26、`accepted` 12 |
| 可发布 | **0 条** |

可发布为 0 是当前事实，不是缺陷：`registry/releases.json` 中所有包的 `business_approval`
仍为 `not_assessed`，因此即使条款通过真实验收也只能停在 `accepted`。这正是“未通过真实验收不得标为可发布”的另一半：
**没有业务批准也不标为可发布**。

未通过的 26 条集中在阿里巴巴与法拉电子的单条款运行（`found_status=uncertain`、无覆盖决策），
说明“试提取没有形成可判定的结论”本身就会被记为未通过，而不是被跳过。

## 4. 门禁

| 位置 | 行为 |
|---|---|
| `POST /internal/v1/extensions/platform-selection` | 选中的延伸条款必须 `publishable`，否则 409 并列出状态与原因 |
| 延伸成果包交付入库（`ExtensionControl.validate_bundle`） | 快照中的每个延伸版本必须有通过验收，否则拒绝导入 |
| Console 平台主要配置 | 未通过验收的延伸复选框禁用并标注“未通过真实验收” |

原条款的既有发布不受影响（历史发布含义不变）；原条款的可发布标记只用于声明其自身状态。

## 5. 编辑、版本差异、依赖与退役

- **版本差异**：`clause_version_diff` 以原编号为单位给出新增/删除/变更，变更字段包含单位、值角色、聚合、
  身份轴、比较轴、固定维度、排除条件、正向定义、阅读说明与易混概念。
  接口 `GET /api/pipeline/standards/{pkg}/{old}/diff/{new}`。
- **依赖**：`dependent_clauses` 合并标准包关系（`decomposes`、`calculation_dependency` 等）与延伸条款的
  原点链接/计算操作数，区分“引用本条款”和“本条款依赖”。
  接口 `GET /api/pipeline/standards/{pkg}/{ver}/dependents/{source_id}`。
- **退役**：`retire_clause` 要求操作人与原因；记录影响（依赖延伸、已发布观测数、运行中任务）。
  有运行中任务时默认拒绝，需要显式 `force`；退役不删除历史、不改写已发布观测。
  接口 `POST /api/pipeline/clause-lifecycle/retirements`。

## 6. Console

标准中心新增“条款生命周期与真实验收”：

- 头部显示全平台“已验收 / 可发布”条数；
- 条款表新增生命周期列；
- 面板按状态汇总，逐条显示原因、验收报告与每项检查的结论和说明；
- “依赖影响”就地展开；“退役”要求填写原因；
- “版本差异”选择另一个版本后列出新增/删除/变更条款与前后对照。

以上均由真实接口驱动，页面不自行推断状态。

## 7. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/pipeline/clause-lifecycle` | 全目录条款状态、验收账本、退役记录、包级汇总 |
| POST | `/api/pipeline/clause-lifecycle/acceptances` | 从冻结成果包计算并登记验收 |
| POST | `/api/pipeline/clause-lifecycle/retirements` | 退役一个条款版本 |
| GET | `/api/pipeline/standards/{pkg}/{ver}/diff/{other}` | 版本差异 |
| GET | `/api/pipeline/standards/{pkg}/{ver}/dependents/{source_id}` | 依赖影响 |
| GET | `/api/standards/{pkg}/{ver}` | 详情中带 `lifecycle` |
| GET | `/api/extensions` | 每个延伸版本带 `lifecycle` |

验收登记只接受配置结果根目录下的成果包；路径越界、缺少导出结果、条款不在该包内都会被拒绝。

## 8. 测试与真实核验

| 套件 | 结果 |
|---|---|
| 标准包（含 16 条新生命周期测试） | 134 通过 |
| 定向提取后端（含 7 条服务测试） | 775 通过、6 跳过 |
| Document 后端 | 677 通过 |
| ConsoleESG | 228 通过 |
| PlatESG Python（含延伸验收门禁） | 79 通过 |
| PlatESG Node | 62 通过、10 跳过 |
| 数据库 | 56 通过、2 跳过 |

真实界面核验（Console 18081 / 开发 5180 对接真实后端）：

- 头部显示“已验收 12 条 · 可发布 0 条”；
- E1-5 面板显示 `E1-5_01` 已验收（阿里巴巴报告，任务/绑定/锚点/单位期间全部通过）、
  `E1-5_02` 试提取未通过（法拉电子，未绑定原值）；
- `E1-5_03` 依赖影响返回 `本条款依赖 · decomposes · E1-5_01` 与 `引用本条款 · calculation_dependency · E1-5_04`；
- 版本差异 2.1.1 → 2.1.2 显示 23 条变更及逐字段前后对照。

## 9. 已知边界

- 没有任何标准包记录业务批准，因此当前可发布为 0；业务批准仍是独立的人工决定，不由验收替代。
- 生产发布库中延伸版本为 0，延伸门禁由隔离测试与真实接口行为验证，未在正式库登记新延伸。
- 验收取“当前合同下最近一次”记录：新报告未通过会覆盖旧的通过结论并暴露回归，这是有意的严格口径。
- 对照期望（`gold_match`）只在调用方提供时参与判定；当前真实批量验收未提供，记为 `not_applicable`。
- 退役按版本终止，不提供撤销；需要用新版本重新走生命周期。
- 发布服务本次以 `PLATESG_AUTO_PUBLISH=0` 启动时应用了 `0015_scientific_projections.sql`，
  正式库 `PlatESG/.local/data` 现已具备阶段 4 的七类科学视图（只新增视图，无数据改写）。
