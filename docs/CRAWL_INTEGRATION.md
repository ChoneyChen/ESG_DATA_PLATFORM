# CrawlESG 对接接口（只读）

本文固定 CrawlESG 与本链路的边界。CrawlESG 是**报告采集源**，不是 PDF 资产库，
也不是候选或发布库。控制台只读它的索引与下载台账，不运行爬虫、不改它的索引、不自动导入。

## 1. 边界

```
CrawlESG                         认 证 边 界                  本链路
─────────                        ──────────                  ───────
index/gb_filings_primary.csv  ──►  CrawlIndexReader  ──►  报告资源页 / 报告身份草稿
download_ledger.csv           ──►  （只读 CSV）        ──►  包可用性、下载状态
packages/*.zip (ESEF)         ──►  只读文件存在性      ──►  不进入 PDF / OCR / IR / 候选
```

- CrawlESG 目录受保护：本链路不写入、不暂存、不删除其中文件。
- 依赖与下载工作区在仓库外：`<workspace>/.local/crawl-venv`、`<workspace>/.local/crawl-work`。
- Console 通过 `GET /api/console/crawl-index/*` 读取，接口不返回 PDF 内容，也不返回可写地址。

## 2. 读取的字段

索引（每行一份申报）：

| 字段 | 用途 |
|---|---|
| `report_id` | Crawl 内部稳定编号，写入报告身份的 `source_report_id` |
| `lei` / `entity_name` | 主体候选；不自动等于 `publisher_entity_ref` |
| `period_end` / `fiscal_year` | 报告期候选；不自动填 `period_start` |
| `sha256` | 外部包身份，写入 `source_package_sha256` |
| `package_url` / `viewer_url` / `json_url` | 公开来源链接，供人工核对 |
| `report_type` | 如 ESEF；用于提示产物类型 |

下载台账（每份已尝试下载的包）：

| 字段 | 用途 |
|---|---|
| `file` | 包文件名；与 `packages/` 下文件对应 |
| `status` | `ok` / 失败状态；只有 `ok` 且文件存在才算已下载 |
| `size_mb` | 包大小展示 |
| `esg_flag` | 是否探测到 ESRS 声明；只是排序提示，不是抽取结果 |

适配器返回 `artifact_kind=ESEF_ZIP`、`pdf_available=false`。**ESEF ZIP 不是 PDF**，
不能被当作 `pdf/` 资产，也不能直接进入 OCR/IR。

## 3. 本地仍须显式确认

- 报告标题 `title`
- 发布主体 `publisher_entity_ref`（必须已登记）
- 报告期起始 `period_start`、报告期结束 `period_end`
- 内部 `report_id` 与 PDF 资产绑定
- 指标测量主体与边界

Crawl 选择器只把这些作为草稿填入；保存报告身份仍需操作员确认。

## 4. 命令

```bash
ExtractESG/scripts/setup_crawl_esg.sh
```

脚本在仓库外创建 venv 并安装 `requests`、`pandas`，然后打印 index/plan/download 命令。
当前工作区实测：

```bash
<workspace>/.local/crawl-venv/bin/python CrawlESG/src/esef_crawler.py plan \
  --index <repo>/CrawlESG/index/gb_filings_primary.csv --years 2024 \
  --out-dir <workspace>/.local/crawl-work --pkg-dir <workspace>/.local/crawl-work/packages
```

## 5. ESEF 读取器（已实现）

入口：

- `GET /api/console/esef-packages/{source_report_id}`：包摘要、身份、上下文/单位、事实样例。
- `GET /api/console/esef-packages/{source_report_id}/facts?query=&offset=&limit=`：事实分页。
- Python：`esg_v2.control_plane.esef_reader.read_package(path)`；CLI
  `python -m esg_v2.control_plane.esef_reader --package <zip>`（以服务环境为准）。

读取器行为：

- 直接从 ZIP 流式解析 iXBRL XHTML，不把 100–200 MB 报告解压到磁盘。
- 保留 `ix:nonFraction` 与 `ix:nonNumeric` 事实、`xbrli:context` 期间/实体/维度、`xbrli:unit` 单位、
  `ix:continuation` 续段，忽略 `ix:exclude` 脚注。
