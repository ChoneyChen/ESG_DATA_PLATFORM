# IR 复核引用与执行状态合同

本轮变更根据 `ir-20261006T195007Z-2a5862660c87` 的八次真实响应离线回放建设。回放使用冻结响应、匿名候选和固定适配器；不调用模型，不修改原 IR，也不把回放接受作为真实报告质量验收。

## 引用职责

- `AtomicPatch.evidence_refs` 保留模型声明的对象、文字和来源引用。接收器可追加已登记任务来源，但不会删掉无法解析的显式声明来使校验通过。
- `chart_spec.visual_evidence_refs` 的自动补充仅来自 IR 已登记的图像 `ArtifactRef`。Markdown、未登记图片字符串及未知对象不会被自动转换为图像证据。显式无效声明继续交给严格门禁拒绝。
- `point.evidence_refs` 是单点来源声明，不能由整页图像或任务上下文补造。空引用或仅图像引用保持 `source_context_only`，像素值未经校验；精确文字支持、无效引用与范围冲突沿用既有独立账目。
- OCR 包里的真实图像由转换器核对文件位于包内、文件格式和尺寸后登记 `ocr_image`。登记来源与 SHA 不提供虚构坐标或图表单点证明。
- 包相对别名仅对当前 IR 的已登记文件成立；别名冲突关闭解析权限。原始成果不会因为新解析规则被原地改写。

## 操作与事务

23 种操作继续通过同源 `OperationRegistry` 和 typed payload 合同。接收端先校验载荷形状，非法载荷不得进入依赖该形状的业务检查；图表来源检查保留独立、安全的诊断。合法载荷仍需通过几何、冻结范围和事务门禁。

`confirm_spread` 是明确的分类操作，`link_horizontal_continuation` 是关系写入；关系存在不替代明确确认。模型明确确认时，记录器仍物化分类操作，并保留两张表的冻结范围、共同事务和候选快照。非法或空表格载荷不会因此获准。

## 状态与审计

`AgentModelCall.status` 仍表达原有模型调用与响应包络结果。新增字段分开记录：

| 字段 | 含义 |
| --- | --- |
| `envelope_validation` | `not_evaluated` / `passed` / `failed`；响应协议校验 |
| `operation_payload_validation` | 同一枚举；Reviewer 操作载荷校验 |
| `operation_payload_issues` | 操作、对象和实际载荷错误，不代表图像像素校验 |
| `capability_receipt` | 实际 Worker 返回的 schema 通道及 token 级约束能力 |

包络成功不代表操作合法、事务接受或单点支持。能力信息缺失保持 `unknown` / `null`；当前实际提示 schema 通道明确记录 `token_level_schema_enforcement=false`。本轮不宣称新增 token 级约束解码。

一次尝试失败不会被重命名为 `repeated_failure`，也不会写成 `after_repair`。任务结果保存实际次数、配置预算和是否预算耗尽；重复失败仍由既有收敛指纹判定。`deferred` 决策引用已经存在的 Reviewer、Guard 和 Verifier 记录，没有 Verifier 就保留空列表。

`AtomicPatch.effect` 区分 `not_evaluated`、`no_change`、`proposed_change`。与现有 OCR 文字完全相同的替换不算修复，保留 `no_material_change_requires_review`；不自动确认原风险，也不编造改动。

## 有界上下文

可写文字对象读取冻结计划内对象，正文上限 3000 字；页文字和其他预览沿用既定预算。候选明确附带原始长度与完整性字段，例如 `text_length` / `text_complete`、`text_preview_complete`、`cell_count` / `cells_complete`。被截断的预览不等于原来源缺失。扩大模型读取或修改冻结范围仍需合法的规划合同，不能通过 preview 标记绕过门禁。

## 当前版本边界

只存储当前 IR、`review-plan-v3`、`review-operation-payload-v2` 及其当前枚举。旧 Ledger、旧计划、扁平目录及旧整页替换操作没有读取适配；新的提供者响应先规范化，再进入当前合同。可缺省审计能力保持 unknown/null，不能补造验证已经完成。修复生成新修订，真实验收重新检查当前产物，离线回放不能替代业务结果。
