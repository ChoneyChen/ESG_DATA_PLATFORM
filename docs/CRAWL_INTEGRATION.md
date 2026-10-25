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

## 5. 下一步构造

1. **ESEF 包读取器**（独立于 PDF 链路）：解析 XBRL-JSON / iXBRL，产出与 OCR/IR 同级的证据合同；
   失败不影响 PDF 链路。
2. **下载监控**：只读 `crawl.log` 与台账进度，报告资源页显示“下载中/失败/已完成”，不启动爬虫。
3. **身份匹配**：LEI + 期间 + 主体登记的人工确认流程；禁止字符串自动匹配。
4. **权限与密钥**：Crawl 不持 Kodo/对象存储密钥；若未来接入私有对象存储，签名只在平台后端完成。

## 6. 禁止事项

- 不把 ESEF ZIP 写进 `pdf/` 资产目录。
- 不把 Crawl 索引行直接变成报告身份或候选。
- 不在 CrawlESG 目录内安装依赖、写包或改索引。
- 不从文件名或 LEI 自动推断 `publisher_entity_ref`。
