# ExtractESG 定向抽取链路

项目级现状、跨模块关系和新对话交接见仓库根目录 [`../../README.md`](../../README.md)。
本文只维护定向抽取的详细运行和产物说明。

本模块位于 Document IR 下游，只读消费一个 `can_build_evidence=true` 的 Document IR revision，
并按照已编译的标准要求包，把报告中的直接披露转成可追溯、多行、机器可用的 Core records。

它不生产或修改 OCR、PDF 与 Document IR，也不把前端表格当成权威数据。标准包定义“填什么”，
本模块负责“到哪里找、把哪些证据交给模型、怎样校验来源、怎样物化结果”。

默认服务：

- 统一前端：`http://127.0.0.1:18081/`
- 定向抽取后端：`http://127.0.0.1:18180/`
- API 文档：`http://127.0.0.1:18180/api/docs`

## 唯一现行链路

```text
Evidence-ready Document IR + Compiled Standard Package
                         |
                         v
Evidence Inventory + Literal Harvester
                         |
                         v
Query Intent / FactPattern + BM25 + Qwen3 Embedding + RRF
                         |
                         v
Local Evidence Sufficiency Gate
              /                              \
     全报告无可信证据                    存在合理证据
            |                                  |
            v                                  v
  可审计 not_found                  Evidence Selection
                                                |
                                                v
                                  Coherent Evidence Regions
                                      /                    \
                             本地 NuExtract3          七牛云 VLM
                                      \                    /
                                                v
                                  直接多行语义填表 JSON
                                                |
                                                v
                                宽松 Grounding / Contract Guard
                                                |
                                                v
                           Core records + JSONL + XLSX + CSV
```

### 1. Inventory 与本地字面量

Inventory 从 Document IR 读取正文、句子、表格行、表格单元格、表头路径、图表说明、坐标和 crop
引用。Literal Harvester 用本地代码识别数值、数量、百分比、年份、日期和单位；通用单位目录当前
覆盖质量、能源、GHG CO2e、货币与强度复合单位。它们只增加检索与溯源
线索，不决定指标归属，也不会预先制造最终业务事实。

### 2. 检索与充分性判断

查询由标准包的指标语义、元素、概念别名、上下位概念、排除概念和固定维度动态编译。BM25 负责
精确词与数字邻近，Qwen3 Embedding 负责语义召回，FactPattern 检查同一 block 或 table row 内能否
形成“主题 + 披露角色 + 合适数值”的组合。FactPattern 是排序和明确命中的信号，不是替代模型的
最终资格判定：词面组合不完整、但混合检索仍找到高排名且带数量的结构化对象时，Evidence Selection
会把它标记为 `semantic_model_review_fallback` 并送模型判断。只有完整扫描后既没有主题信号、也没有
可送审结构化对象时，才本地生成带扫描证明的 `not_found`。

检索匹配统一处理繁简体、Unicode、范围一/范畴一/Scope 1 等编号写法和 LaTeX `CO_{{2}}e`；这些
归一化只解决排版差异，不决定业务适用性。重复的完全相同语义组会在查询编译时去重，标准并未要求
报告逐字出现的“总量/Gross”等词不得被追加成额外硬门槛。

任务级 `Top N`（1–5，默认 3）限制的是按最终检索得分选出的独立证据对象，不是零散 span 数量。
一个对象可以是一张表、一个图或一个文本区域；命中一张表后仍保留其相关表头和连续行结构。
概念下位词会按标准包声明的适用上下文过滤，例如空气污染不会扩展 COD/BOD 水污染别名。
互斥介质的专属下位概念还会进入负向合同，因此只写 `COD` 而未重复写“废水”的表格行也不会进入
空气污染模型输入；该机制由概念关系动态编译，不依赖 E2-4 条款号硬编码。

### 3. Evidence Selection 与 Regions

`Evidence Selection` 是一个指标的完整审计证据选择。命中表格后会保留该表的可用行、单元格、
堆叠表头、坐标和精确 crop，不因模型窗口较小而提前丢掉整张表。

`Evidence Region` 是实际送模材料。一个 region 只属于一张表、一个图或一个文本区域；大表只按连续
行组和输出容量切分。每个 region 明确携带标准字段合同、原始证据、候选字面量、视觉来源和最大
输出行数，每次模型调用最多发送一张对应 crop。一个独立数量对应一行，多个污染物、年份、公司、
矿区、分项、总计或组成项对应多行。全部 region 完成后只做一次确定性完全重复事实合并。

