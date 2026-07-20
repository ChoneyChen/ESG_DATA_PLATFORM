# ESG Document IR Extract

`document_ir_extract` 是从零开始建设的 ESG 报告 OCR 与 Document IR 主链路，
当前完成两块：

1. `PDF -> PaddleOCR-VL API -> ocr_output`；
2. `ocr_output + 原 PDF -> validated/versioned Document IR`。

本模块不负责报告爬取、Kodo 文件索引、公司/年份/行业主数据或全局报告登记。
本地上传、路径和 URL 只是开发输入方式。当前还不抽取 ESG 事实。

## 当前真实链路

```text
PDF
  -> PaddleOCR-VL API
  -> raw JSONL / Markdown / OCR images
  -> page rendering (pypdfium2)
  -> native PDF cross-check (pdfplumber)
  -> Paddle raw layout parsing
  -> canonical coordinates
  -> deterministic Block/Table/Figure deduplication
  -> structure reconstruction
  -> HTML table parsing + pdfplumber cell observations
  -> Table Graph / cross-page links
  -> semantic visual grouping
  -> table and figure crops
  -> page-compound quality routing
  -> optional Qiniu reviewer
  -> AtomicPatch candidate
  -> local Patch Guard
  -> independent Qiniu verifier (different model family)
  -> at most one automatic repair round
  -> accepted local patch application or bounded human fallback
  -> corrected structure rebuild
  -> readiness validation
  -> immutable Document IR revision
```

PaddleOCR-VL 是第一主解析器。七牛模型只处理质量路由选出的复杂表格、
图表和冲突区域。模型结果不能直接覆盖 OCR：主审结果必须先形成局部候选，
通过本地 Guard，再由不同模型家族复核。云 API 失败进入 `auto_review_pending`，
不会被误算为人工审核。只有两轮后仍存在阻断性语义分歧才进入人工队列。

## 产品化输出包

OCR：

```text
ocr_output/<run_id>/
  manifest.json                     # 唯一入口和相对路径目录
  source/request.json
  provider/submit-response.json
  provider/poll-events.jsonl
  provider/result.jsonl             # PaddleOCR-VL 原始返回
  observations/pages/index.json
  observations/pages/page-0001.json
  content/pages/page-0001.md
  artifacts/index.json
  artifacts/images/page-0001/
  artifacts/layouts/page-0001.jpg
  integrity/files.json
```

Document IR：

```text
document_ir_output/<run_id>/
  manifest.json                     # Package 目录与准入状态
  canonical/document.json           # 语义主文件
  canonical/pages/
  canonical/tables/
  canonical/figures/
  canonical/relations/
  canonical/coordinates/
  observations/paddle-layout/
  observations/local-pdf/
  artifacts/index.json
  artifacts/page-images/
  artifacts/crops/tables/
  artifacts/crops/figures/
  quality/
  review/
  integrity/files.json
  exports/document-ir.snapshot.json # 可重建兼容快照
```

OCR 和 IR 的运行状态不属于不可变产物，统一写入 `.local/jobs/`。所有包内
路径均为相对路径；系统生成页文件统一从 `page-0001` 开始。完整命名、编号、
权威数据与审计分层规则见 `docs/ARCHITECTURE.md` 的
`Product Artifact Package Contract`。

每次 Package v1 写入都会自动检查 manifest 类型与入口、严格 run ID、目录逃逸、
漏文件/多余文件、字节数和 SHA-256。新建包不接受随意 run ID；旧版平铺产物保持
只读兼容，不会被原地改写。已存在的 run 目录也不能复用或覆盖。

`manifest.json` 中的 `readiness` 为：

- `ready`
- `ready_with_warnings`
- `auto_review_pending`
- `review_required`
- `failed`

只有 `can_build_evidence=true` 的 revision 才允许进入 Evidence Inventory。

## 后端启动

```bash
./scripts/run_backend.sh
```

默认地址：`http://127.0.0.1:18080`。

后端不依赖前端，也可以直接使用 CLI：

```bash
cd backend
source .venv/bin/activate
esg-v2 ocr --file ../sample.pdf
esg-v2 ir \
  --ocr-run-id ocr-20260718T120000Z-0123456789ab \
  --pdf ../sample.pdf \
  --render-dpi 144
```

只重试一个已路由目标：

```bash
esg-v2 ir \
  --ocr-run-id ocr-20260718T120000Z-0123456789ab \
  --pdf ../sample.pdf \
  --execute-vlm-reviews \
  --review-target-id table-p0004-0001
```

基于旧 IR 建立子 revision：

```bash
esg-v2 ir \
  --ocr-run-id ocr-20260718T120000Z-0123456789ab \
  --pdf ../sample.pdf \
  --parent-ir-run-id ir-20260718T130000Z-abcdef012345
```

主要 API：

