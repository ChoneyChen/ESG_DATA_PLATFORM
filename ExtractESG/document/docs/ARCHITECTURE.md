# 文档证据模块

本文件描述当前 `ExtractESG/document`，全项目边界见[模块规范](../../../docs/architecture/README.md)。运行位置见[本地运行](../../../docs/guides/local-operation.md)。不再提供旧 IR/OCR 格式或旧标准体系的读取方式。

## 职责与依赖

文档模块从完整 PDF 生成可回读的物理证据，不拥有条款词典、目标匹配、测量事实、数据库发布或报告主数据。报告资源的散列和元数据通过资源端口传入；Console 后端是应用组合根，队列与会话归 `pipeline`。不从其他模块的产物目录猜输入。

| 组件 | 内聚职责 |
|---|---|
| `ocr/` | 本地布局/识别、受控提供者适配、OCR 包写入和资源解析 |
| `local_forensics.py`、`page_words.py` | 可见页面文字、坐标、原生表格候选；裁切框外文字不参与当前页解释 |
| `paddleocr_converter.py`、`parser_fusion.py` | 提供者观察转 IR，保留原观察、冲突与来源 |
| `coordinate_mapper.py`、`table_geometry_resolver.py` | 坐标系统与有证据的几何恢复 |
| `structure_reconstruction.py`、`table_graph_builder.py` | 中立章节、表头、单位和续表关系 |
| `spread_builder.py`、`logical_table_builder.py` | 邻页关系候选、物理表到逻辑表的明确映射 |
| `quality_router.py`、`evidence_capabilities.py` | 具体风险、阻塞/可选复核及对象能力 |
| `review_orchestrator.py`、`operation_registry.py`、`patch_guard.py` | 有界模型提案、唯一操作合同、局部校验与事务应用 |
| `reader.py`、`writer.py`、`revision_service.py` | 当前包格式、分片、完整性、修订及来源回读 |
| `storage/` | 本模块产物目录、引用保护、清理计划和保留策略 |

## 从 PDF 到证据

先检查 PDF 画布、旋转、页序与可渲染性。必要的提供者输入变换单独记录，不改原 PDF 身份。原生文字可用时读取文字与布局；不可读页面/区域使用本地 OCR，搜索范围及实际读取方式写入覆盖记录。稀疏处理保留原 PDF 的页号，不把第 73 页改称第 1 页。

物理解析经融合、去重、标识规范、几何恢复、结构与表格关系构建后进入质量检查。它不保证任何条款被披露，也不把报告标题年分配给所有单元格。跨页、脚注、重复页头和图像属于物理关系；ESG 含义留给消费条款的提取模块。

`can_build_evidence` 与对象级能力共同决定可读范围。某个图表或可选复核未完成，不等于其他已验证文字/表格不能读取；缺坐标也不能伪造坐标。失败与不确定性以真实原因保留。

## 宽表与来源映射

`TableIR` 是物理网格。`LogicalTableIR` 只组织物理单元格，支持纵向续表、横向加列、末列跨页延续。每个逻辑单元格保留 `source_cell_ids`，物理坐标始终属于原页面。

横向表允许左右存在不同数量的空白分节行，但必须能确定性匹配实际值行。不能解析的行映射标为 `review_required`，不回退为“行偏移零”。提取证据服务只消费这份已确认映射，不再另外实现第二套横向拼接。

多年重复列组保留完整父表头片段、年份和各片段的原单元格引用。列名是组合语义，证据是实际片段；不能将“Ports and Related Services”退化为某一截“Services”。零是数值，空白或“/”不是零。

## 当前产物合同

仅接受 `ocr-package-v1` 与 `document-ir-package-v1`；IR 语义版本仅为 `document-ir-v0.12`。旧扁平目录、旧 schema、外部绝对 raw JSONL 路径和旧修订迁移复制均被拒绝。OCR 提供者当前响应的结构规范化仍属于正常输入适配。

包唯一入口为 `manifest.json`，成员通过相对 `entrypoints` 解析。目录不能逃逸包根。`integrity/files.json` 记录文件、大小、SHA256；写入完成后验证，不能覆盖已存在运行。

```text
OCR 包
  manifest.json
  source/                         请求、选页与变换
  provider/                       已去除凭据的响应及资源
  observations/pages/             每页观察与索引
  content/pages/                  派生 Markdown
  artifacts/                      图像与资源索引
  integrity/files.json

IR 包
  manifest.json
  canonical/                      document、pages、tables、logical-tables、figures、spreads
  observations/                   解析观察与本地核验
  artifacts/                      页面、裁图、合成图及索引
  quality/                        能力、质量与验证
  review/                         任务、调用、候选、补丁、事务及决定
  exports/document-ir.snapshot.json  当前完整模型导出，不是旧格式入口
  integrity/files.json
```

业务成员与完整模型导出由同一写入器产生。提取服务通过 `DocumentPort`、搜索和证据帧取得数据，不读取此内部目录。运行状态和日志位于独立 `control`/`logs`，不是不可变证据成员。

PDF 身份为 `doc-sha256-<完整散列>`；确无 PDF 散列的物理识别输入使用 `doc-artifact-<散列>`，不冒称已验证 PDF。`ocr_run_id`、`ir_run_id`、`lineage_id` 分别表示处理尝试、证据修订和修订链，不是事实的测量期间。

## 复核与模型

默认使用本地模型；`allow_cloud_ocr=false`。保留显式远程适配能力不等于允许静默切换。普通提取准备不自动执行全报告的可选 VLM 精修。统一模型调用信封记录实际预算、模态、能力、耗时、校验和失败；完整内容不进入公共调用摘要。

当前复核计划为 `review-plan-v3`，操作合同为 `review-operation-payload-v2`。`OperationRegistry` 同源生成操作 Schema、提示说明和接收校验。旧 `replace_text`/`replace_table` 操作已移除；当前分别使用对象级文字操作或 `set_table_grid`。新模型输出的字段别名可被明确规范化，存储结果必须使用当前字段和枚举。

模型仅能在冻结范围内提议操作。载荷、几何、保留文字/数字、证据和范围校验通过后才进入候选事务；验证器决定是否应用。使用相同模型的第二次核验必须如实记作同模型核验，不能宣称不同家族独立验证。无效提案或服务不可用不等于当前 IR 已确认正确。

修复、重试与人工决定生成当前格式的新修订，不修改原包。拒绝一个补丁并不自动确认当前内容；需明确保留当前或完成有效修复。活动消费者受到引用保护；自包含后继完成后，可按保留策略清除已被替代的中间包。

## 运行与验收

统一入口在 `ConsoleESG/scripts`；文档/Console API 在 18080，提取在 18180。后端可独立通过 API/CLI 调用；前端不是执行器。所有接口的状态与准备、执行、覆盖、原值和发布状态分开，不以一个“部分”混淆原因。

运行 `pytest ExtractESG/document/tests` 验证当前包、几何、复核和 API；真实标注基准见[本轮实施](../../../docs/implementation/2026-10-31/README.md)。回归与离线响应回放不证明全报告召回或全部条款科学正确。