### 4. 模型直接填表

2026-09-06：主证据之外增加独立小预算 `linked_context`，携带同页/邻页或全报告相关的方法、
脚注与边界说明；`L` 引用可追溯，不是额外数量任务。能源/GHG 量型的强语义数值对象和相邻行
不再被词面资格检查裁掉；污染物质量/介质选择保持已验证路径。

每行增加 `metric_match=match|uncertain|different`，以及按需的 `interpretation_note/context_refs`。
只有模型认定 match 的有据行才按固定标准标签物化；其他已观察测量保留在决定和检查页。
`skipped_targets` 表达主动跳过的数字，`missing_context` 表达缺少的说明。正常停止不触发
“覆盖所有数字”的催补，仅真实输出截断继续写。不新增语义 Guard，不把未知方法的 Scope 2
直接同时标成位置法和市场法。

本地 `NuExtract3` 和七牛云 VLM 共用同一动态字段合同与结果结构，但按各自能力使用不同预算：

- 本地通道使用较小的连贯 region 和最多一张对应图，降低统一内存压力与输出截断风险；
- 云端通道使用更大的 region 与更高输出预算，但仍坚持一次一个视觉对象、最多一张对应 crop；
- 两条通道都直接判断语义归属、读取表格、组合表头和单元格，并输出全部适用事实行；
- 固定值字段由标准包物化，不要求模型重复输出。

模型上下文显式给出 `value_family`、语义意图、固定范围、直接行主题和堆叠表头。语义意图中的词表仅
用于解释召回方向，不是逐词核对清单；模型负责区分总量/子总计/组成项、消费/生产、数量/比例/强度/
容量以及 Scope 1 与 Scope 1+2。局部规则不得因为宽表标题同时出现“排放量及密度”就预先裁掉正确行。

有专门实体字段的标准包，由模型优先填入 `reporting_entity`，适配器不再把相同表头强行复制到
`additional_breakdown`。无此字段的 E2-4 保留原有表头补全。遇到多层表头的明确结构矛盾，目标 cell
携带 `header_alignment_uncertain`：保留原文和完整 crop，由模型判读，不能用错位 IR 表头覆盖视觉结果。

模型字段来源采用短别名：

- `C`：本地已识别的精确字面量；
- `S`：Document IR span 中的精确可见字符串；
- `V`：模型从本次实际发送的 crop 直接读取的值，即使 OCR 或正则没有提前识别也可以使用。

### 5. Guard 的边界

Guard 不重新判断模型的 ESG 语义，不要求视觉值先被 OCR 或正则发现，也不会因为报告没有披露某个
上下文字段而丢弃已找到的主数值。缺少字段保留为空并记为警告。

Guard 只阻止确定性工程错误：未知任务、未知字段、未知行组、伪造来源、`C` 与原文不一致、`S`
不是可见子串、`V` 未绑定本次图片、跨表/跨行错误混配、定量行没有且仅有一个主值，以及违反
标准包明确基数合同的输出。这样既让模型承担语义和视觉工作，也守住可追溯结果不能凭空生成的底线。
模型把合法的单一 group 写成 `['G2']` 时，适配器只做无损单元素解包；多元素数组仍按合同错误保留。
相同错误输出连续重复时直接停止无效重试，使用原文比较，不增加输出哈希链。

## 启动

首次安装或依赖变化后：

```bash
cd ExtractESG/targeted_table_extract
./scripts/setup.sh
```

独立启动后端：

```bash
./scripts/run_all.sh
```

停止：

```bash
./scripts/stop_all.sh
```

桌面 `启动ESG统一工作台.command` 会启动或复用 Document/OCR 后端 `18080`、定向抽取后端
`18180` 和统一前端 `18081`。三条重负载处理链路由统一控制面的单 worker 队列串行调度，业务逻辑
仍在各自后端中独立执行。

默认模型目录：

- `~/Desktop/model/NuExtract3-mlx-4bits`
- `~/Desktop/model/Qwen3-Embedding-0.6B-8bit`

七牛 API Key 只通过控制面的内存 Secret Vault 注入 worker，不写入任务请求、SQLite、日志、文档
或 Result Bundle。

## 无前端运行