```text
POST /api/ocr/jobs
GET  /api/ocr/jobs
GET  /api/ocr/jobs/{run_id}/manifest
GET  /api/ocr/jobs/{run_id}/artifacts

POST /api/document-ir/jobs
GET  /api/document-ir/jobs
GET  /api/document-ir/jobs/{run_id}/manifest
GET  /api/document-ir/jobs/{run_id}/document
GET  /api/document-ir/jobs/{run_id}/validation-report
GET  /api/document-ir/jobs/{run_id}/quality-report
GET  /api/document-ir/jobs/{run_id}/pages
GET  /api/document-ir/jobs/{run_id}/pages/{page_index}
GET  /api/document-ir/jobs/{run_id}/tables
GET  /api/document-ir/jobs/{run_id}/tables/{table_id}
GET  /api/document-ir/jobs/{run_id}/figures
GET  /api/document-ir/jobs/{run_id}/review-tasks
GET  /api/document-ir/jobs/{run_id}/correction-patches
GET  /api/document-ir/jobs/{run_id}/model-calls
GET  /api/document-ir/jobs/{run_id}/reviewer-results
GET  /api/document-ir/jobs/{run_id}/atomic-patches
GET  /api/document-ir/jobs/{run_id}/guard-results
GET  /api/document-ir/jobs/{run_id}/verifier-results
GET  /api/document-ir/jobs/{run_id}/final-decisions
GET  /api/document-ir/jobs/{run_id}/candidate-revisions
GET  /api/document-ir/jobs/{run_id}/reviews/{task_id}
GET  /api/document-ir/jobs/{run_id}/conflict-groups
GET  /api/document-ir/jobs/{run_id}/structure-edges
GET  /api/document-ir/jobs/{run_id}/artifact-index
GET  /api/document-ir/jobs/{run_id}/artifacts
GET  /api/document-ir/revisions/{ocr_run_id}
POST /api/document-ir/jobs/{run_id}/patches/{patch_id}/decision
POST /api/document-ir/jobs/{run_id}/review-tasks/{task_id}/decision
POST /api/document-ir/jobs/{run_id}/repair
GET  /api/models/status
```

## 前端启动

```bash
./scripts/run_frontend.sh
```

打开 `http://127.0.0.1:18081`。

也可以同时启动前后端：

```bash
./scripts/run_all.sh
```

`run_all.sh` 会检查两个服务、复用已经运行的正确实例、等待健康检查通过并
自动打开前端。启动日志保存在 `.local/logs/`；关闭启动器终端会停止由本次
启动器拉起的服务。

前端是独立操作台，保留 OCR 上传、状态、Manifest、Markdown 和全部文件浏览，
并增加：

- 10 个后端节点对应的可视化链路；
- OCR/IR 运行历史和 revision；
- readiness、Evidence 准入门槛和覆盖率；
- 页图、Paddle layout 坐标框和对象详情；
- 表格单元格与 Table Graph；
- 图片/图表区域截图；
- 复合 Review Task、每一轮模型调用、Reviewer 结果和模型健康状态；
- Atomic Patch、修正前后 diff、本地 Guard、独立 Verifier 和最终决策；
- 仅针对 `human_required` 的人工接受/拒绝操作，以及 Targeted Repair；
- `document_ir_output` 全部中间文件预览。
- OCR/IR 文件按 Package 入口、输入、服务商原件、可读内容、观察、Canonical、
  图像、质量、复核、完整性和兼容导出分组浏览；切换后端会清空旧上下文，
  不接受上一个后端的迟到响应。

## 密钥

前端可以临时填写 PaddleOCR 和七牛 API Key。密钥只随请求发送，不写入产物。
也可以放到被 Git 忽略的 `.env.local`：

```text
PADDLEOCR_VL_API_TOKEN=...
QINIU_API_KEY=...
QINIU_BASE_URL=https://api.qnaigc.com/v1
QINIU_VLM_MODEL=...
ESG_V2_MAX_VLM_REVIEWS_PER_IR_RUN=3
```

`QINIU_VLM_MODEL` 可以填写逗号分隔的固定降级链。未设置时，系统从 `/models`
建立已批准且未退役的视觉模型候选顺序；429/5xx、超时和格式错误会尝试下一个
模型并写入健康状态。本地视觉输入会压缩为七牛建议的 JPEG data URI；服务器阶段
优先使用 Kodo/CDN 地址，避免大体积内联传输。

## 测试

```bash
cd backend
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

当前测试覆盖页图渲染、坐标映射、Paddle raw layout、结构关系、Table Graph、
区域截图、模型路由、自动确认、自动修正、云故障延期、双模型分歧、Patch Guard、
受限表格插行、人工子版本、Targeted Repair、Package v1 路径/哈希校验、OCR
脱敏与原始响应保真、旧版 IR 图片迁移和 FastAPI 中间视图。当前为 28 项测试。

## 下一阶段

Document IR 之后才建设：

- Evidence Inventory 与 Evidence Packet；
- Full Harvest 与 Targeted Recall；
- 数值/单位/期间/范围规范化；
- Concept 与 Requirement Mapping；
- 独立事实验证、人工审核和 Publication。
