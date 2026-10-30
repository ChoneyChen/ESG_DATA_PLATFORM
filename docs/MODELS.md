# 模型使用合同盘点与标准化建议

日期：2026-10-30。按当前代码及本轮真实 PDF 运行核对；没有通过调用云模型或下载新模型来完成盘点。这里区分生产路径、条件能力和显式可选路径，不能把安装的模型或前端选项当作已经发生的模型调用。

## 1. 当前实际链路

```text
PDF 资源
  → 原生文字层选页（程序）
  → PP-DocLayoutV3 布局模型
  → 可读页面：原生文字/网格回填（程序）
    或不可读页面：本地 PaddleOCR-VL 识别
  → DocumentIR 与物理证据
  → 所选条款的词项/结构检索（程序，无 embedding）
  → 每条独立的 Qwen 来源选择与局部语义阅读
  → 原值回读、字段证据绑定、合同守卫（程序）
  → 独立事实、条款映射、覆盖与组织视图（程序）
```

扫描 PDF 选页以及局部缺少原生文字时，还可以使用系统 Vision OCR。文档视觉修复通过另一条显式启用的 IR 审查路径执行；当前条款提取编排将两类自动审查开关都关闭。这不会把未修复的证据问题标成已经解决。

## 2. 使用位置、输入输出和归属

| 位置 / 模型 | 何时使用 | 输入合同 | 输出与接收边界 | 当前归属与入口 |
|---|---|---|---|---|
| 布局：PP-DocLayoutV3 | 可用原生文字的 PDF 选中页；完整 OCR 管线也需要布局 | 原 PDF 页、像素画布、页编号、布局参数 | 区域类别、框、阅读顺序；不输出条款事实 | 文档模块；`ocr/local_worker.py: run_layout`、`ocr_workflow.py: _read_text_layer` |
| 文字识别：PaddleOCR-VL-1.6 | 原生文字不可用或区域回填失败 | `OcrRunRequest`、选中 PDF 页/图像、`OptionalPayload` | provider JSONL、文本/表格/坐标，随后转换为 DocumentIR | 文档模块；`ocr/providers.py`、`ocr/local_worker.py` |
| 方向：PP-LCNet_x1_0_doc_ori | 完整 OCR 预处理显式开启方向分类 | 页图像与 `useDocOrientationClassify` | 页方向 / 预处理结果 | PaddleOCR 本地管线；默认关闭 |
| 去畸变：UVDoc | 完整 OCR 预处理显式开启去畸变 | 页图像与 `useDocUnwarping` | 校正图像 / 几何处理结果 | PaddleOCR 本地管线；默认关闭 |
| 系统 Vision OCR | 扫描件选页；原生文字区域缺字补读 | PDF、原页号、缩放、语言和并行数 | 每页 JSONL：文本行、置信度、像素框，可选词；不是条款事实 | 文档模块；`ocr/vision_prescan.py`、`vision_ocr.swift` |
| 定量阅读：本地 Qwen3.5-9B-4bit | 每条定量条款、每个有界候选帧 | `ModelPort.complete`：条款阅读卡、物理帧、输出 JSON Schema、预算、取消检查 | `QuantitativeDecision`：行号、列号、身份原词/引用、匹配建议；程序回读原值 | 提取模块；`pipeline/engine.py`、`pipeline/adapters/local_model.py` |
| 定性阅读：同一本地 Qwen | 每条定性条款、每个有界候选帧 | 同一端口；该条自然语言阅读指令、实际身份字段和局部词典 | `QualitativeDecision`：span 编号、可选精确摘录、身份引用、匹配建议；禁止自由改写原文 | 同一提取骨干，仅由条款 `data_kind` 选择值类 |
| IR 视觉审查 / 修复：本地 Qwen | 构建/修复/重试中显式启用审查 | `VlmReviewTask + ReviewPlan`、局部图像、IR 上下文、受控操作合同 | `ReviewerPayload` 的发现、范围决定和补丁提案；`PatchGuard` 与事务规则接收 | 文档模块；`review_orchestrator.py`、`model_runner.py`、`review_provider.py` |
| IR 二次验证：本地 Qwen | 文档审查策略要求验证时 | 局部证据、补丁事务、隔离的验证上下文 | `VerifierPayload`：事务决定、分歧、置信度；再由程序决定能否应用 | 同一文档审查内核；本地为同模型第二次审查，不能称独立模型族验证 |
| IR 审查 / 验证：本地 NuExtract3 | 显式选择 `local_nuextract`，当前条款骨干不选择它 | 同一文档审查协议，适配为本地 worker 输入 | 简单模板可走 native template；复杂 JSON / 动态补丁走内容模式并由服务校验 | `local_nuextract_adapter.py`、`local_nuextract_worker.py`、对应 registry |
| 远程 OCR：Paddle API | 独立 OCR 接口明确使用远程或允许回退时 | OCR 请求及私有凭据通道 | provider 作业/轮询/结果，转换到相同文档产物 | 文档 provider 层；本轮主链路禁用，未调用 |
| 远程 IR 审查：Qiniu 候选模型 | 显式选择 `qiniu` 并具备授权配置时 | 同一审查任务；模型从该 provider 的批准/健康目录选择 | 审查/验证结构及调用回执；不自动接管本地提取 | `QiniuModelRegistry`、`QiniuModelAdapter`、`ModelRunner`；本轮未调用，未向远端查询实际可用目录 |

