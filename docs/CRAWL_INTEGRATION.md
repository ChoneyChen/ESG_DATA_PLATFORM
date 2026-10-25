# CrawlESG 对接接口（只读索引 + PDF 交接预留）

目标很明确：从 CrawlESG 拿到 **ESG PDF 报告及其身份信息**，交给本链路的报告资源。
当前本地 CrawlESG 导出只有申报索引（`gb_filings_primary.csv`）和 ESEF ZIP 包，**没有可直接
进入 PDF/OCR/IR 链路的 PDF 文件**。因此本链路现在只做两件事：

1. 只读索引，用于确认企业、LEI、财年、报告编号与公开来源；
2. 预留 PDF 交接接口，等 Crawl 侧能提供 PDF 文件后再实现导入。

不读取 ESEF 内容，不把 ESEF ZIP 当作 PDF，不运行爬虫，不自动生成报告身份或候选。

## 1. 当前边界

```
CrawlESG 索引（只读 CSV）
        │ 企业 / LEI / 期末 / 财年 / 报告类型 / 报告编号 / 公开链接
        ▼
Console `#/crawl` 与报告身份编辑器
        │ 仅作为候选元数据，人工确认后才写入报告身份
        ▼
报告资源：标题、发布主体、报告期、PDF 资产仍由本链路登记

CrawlESG PDF（当前未提供）
        │ 未来通过 PDF 交接合同
        ▼
报告资源 → OCR/IR → 提取 → 审核 → 发布（现有链路不变）
```

- `CrawlESG/` 目录受保护：不写入、不暂存、不改索引。
- Console 只读 `GET /api/console/crawl-index` 与 `/crawl-index/companies`；
  状态接口 `GET /api/console/crawl-index/status` 固定返回 `pdf_available=false`、
  `pdf_interface=reserved`。
- 索引选择器只在用户点选后填入来源字段，不自动匹配、不自动创建报告身份。

## 2. 从索引读取的字段

| 字段 | 用途 |
|---|---|
| `report_id` | Crawl 内部稳定编号，写入 `source_report_id` |
| `lei` / `entity_name` | 主体候选；不自动等于 `publisher_entity_ref` |
| `period_end` / `fiscal_year` | 报告期候选；`period_start` 仍需人工确认 |
| `report_type` | 如 ESEF；只用于来源说明 |
| `sha256` | 外部申报包身份；不是 PDF 身份 |
| `package_url` / `viewer_url` / `json_url` | 公开来源链接，供人工核对 |

适配器返回的 `pdf_available` 明确为 `false`；没有 PDF 路径、PDF SHA 或文件大小。

## 3. 本链路仍须显式确认

- 报告标题 `title`
- 发布主体 `publisher_entity_ref`（必须已登记）
- 报告期起始 `period_start`、结束 `period_end`
- 内部 `report_id` 与 PDF 资产绑定
- 指标测量主体与边界

索引只提供候选信息，不能替代以上登记。

## 4. PDF 交接合同（预留，未实现）

Crawl 侧提供 PDF 后，按以下字段交给报告资源：

```json
{
  "source_system": "CrawlESG",
  "source_report_id": "R000001",
  "entity_name": "SEPLAT ENERGY PLC",
  "lei": "029200711C4S4CB2E982",
  "period_end": "2024-12-31",
  "fiscal_year": 2024,
  "report_type": "ESEF",
  "pdf_path": "/absolute/local/path/report.pdf",
  "pdf_sha256": "64-hex",
  "pdf_size_bytes": 1234567,
  "viewer_url": "https://..."
}
```

接收规则（待实现）：

- `pdf_path` 必须位于配置的 Crawl PDF 根目录内，拒绝路径穿越与符号链接；
- `pdf_sha256` 必须是 64 位十六进制，并在服务端重新计算校验；
- PDF 进入报告资源资产库后才允许 OCR/IR；
- `source_report_id`、`lei` 只写入来源字段，不自动生成 `publisher_entity_ref`；
- 同一 `pdf_sha256` 不能重复导入；报告身份冲突时拒绝并保留原记录。

在 PDF 合同未实现前，`#/crawl` 的“导入 PDF（接口预留）”按钮保持禁用。

## 5. 明确不做的事情

- 不解析 ESEF/iXBRL 事实，不把 ESEF ZIP 当作可提取报告。
- 不在 Console 启动 CrawlESG CLI、下载包或管理下载任务。
- 不做自动采集计划、自动送提取或后台 worker。
- 不从文件名、LEI 或索引记录自动推断报告主体。

这些能力只有在 Crawl 侧能稳定提供 **PDF 文件** 后，才按上面的接收合同重新评估。
