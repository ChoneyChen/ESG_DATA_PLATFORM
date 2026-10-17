# P4–P7独立连接审计：当前合同、历史入口与业务边界

日期：2026-10-05。本记录由独立迁移/金标准审计者完成，读取冻结源码、已登记版本、原PDF、历史输入快照及实际本机GET接口。没有提交OCR、模型、提取、审核、发布、取消或清理命令，也没有编辑生产源码或重启服务。常规资产GET会刷新其索引，这是现有接口行为；没有重写历史PDF/IR/Bundle。

机器证据见 [只读连接快照](migrations/2026-10-05-console-readonly-link-audit.json)。活动任务状态仅代表该快照时间，不应当作后续完成状态。原要求仍为 [更新要求](../更新要求.md)，本页不重写用户规划。

## 已连接的业务合同

| 要求/边界 | 对应实现 | 独立核对 |
|---|---|---|
| P0：业务就绪及版本 | `ExtractESG/scripts/workbench_release.py`；两API兼容合同；标准registry与平台profile | 实际目录41包；整体49原编号；显式E1–E3选择16，后续33；目录、版本、主要/核心字段未在新Console缩减 |
| P1：报告对象及原PDF身份 | `document_processing/.../assets/report_catalog.py`；`control_plane/.../report_processing.py`；`api/console.py`报告投影 | 当前中国石油登记主体CNPC集团、报告期2025、类型企业社会责任；资产SHA独立于业务报告ID和测量主体 |
| P1：连续OCR→IR | ProcessingRequest、ProcessingConfig、协调器；Console `Processing.tsx` | 前端auto/rebuild_ir/reprocess与服务端枚举和字段一致；独立处理不要求标准；真实96页流程在本次观察时排队，尚未完成验收 |
| P1：固定IR、复用及恢复 | `_report_document`调用处理协调器的严格来源/config resolver；输入manifest、原生reservation及queue pins | 新16项Session固定历史IR `ir-20260918T181255Z-005c90d28dab`；来源SHA与原PDF一致。新96页处理计划另有身份，不自动替换抽取所用IR |
| P2：父归属、归档及引用 | `plan_lifecycle`、`processing_lifecycle`、native `storage/lifecycle`及共享guard | 中国石油旧job的request有父Session，lifecycle返回open_parent；不能按顶层无parent字段错误认定独立任务 |
| P3：真实进度/错误/事件 | queue progress/timing、SSE；Console `state.tsx`、`ui.tsx` | 使用真实current/total或未知量，技术失败保留来源；UI时间/当前调用细节仍有下述明确缺口 |
| P4：范围选择与预览 | Console `ScopeSelector.tsx`、`Extractions.tsx`；`/api/report-sessions/preview` | 当前实际Session的selection_snapshot完整含16原编号和固定profile/dictionary/package信息，5包输出分别3/4/3/1/5条；改变来源/范围/角色/预算后旧预览失效 |
| P4：结果与候选交付 | Console交付表单；Document `api/console.py`；Plat `/internal/v1/deliveries` | 请求report_id/expected_revision/organization_ref/reason与严格DTO一致；后端固定成果路径、报告资产版本、IR和Bundle快照，拒绝任意bundle_path及错来源。交付测试2通过 |
| P5：公开与内部边界 | Plat公开`/v1`；内部`/internal/v1`；服务端角色映射与来源PDF合同 | 公共读不携带Console token；内部读/审/发/admin分别验证。多token配置存在，生产SSO未建设 |
| P5：数据库与发布 | canonical `database`、Plat适配器、outbox、sealed dataset | PostgreSQL SQL及pg依赖可解析；本地AUTO_PUBLISH0重启暂停由独立持久adapter测试验证。尚未用真实业务本次结果完成审核发布 |
| P6：唯一源与固定历史版本 | canonical领域目录、旧符号链接、标准显式release index | 95个登记版本逐个读取/源摘要/文件SHA通过，41活动/54历史；未登记目录不激活；历史固定E1-6 2.0.0仍解析 |
| P7：独立答案来源 | `cnpc-2025-esrs-major-gold.v1.json`及原PDF直接读取 | 原PDF2/90/91页检查通过，封面1页直接核对标题；30个年度单元格，15个污染物单元格允许候选映射；未评价新模型准确率或正式发布 |

