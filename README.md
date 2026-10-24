# ESG Data Platform · 当前项目手册

本文件是项目级唯一当前事实入口，更新于 **2026-10-23**。每轮变化见[更新日志](docs/CHANGELOG.md)，
服务与实测状态见[系统状态](docs/SYSTEM_STATE.md)，历史实施报告已移出工作树（用 `git log --all -- <原路径>` 追溯）。

## 1. 这个系统做什么

从任意 PDF 可持续发展报告里，按**明确选择的条款**提取数值与原文证据，经人工审核后发布为可比较的正式数据，
并在公开数据网站上按企业与指标查看、比较和导出。

一条数据从报告到网站要经过四步：

```
① 报告        ② 提取                   ③ 审核              ④ 发布
选 PDF    →   读取页面＋构建结构证据     事实 / 映射 / 口径   →  候选入库 → 发布 → 公开网站
              ＋按条款提取数值          分别批准
```

读取页面不是独立步骤：它发生在提取任务内部，用户只需要选报告和指标集。

## 2. 四个部分，各管什么

| 部分 | 角色 | 是否有人工界面 |
|---|---|---|
| **ConsoleESG/** | 操作台：报告库、提取、审核、发布、标准与运行 | **是**，产品界面（18081） |
| **ExtractESG/** | 提取引擎与两个后端 API：文档读取（OCR→DocumentIR）、定向提取（条款→事实） | 否，只提供 HTTP API 与命令行 |
| **PlatESG/** | 公开数据产品（8765）：介绍页、平台数据页、API 文档、发布库与审核接口 | 是，面向读者的网站 |
| **database/** | 存储合同与 SQL：来源事实、候选、审核、观测、数据版本 | 否 |

**Console 与 Extract 的关系**

- ConsoleESG 是**唯一的操作界面**，只消费后端合同：它不解析 PDF、不调用模型、不决定事实。
- ExtractESG 是**引擎**：`document_ir_extract`（PDF→OCR→DocumentIR）与 `targeted_table_extract`
  （DocumentIR→条款事实）两个服务，加上 `extraction`/`standards`/`control_plane` 等库。
- 二者通过 HTTP 合同连接：Console 用同源代理 `/document`、`/targeted`、`/publication` 访问三个服务，
  前端不直接读写文件系统、数据库或模型。

**从提取到发布的一条命令**：`scripts/publish_session.py --session plan-… --publisher "…" --entity org:…`
确认报告身份、登记主体、交付成果包，并列出每条候选能否审核、缺什么口径；审核与发布仍在操作台完成。

**Extract 自己的前端已经移除（2026-10-23）**：早期 `document_ir_extract/frontend` 是一个原生 JS
工作台（`/legacy/` 入口），功能已被 ConsoleESG 完整覆盖（报告库、提取创建、证据查看、审核、发布、
标准、运行诊断）。保留两套界面只会让同一合同的措辞与行为漂移，因此删除；对应浏览器合同测试改为
服务端合同测试。现在仓库里只有两个前端：ConsoleESG（操作台）与 `PlatESG/frontend`（公开网站）。

## 3. 快速开始

```sh
ExtractESG/scripts/start_local_workbenches.sh    # Document 18080 · Targeted 18180 · Console 18081
PlatESG/scripts/run.sh                           # 数据平台与公开网站 8765
```

| 入口 | 地址 | 用途 |
|---|---|---|
| 操作台 | http://127.0.0.1:18081/ | 报告 → 提取 → 审核 → 发布 |
| Document API | http://127.0.0.1:18080/docs | 资产、连续处理、会话、统一队列 |
| Targeted API | http://127.0.0.1:18180/api/docs | 固定证据、条款投影、事实与结果 |
| 数据平台 / 公开网站 | http://127.0.0.1:8765/ | 公开 `/v1`、受控 `/internal/v1` |
| Console 开发服务 | http://127.0.0.1:5180/ | Vite 热更新（需先起三个后端） |
| 数据平台开发服务 | http://127.0.0.1:5178/ | 产品前端 Vite |

操作台不需要粘贴任何令牌：本机回环请求由数据平台识别为 `local-operator`（读取、审核、发布、管理）。
从其他机器访问或脚本调用仍须 Bearer 令牌；`PLATESG_LOCAL_OPERATOR=0` 关闭本机识别。

各服务启动时冻结源码指纹，磁盘源码变更会要求重启，端口能通不等于业务就绪。

## 4. 数据流

```
PDF（pdf/ 资产目录，SHA-256 身份）
  → 页面读取（文字层优先，图片页才走本地视觉识别）         ocr_output/
  → DocumentIR（正文、表格单元格、逻辑表、跨页、坐标）      document_ir_output/
  → 提取：每张候选表一次本地 Qwen 调用 + 确定性绑定/守卫     targeted_extract_output/
  → 成果包（来源事实、标准映射、证据位置）
  → 交付（/internal/v1/deliveries）→ 候选
  → 事实审核 / 映射审核 / 测量口径
  → 发布（PLATESG_AUTO_PUBLISH=0 时人工触发）→ 观测与冻结数据版本
  → 公开网站 /v1
```

PDF 只是检验链路是否成立的样本：规则来自可推广的类别（单位与标签判定、许可上限与实际排放、
跨页表格、显式合计列、父/子条款），**不为任何一份报告写特例**。

## 5. 指标集与标准

| 指标集 | 内容 | 用途 |
|---|---|---|
| `base16` | E1–E3 环境核心 16 条（能源、温室气体、空气与水污染物、水与资源） | 快速环境体检 |
| `priority32` | 基础 16 + 16 条（化石能源、排放总量与强度、废弃物、材料、员工人数/流失率/高管性别/培训/工伤/薪酬/歧视、董事会性别、腐败定罪、付款天数） | 一次覆盖 ESG 主要披露 |
| `quantitative` | 全部带数值的 38 条（数量、比例、强度；不含“污染物测量方法”陈述） | 一次选中全部定量条款 |
| `universal` | 跨全部标准包挑选的 34 条通用必备（能源与排放、水与废弃物、材料与再生比例、员工与安全、治理与投诉） | 新报告的首选组合 |

指标集是**数据**：`catalog.PROFILES = {"base16": …, "priority32": …, "quantitative": …, "universal": …}`，引擎按请求中的 `metric_profile` 运行，
条款包、链路与报告三者互不绑定；新增集合不新增分支，也不改变任何单条条款的提取逻辑。

- **标准包**：1,126 个独立原编号编译为 99 个草稿包；承载重点条款的 11 个包已按条款撰写并发布 2.2.0。
- **平台范围**：55 个原编号（用户原 49 条按原顺序保留 + 重点条款中不在该清单的 6 项）。
- **平台字典 1.4.5**：55 条全部就绪；重点条款 32 条与其余主要条款均可提取、审核并发布。

## 6. 目录

```text
ESG Database/                      # 工作区（仓库外一层）
├── pdf/                           # 原始 PDF 与资产目录（运行数据）
├── .local/platform-runtime/       # 唯一队列、执行锁、服务日志
└── ESG_DATA_PLATFORM/             # 仓库
    ├── README.md                  # 本手册
    ├── docs/                      # 更新日志、系统状态、合同说明、迁移原件
    ├── ConsoleESG/                # 操作台（React + Vite）
    ├── PlatESG/                   # 公开产品：backend / frontend / catalog / review
    ├── database/                  # PostgreSQL SQL、导入、审核、发布
    ├── CrawlESG/                  # 独立保护区：不修改、不暂存
    └── ExtractESG/                # 提取引擎
        ├── document_ir_extract/   # Document API 宿主（无自带前端）
        ├── targeted_table_extract/# Targeted API 宿主
        ├── document_processing/   # PDF/OCR/IR、模型适配、资产生命周期
        ├── control_plane/         # 队列、计划、处理协调、事件
        ├── extraction/            # 检索、来源事实、映射、foundation16/32 引擎
        ├── standards/             # 标准目录、作者源、编译发布、profiles
        ├── contracts/             # 共享类型
        └── shared_runtime/        # 进度、生命周期、路径定位
```

Python 包名仍是 `esg_v2`、`esg_targeted`；业务目录已经真实迁移，旧 import 路径通过符号链接指向同一份源码。

## 7. 测试与验收

| 套件 | 命令 | 结果（2026-10-23） |
|---|---|---|
| ConsoleESG | `cd ConsoleESG && npm test` | 213 通过 |
| Document 后端 | `pytest ExtractESG/document_ir_extract/backend/tests` | 657 通过 |
| 提取后端 | `pytest ExtractESG/targeted_table_extract/backend/tests` | 698 通过；16 项既有失败（版本常量、删除契约） |
| 标准包 | `pytest ExtractESG/standards/tests` | 116 通过 |
| PlatESG | `pytest PlatESG/tests` | 70 通过 |
| 数据库 | `cd database && npm test` | 54 通过、2 跳过 |
| 金标回归（9 份报告，只重跑提取） | `.local/foundation-lab/reg_only.sh` | 158/159、0 错 0 漏、1 项已知误报 |

工程通过不等于提取准确：金标与未见报告核验（宁德、越秀、紫金、隆基、航空工业、中华煤气、法拉电子）
都在[系统状态](docs/SYSTEM_STATE.md)中记录，并逐条对照原文。

## 8. 开发约定

- `CrawlESG/` 是独立保护区，不修改、不暂存。
- 密钥只进入受控环境配置与进程内存，不保存到 URL、浏览器存储、任务库、日志、成果包或文档。
- 所有模型调用默认本地；云模型、云 OCR 必须显式授权，`allow_cloud_ocr=false`。
- 正式库为 `PlatESG/.local/data`（`PlatESG/.local/demo-data` 是历史快照，未被服务使用），不重置、不迁移；
  产物清理走 `POST /internal/v1/artifact-purge`，不直接改库；`PLATESG_AUTO_PUBLISH=0` 保持人工发布。
- 真实 OCR、模型任务与发布由用户发起；离线测试使用假阶段与隔离库。
- 前端只消费后端合同，不推断完成、发布或未披露；来源修订与重新执行都是显式动作。
- 提交身份：`Choney Chen <ChoneyChen@users.noreply.github.com>`；远端 `ChoneyChen/ESG_DATA_PLATFORM` 为公开仓库。

## 9. 文档

| 文档 | 内容 |
|---|---|
| [更新日志](docs/CHANGELOG.md) | 每轮实际变化与结论 |
| [系统状态](docs/SYSTEM_STATE.md) | 服务、端口、数据位置、实测质量、测试与已知问题 |
| [操作台](ConsoleESG/README.md) | 页面、合同与交互约定 |
| [数据产品](PlatESG/README.md) | 公开 `/v1`、内部接口与发布库 |
| [标准系统](ExtractESG/standards/README.md) | 作者源、编译、发布与版本不可变规则 |
| [文档读取](ExtractESG/document_ir_extract/README.md) | OCR 与 DocumentIR 的详细合同 |
| [定向提取](ExtractESG/targeted_table_extract/README.md) | 条款投影、事实与结果包 |
| [数据存储](docs/SCIENTIFIC_DATA_STORAGE.md) | 事实、口径、版本与发布的存储合同 |
| [工作台操作](docs/PRODUCTION_WORKBENCH_20261010.md) | 日常操作、权限与恢复 |