- 数值按 `scale`、`sign`、`ixt:num-dot-decimal`/comma-decimal 归一；原文本始终保留。
- 身份只输出 `entity_identifiers`、`period_start/end`、`instants`；不写报告身份。
- 结果按 ZIP SHA-256 缓存在 `<workspace>/.local/esef-cache/<sha>/reader.json`；重复读取不重新解析。
- 真实包实测：SEPLAT 2024，112 MB ZIP，705 个事实（610 数值 / 95 文本）、49 个上下文、4 个单位，
  首次解析约 1.6 秒，无解析错误。

事实合同（每条）：

```
fact_id, concept, kind(numeric|text), value_text, value_numeric, raw_text,
unit[], unit_divide, scale, sign, decimals, format,
context_ref, period_start, period_end, instant, dimensions{},
file, order, esg_related
```

边界：读取器输出的是**证据事实**，不是已映射的 ESRS 指标，也不是候选。它不调用模型、不写发布库。

## 6. 运行控制（已实现，进程边界）

Console 的 CrawlESG 独立页只启动官方 CLI，不导入 CrawlESG 代码：

| 控制 | 说明 |
|---|---|
| `plan` | 干跑统计；只读索引与本地文件 |
| `download` | 下载 ESEF 包；显式 `confirm` 后才允许，写 `packages/` 与台账 |
| `verify` | 校验已下文件 |
| 停止 | 只向该任务的独立进程组发 SIGTERM/SIGKILL |
| 日志 | 写入 `<workspace>/.local/crawl-work/.runner/`，页面显示尾部 |

API：

- `GET /api/console/crawl-runner/status`
- `GET /api/console/crawl-runner/jobs?limit=`
- `GET /api/console/crawl-runner/jobs/{job_id}`
- `POST /api/console/crawl-runner/jobs`
- `POST /api/console/crawl-runner/jobs/{job_id}/stop`

启动/停止需要平台 admin；任务一次只允许一个。命令参数由服务端白名单构造，不经过 shell。
CrawlESG 目录始终只读；Python 环境、索引和输出目录都可配置但默认在仓库外运行时目录。

## 7. 自动采集规划（设置与扫描已实现，执行 worker 预留）

独立页“自动采集规划”保存以下设置到 `<workspace>/.local/crawl-intake/settings.json`：

- 总开关、监视下载台账、自动读取 ESEF、自动建报告身份草稿、自动送提取计划、只处理 ESG 标记包
- 扫描间隔、国家、财年、提取配置（foundation16 / platform_major / quantitative / base16 / priority32）

`POST /api/console/crawl-intake/scan?mark_seen=false` 只读取下载台账，返回：

- `new_packages`：台账中新出现的包
- `candidates`：按国家/财年/ESG 标记过滤后的包
- `filtered_out`：被过滤排除的包
- `actions`：`read_esef` / `create_report_draft` / `queue_extraction` 计划动作

当前状态是 **plan_only**：不自动下载、不读取包、不调用模型、不写候选。`mark_seen=true` 只推进已见状态。
接入执行 worker 后，建议按以下幂等键派发，避免重复：

```
(source_package_sha256, reader_schema_version)   → ESEF 读取
(source_package_sha256, report_ref, profile)     → 提取计划
```

## 8. 全自动流程规划

```
① CrawlESG 外部 CLI
   index → download → packages/ + download_ledger.csv
        │（只读边界）
        ▼
② CrawlIntake.scan
   new package → filter → plan actions（当前 plan_only）
        │
        ▼
③ EsefPackageReader
   ZIP → facts/contexts/units → cache（已实现）
        │
        ▼
④ ESEF → 指标映射适配器（待构造）
   ESRS/concept → 55 条指标定义 → 证据包/候选合同
        │
        ▼
⑤ 现有交付、审核、发布
   candidates → review → publication（不变）
```

各阶段失败隔离：Crawl 下载失败不影响 PDF 链；ESEF 解析失败不产生候选；映射失败保留读取事实；
审核/发布仍按现有不可变合同执行。身份匹配必须人工确认，不能用 LEI 或文件名自动生成
`publisher_entity_ref`。

## 9. 禁止事项

- 不把 ESEF ZIP 写进 `pdf/` 资产目录。
- 不把 Crawl 索引行直接变成报告身份或候选。
- 不在 CrawlESG 目录内安装依赖、写包或改索引。
- 不从文件名或 LEI 自动推断 `publisher_entity_ref`。
- 不让 Console 直接 import CrawlESG 的 Python 模块；只通过 CLI、CSV、ZIP 和进程状态交互。