共享事实响应使用 `facts`、`physical_sources`、`identity_raw`，Console消费相同字段；物理页使用真实pdf_page_index，原编号来自固定标准版本。审核表单按平台指标合同构造必要主体、期间、边界、方法及维度，测量主体不默认为报告发布者。context保存、事实批准和映射批准也不代替published状态。

## 历史归属的实际证据与初次判断更正

实际GET `/api/jobs?limit=500&include_archived=true` 在记录时返回15条：5个新Session投影、1个中国石油历史Session投影、9个历史独立job。

中国石油旧job `tx-20261002T182823Z-f5b3169b0c` **已经可经父Session访问**：

- `request.report_session_id = report-b7373a91fe35ef85003e5566d1e7b2c6`。
- 该native Session的 `job_ids` 和 `jobs` 都包含该job。
- 平台 `plan-6c52de913dcd44058b61bef4573dd285` 聚合该Session；Console报告提取页已经列出这个历史入口。
- 旧job inspection返回35个原条款，状态interrupted，固定E1-6 `2.0.0` 和原IR仍可解析；它不是新增standalone列表的候选。

初次审计只检查了job顶层parent字段，误判此job没有父归属；补查request和Session实际成员后已纠正。不能在工程修补中为它重新创建Session或重复展示。

真正的历史独立job共9个，均无 `request.report_session_id` 且不在任何已知Session的job_ids/jobs中：

| 固定IR | 原PDF资产身份 | 真实独立job数 |
|---|---|---|
| `ir-20260830T125314Z-ba0770424c80` | `pdf-33bdb1b34f5ac2d9d091c047` | 7 |
| `ir-20260831T153625Z-cf25e52ace9b` | `pdf-374b145003e7ec82e7048342` | 2 |

这9个任务有固定输入IR manifest，来源SHA均匹配已登记资产，不需要文件名或企业简称猜归属。它们目前仍可从Targeted API和旧技术工作台查，但新Console报告提取页/提取列表没有独立job入口。

## 待统一解冻的明确更新安排

当前真实模型正在运行，本审计只提出合同和验收安排，不应用生产patch。

### A. 报告聚合登记历史独立成果，保持父任务去重

根因位于 Document `api/console.py` 的 `sessions_for`：仅遍历 `plans.list()` 中的 `report_request`。报告聚合的 `extraction_sessions` 和所选原编号因此只反映平台Session，不包含真实独立native job。前端不是缺少字段fallback，而是后端缺少该资源类型。

建议保留Session DTO，并新增明确的历史资源：

```json
{
  "reference_kind": "standalone_job",
  "job_id": "固定历史job ID",
  "source_identity": {
    "ir_run_id": "固定IR ID",
    "asset_id": "经SHA匹配的资产ID",
    "source_reference_status": "frozen_input_verified"
  },
  "package_id": "固定包ID",
  "package_version": "固定内容版本",
  "selected_metric_count": 4,
  "execution_status": "completed",
  "result_status": "来自真实成果或not_evaluated",
  "lifecycle": {"state": "active", "revision": 0},
  "inspection_endpoint": "/api/jobs/固定job ID/inspection"
}
```

该JSON是拟议合同示例，尚未作为生产DTO上线。execution completed不自动产生result full，历史条款/成果详细状态由inspection返回。

具体服务实现顺序：