表中模型名称是仓库配置和本次运行记录的名称。Paddle 的辅助预处理模型由管线配置选择，不能只根据缓存目录存在来宣称运行过。系统 Vision 的具体内部权重版本没有进入当前应用合同，审计中只能明确平台、框架、助手源码版本和输入参数。

## 3. 视觉审查究竟做什么

当前 `ReviewKind` 有七类：水平跨页拼版、页面文字覆盖、表候选分类、表格结构重建、图文绑定、图表语义结构、通用 DocumentIR 审查。它们共享 `ReviewPlan`、允许操作集、`ReviewerPayload`、补丁守卫、事务和可选验证。任务语义属于文档模块，不读取某条 ESG 条款的字典。

模型只提议看得见的 OCR/结构修复；不能直接修改 IR，也不能批准 ESG 事实。无需改动的确认不必总有验证调用；需要验证的事务按策略执行。可选的本地 provider 使用同模型隔离上下文做第二次审查，回执明确其验证策略。

本轮清理了无生产入口引用的旧 `VlmReviewExecutor` 和专属 `QiniuVisionModelRouter`。模型候选回归现在直接检验实际生产使用的 `QiniuModelRegistry`。当前文档视觉审查保留 `AgentReviewOrchestrator → ModelRunner → ReviewProviderBundle` 一条内核。

## 4. Embedding：目前没有生产调用

对当前 `ExtractESG/contracts/src`、`extraction/src`、`pipeline/src`、`document/src`、文档 API、PlatESG 后端、数据库脚本及 Console 源码检索，没有 embedding / Qwen3-Embedding 调用实现。旧向量检索适配器已随旧提取路线退出。

当前对象检索是词项权重、精确标签、正负词、主题/角色组、必需词组和结构优先级。不是 BM25 + embedding + RRF，也没有当前实际调用的 embedding 合同。它的本次调用数是 0。不能沿用原升级规划中的旧架构描述。

如后续多报告试验显示词项召回不足，可独立设计 `EmbeddingPort`：输入受控文本与显式本地模型身份，输出维度、向量、归一策略及实际模型摘要，只参与候选排序。它不能生成事实、改写类别或批准匹配。启用前需要真实增益对照、预算、版本回执和用户要求的复用边界；本轮没有为预留接口引入向量数据库或恢复语义理解缓存。

## 5. 各模型合同已经怎样落地

### 提取阅读

- `ModelPort.complete(instructions, context, schema, max_tokens, cancel_check)` 是骨干边界；文件/MLX 细节停在适配器。
- 条款 `reading.instructions` 是必需自然语言阅读指令。`ClauseContract.reading_card()` 给模型定义、区分/排除、值目标、范围、证据要求和实际身份字段；检索字典和完整正则不混进阅读卡，分别由检索器和绑定器执行。
- 实际输入是文字化表格、正文、对象 ID、坐标及引用；虽然 Qwen 是视觉语言模型，当前 `complete()` 没有传图像，条款阅读是纯文字输入。不能把它统计成截图/视觉复核调用。
- 输出引用按当前可见行/列/span/来源编号生成枚举，语法约束解码后再做 Pydantic 和实际来源核验。定量模型不能返回一个自行编写的数值替代物理原值。
- 默认输出申请 4096 tokens；任务合同允许 128–8192，但适配器实例另有默认 4096 上限，实际预算取二者较小值。当前回执记录实际预算；后续应让能力协商和预算裁定成为同一合同。
- 温度 0，关闭 thinking；取消、超时、截断和来源绑定失败有诊断。截断结果不猜补事实。只保持权重驻留，每条重新执行模型阅读，不复用跨条款理解结果。

### 文档审查

`ReviewModelAdapter.chat_completions(ModelRequest) → ModelResult` 当前同时承载本地和远程 provider。请求含 messages、图像、output_template、预算、温度和任务元数据；结果含实际模型、usage、时延和 provider 回执。