```bash
./scripts/run_backend.sh

$HOME/Desktop/model/.runtime/venv/bin/esg-targeted catalog
$HOME/Desktop/model/.runtime/venv/bin/esg-targeted run \
  --ir-run-id <IR_RUN_ID> \
  --package-id esrs.2023-set1.e2-4 \
  --package-version 1.0.0 \
  --metric esrs.2023-set1.e2-4.dp02
```

CLI、API 与统一队列调用同一个 `TargetedExtractionWorkflow`，不依赖浏览器。

## 单次任务产物

```text
targeted_extract_output/<job_id>/
├── manifest.json
├── failure.json                         # 仅任务级失败时存在
├── input/
│   ├── request.json
│   ├── ir-manifest.json
│   └── standard-package.json
├── inventory/
│   ├── index.json
│   ├── spans.jsonl
│   └── candidates.jsonl
├── retrieval/
│   ├── semantic-index.json              # 启用语义检索时存在
│   └── <metric_id>.json
├── packets/
│   ├── <metric_id>.json                 # 完整 Evidence Selection
│   └── <metric_id>.regions.json         # 实际送模的连贯区域
├── decisions/<metric_id>.json           # 原始输出、适配结果、遥测与接受决定
├── guards/<metric_id>.json
├── checkpoints/<metric_id>.json
├── results/                              # 六类权威 Core records
└── exports/
    ├── extraction-result.json
    ├── machine-fill.xlsx
    └── task-summary.csv
```

`packets/<metric>.json` 用于完整审计，`packets/<metric>.regions.json` 才是模型实际看到的材料清单。
模型失败原文会保留在 `decisions/`，但未通过确定性来源合同的数据不会进入 `results/`。

## 成果检查

统一前端允许同时选择 E1-5、E1-6、E2-4 中的细分指标。跨卡片选择只是一份批量任务计划；
控制面会按标准包创建独立的定向抽取 job，因此每个 Result Bundle 仍只绑定一个 package version
和 digest，不会把不同标准的 elements、校验规则或 Core snapshot 混写在一起。

统一前端“抽取结果”页按当前报告和指标展示一条事实一行、一个标准 element 一列；列顺序、主维度
和排序提示由 element `semantic_role` 动态生成，不再假定所有模块都是污染物表。未披露字段为空。
页面同时展示证据页码与摘录、模型 provider/model、region、图片数、输入输出规模、重试、Guard 警告
和召回依据。该页面来自只读 `GET /api/jobs/{job_id}/inspection` ViewModel，不修改权威结果。

`GET /api/result-catalog` 同时报告任务 SQLite 记录数、Result Bundle 目录数、孤立目录和缺失目录。
后端启动时会用同时具有 `input/request.json` 与 `manifest.json`/`failure.json` 的终态 Result Bundle
重建丢失的任务索引；不会猜测没有终态标记的半成品状态。标准目录的有效包和校验失败详情分别由
`GET /api/standards` 与 `GET /api/standards/diagnostics` 提供，前端不得静默隐藏无效包。

任务终态可调用 `DELETE /api/jobs/{job_id}` 清除该任务的全部中间产物和成果；Document IR、OCR、
标准包、模型权重与可复用语义索引不会被删除。

输出编号、六类记录与关联规则见 [Result Bundle 规范](docs/RESULT_BUNDLE_SPEC.md)，组件边界见
[架构文档](docs/ARCHITECTURE.md)。当前项目状态与后续任务只在仓库根 README 维护。

## 当前范围与验证

当前已实现本地/云端并列的直接多行语义填表、视觉 crop 一等来源、宽松确定性 Guard、断点、审计、
导出和统一前端。暂不包含 publication 正式数据库、人工编辑写回、Kodo 和跨服务器分布式队列。

无模型回归测试：

```bash
cd backend
$HOME/Desktop/model/.runtime/venv/bin/python -m pytest -q
```

测试使用固定 Document IR 夹具和假模型，不启动 OCR、Document IR、NuExtract3 或七牛云任务。

保留四个污染物历史 Result Bundle 时，还可从 `backend` 执行只读回放：

```bash
PYTHONPATH=src "$HOME/Desktop/model/.runtime/venv/bin/python" ../scripts/replay_e2_baseline.py
```

该脚本重新适配并校验已保存的模型原文，直接比较字段和证据引用，不调用模型、不写结果包、不新增哈希。
它保护历史输出的兼容性；新提示词下的真实提取效果仍须由用户复跑确认。
