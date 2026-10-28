# CrawlESG 对接接口（已移除）

**2026-10-28 起，本链路不再从 CrawlESG 主动接入或读取任何内容。**

按明确要求，Crawl 报告源在前端与后端一并删除：

| 移除项 | 原位置 |
|---|---|
| 报告源页面 `#/crawl` | `ConsoleESG/src/pages/CrawlSource.tsx` |
| 报告库连接卡片与外部索引选择 | `ConsoleESG/src/components/CrawlConnection.tsx`、`CrawlIndexPicker.tsx` |
| 外部索引字段（系统、报告编号、LEI、证券代码、ISIN、包摘要、来源地址） | 报告身份编辑表单 |
| 只读索引接口 `/api/console/crawl-index`、`/crawl-index/status`、`/crawl-index/companies` | `document_ir_extract/backend/src/esg_v2/api/console.py` |
| 索引读取器 `CrawlIndexReader` | `control_plane/crawl_index.py` |

边界保持不变的部分：

- `CrawlESG/` 目录仍是独立保护区，不修改、不暂存；
- 报告只由本链路上传 PDF 进入，报告身份在工作台内逐项登记，不靠文件名或外部索引推断；
- 需要外部信息时由人工核实后手工填写，链路不保存外部索引的读取状态。

本文件保留为历史记录：此前设计过“只读索引 + PDF 交接预留”，因为 Crawl 导出没有可直接进入
PDF/OCR/IR 的 PDF 文件，ESEF ZIP 也不作为 PDF 资产。该方案已整体撤除，不再执行。