本地文档审查 worker 以 stdin/stdout 协议运行，与提取的进程内 `MlxVlmRuntime` 是两套适配机制。审查请求温度为 0.1。Qwen 及复杂 NuExtract 请求主要依靠提示词模式 + 服务端校验，worker 明确报告 `token_level_schema_enforcement=false`；不能把它与提取骨干的 `grammar_schema` 说成同一约束级别。

### OCR 和系统 Vision

OCR 有类型化入口、provider 路由、健康检查、进度与 manifest；布局/识别输出仍包含 provider 字典和 JSONL。Vision 助手也是独立 JSONL 协议。这些尚未与前述两套调用合同统一，模型和 helper 的实际版本回执也不齐全。

## 6. 仍需要标准化的具体地方

| 优先级 | 当前问题 | 建议 |
|---|---|---|
| 已修复 | OCR 请求、队列及表单默认 `local_paddleocr / allow_api_fallback=false`；IR DTO 和配置默认 `local_qwen`，本次条款编排始终 `allow_cloud_ocr=false` | 云适配器只保留显式选择能力；默认不会静默回退。请求凭据不参与序列化 |
| P1 | 提取、文档审查、OCR/系统 Vision 三类协议的预算、取消、能力和回执不同 | 统一基础调用信封与回执，各模块继续拥有任务语义及专用输出类型 |
| P1 | 同一个 Qwen 模型有进程内和 worker 两条载入/执行机制，最大输出配置也分两处 | 将本地推理适配层独立；由能力合同声明有效上限、约束级别和取消能力，避免不同消费者分别猜测 |
| P1 | 文档调用已用 `ModelRequest / ModelResult / ModelInfo` 的中立命名，结果必须声明实际 provider；提取的动态 JSON 结果仍由本次生成的 Schema 和定量/定性决定模型校验 | 建立 provider 中立且有版本的请求/结果类型，记录真实 provider、模态和校验级别 |
| P1 | 本次污染物帧各约 15.7k 输入 tokens，耗时占主要部分；有身份字段仍未读取 | 用条款声明驱动有界帧投影，保留物理来源与完整审计帧。对明确的局部别名提供带证据的确定性字段绑定；歧义和未知继续待确认。需要用相同报告对照，不能仅按调用次数声称更快或更准 |
| P2 | OCR/系统 Vision 缺乏统一的任务摘要、模型修订和约束级别 | 补齐模型/管线版本、输入摘要、页面统计、实际模式、失败类别及输出摘要；未知内部权重明确未知 |
| P2 | embedding 无生产实现 | 先测召回缺口再决定是否建设；新增适配器应消费同一证据/检索接口，不携带条款业务定义 |

建议的基础调用合同包括：调用 ID、任务种类、所属运行/报告、provider 与实际模型摘要、真实输入模态、有界证据引用、提示词/Schema 摘要、申请与实际预算、网络策略、取消/时限、输出校验级别、usage、时延、重试血缘、失败类别、输出摘要。凭据不进入该信封、任务 JSON、日志或前端。

这份基础合同服务布局、OCR、文档修复和语义阅读；各自的专用输出仍分别验证。它不改变“定量/定性两份事实值合同”，也不让通用模型模块定义污染物、人员或能源语义。此部分是后续标准化方案，未声称已经完成统一模型运行时。

## 7. 已保存的四条真实样本

实际运行及精确计数以 [机器核验记录](verification/2026-10-30-organization.json) 为准：

- PDF 共 92 页，按所选条款选中 22 页；22 页使用 PP-DocLayoutV3 布局 + 原生文字回填。
- 本次 PaddleOCR-VL 视觉识别页为 0，系统 Vision 补读/扫描预选为 0；方向、去畸变、图表识别选项均关闭。
- Qwen 条款阅读 4 次：定量 3 次、定性 1 次，全部 `local_qwen / Qwen3.5-9B-4bit`、纯文字输入、`grammar_schema`，每次实际输出上限 4096。
- IR 视觉审查/验证 0 次，NuExtract 0 次，embedding 0 次，云调用 0 次。
- 4 次阅读累计输入 40,943 tokens，生成 4,724 tokens；提取运行 409.690 秒，不含前面的 PDF 准备。不能把 22 页布局处理混算成 22 次 Qwen 调用。
- 共保存 12 条独立解释：11 条定量 + 1 条定性。相关目标原值与陈述共 7 条，另外 5 条跨介质错配保留原值、单独标为 `different`；实际身份仍有待确认，不是可发布结论。

上表为前轮保存的四条真实样本，未对 32 条或全部报告声称召回率已达标。本轮结构重整后的当前运行、552 项后端回归、12 项 Console 回归和浏览器核验见 [本轮报告](../模块化重构与逐条核验报告.md) 与 [当前机器记录](verification/2026-10-30-modules.json)。测试数量和一次试跑都不能替代后续多报告模型效果评测。