1. 对单次报告列表请求批量读取Targeted job目录和native Session成员，不能每个PDF再做一次全目录请求。
2. 用request.report_session_id与实际Session.job_ids/jobs双重判定归属。任一权威父引用存在，则作为父投影；父暂不可读仍保留父引用/依赖问题，不能当standalone重复。
3. 对真正独立任务读取固定输入 `input/ir-manifest.json` 并检查其run/source身份；需要当前IR检查时保持旧固定run，不使用“最新IR”。完整性问题形成该历史引用的可读阻塞，不自动重写源。
4. 以PDF完整SHA关联报告资产；无匹配/固定来源不可确认者保留unresolved reference，不按报告名强配，也不从列表消失。
5. 从固定request保留包ID/版本/metric IDs。原编号使用该job冻结标准快照或该明确历史发布身份，不能拿活动2.1.1替代1.0.0/1.4.0/2.0.0。
6. 报告DTO增加 `standalone_jobs`、独立计数和目录读取依赖。新列表如统一Session/job，采用有reference_kind的联合DTO，不把job伪装成Session。
7. 不迁移旧任务归属，不创建新Queue/native ID，不自动resume或交付；历史候选也不因此变成已审核成果。

Console `Reports.tsx` 提取页分别列出“报告会话”和“历史独立提取”；`Extractions.tsx` 列表增加同样资源类型和筛选。建议明确 `/#/extractions/jobs/{job_id}` 只读历史详情，复用真实ProjectionInspection和固定范围说明；在 `App.tsx` 解析为job资源，避免误送 `/api/report-sessions/{job_id}`。父投影继续只在原Session内呈现，历史动作严格消费后端lifecycle归属。

必要回归：父request存在但父暂不可读仍不重复；Session成员存在而旧request未写parent时去重；两报告同名/SHA不同不混配；9独立任务正确映射；其中一个旧manifest坏不隐藏其他报告；原包退役仍按固定版本显示；unknown依赖不显示0任务；全流程GET不创建queue或新Session。

### B. 补齐时间与当前模型动作的真实表达

Console `components/ui.tsx` 的Progress当前展示queue、active、unobserved、wall，遗漏实际DTO的 `sleep_or_clock_adjustment_seconds`。应单独展示睡眠/时钟调整及观测完整性，不把它加到模型工作时间。

真实current_call只有tool/route、没有label/action时，当前UI退回“理解证据”，会把 `repair_format` 的协议修复误写为来源理解。应按已知tool/route合同映射中文阶段，未知字段诚实显示待识别动作，并将真实machine字段放在展开诊断；不能根据调用次数伪造完成百分比。

必要回归：repair_format为协议修复、map为映射、未知tool不冒充理解、睡眠非零独立显示、unobserved不推断计算、无total不绘百分比。

### C. 预检后让按钮与最新允许动作一致

Console `Actions` 目前读取preflight并展示blockers，但确认按钮不检查最新 `available_actions` 是否仍含当前action。后端引用guard仍会拒绝不允许清理，数据保护有效；操作台应在预检已明确拒绝时停止该动作并显示阻塞/父入口，避免形式化确认。

应按返回的真实DTO能力判定：明确返回available_actions时要求该action存在且enabled不为false；只提供blockers的清理计划按明确server eligibility字段判定，不能把任意未知preflight当作允许或禁止。最终执行仍必须服务端重新核查，避免检查与执行之间的新引用。

必要回归：预检返回动作消失/被禁用不发POST；allowed时携带真实revision/reason；open_parent转父入口；预检后新依赖仍由server阻塞。

## 本次独立验证与下一步

本审计实际运行：结构测试7通过；95个发布身份逐个加载/摘要核对通过（41活动/54历史）；gold原PDF2/90/91页通过；隔离候选交付测试2通过。迁移清单核对1,400源文件、144兼容入口、21份精确原件和407历史实物通过；唯一曾发现的publication.test摘要漂移为协调者新增人工暂停回归，审阅后只更新清单after摘要，原before字节保留。

统一协调者另完成canonical后Document324、Targeted357、数据库23通过/1既有fixture跳过（发布13为子集）、service13、Plat API32/PDF4/持久adapter重启暂停1；本页不重复运行并累加这些测试。

P7尚未完成。当前主要16项任务与独立96页处理的实际结果，仍需完成原文召回/映射/身份/成本/恢复检查，再用真实合格候选走独立审核、manual hold/显式发布、产品固定版本与来源回查。金标准确认15个污染物来源单元格不等于当前模型已经提取或发布这些事实；其他条款不能仅凭两页未发现就宣告全报告未披露。
