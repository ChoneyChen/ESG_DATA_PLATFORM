# 本次有效HTTP接口与文件定位

基线与采样时间见 [接口机器清单](interfaces.json)。有效路径来自运行进程OpenAPI，文件定位按所属服务、方法、声明路径和operation_id关联；另已核对36处动态注册。此表表达注册状态，不表示已经调用写接口；本轮GET实测状态见 [运行证据](current-state.json)。

195个操作包括物理文档高级能力。旧v1/internal/review的410兜底和静态/同源代理不全部出现在OpenAPI，参见主报告。重复函数名如有多个候选位置均保留，不据此认定业务重复。

## 18080 — ESG v2 Extraction Backend（114路径/124操作）

| 方法 | 有效路径 | 定位文件与行 |
|---|---|---|
| GET | `/api/pdf-library` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @55 |
| GET | `/api/report-library` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @59 |
| GET | `/api/ocr-library` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @79 |
| GET | `/api/ocr-library/{run_id}` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @83 |
| DELETE | `/api/ocr-library/{run_id}` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @88 |
| GET | `/api/document-ir/jobs/{run_id}/deletion` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @97 |
| DELETE | `/api/document-ir/jobs/{run_id}` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @102 |
| GET | `/api/document-ir/jobs/{run_id}` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @90 |
| GET | `/api/report-assets/{asset_id}/deletion` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @111 |
| DELETE | `/api/report-assets/{asset_id}` | [ExtractESG/document/src/esg_document/api/library_deletion.py](../../../ExtractESG/document/src/esg_document/api/library_deletion.py) @118 |
| GET | `/api/processing-status` | [ExtractESG/document/src/esg_document/api/processing_status.py](../../../ExtractESG/document/src/esg_document/api/processing_status.py) @31 |
| GET | `/health` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @246 |
| GET | `/health/compatibility` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @263 |
| GET | `/api/pipeline/tasks` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @378 |
| GET | `/api/pipeline/tasks/{task_id}` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @403 |
| GET | `/api/pipeline/tasks/{task_id}/events` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @411 |
| PUT | `/api/pipeline/tasks/order` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @423 |
| POST | `/api/pipeline/tasks/{task_id}/cancel` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @439 |
| POST | `/api/pipeline/tasks/native/{native_id}/cancel` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @453 |
| GET | `/api/reports` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @47；[ExtractESG/document/src/esg_document/api/report_resources.py](../../../ExtractESG/document/src/esg_document/api/report_resources.py) @50 |
| POST | `/api/reports` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @54 |
| GET | `/api/reports/{report_id}` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @60 |
| PATCH | `/api/reports/{report_id}` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @64 |
| POST | `/api/reports/{report_id}/versions` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @68 |
| GET | `/api/report-processing/runs` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @77 |
| POST | `/api/report-processing/runs` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @84 |
| GET | `/api/report-processing/runs/{processing_id}` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @89 |
| GET | `/api/report-processing/runs/{processing_id}/events` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @93 |
| GET | `/api/report-processing/runs/{processing_id}/pages` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @105 |
| GET | `/api/report-processing/runs/{processing_id}/pages/{page_index}` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @109 |
| POST | `/api/report-processing/runs/{processing_id}/cancel` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @113 |
| POST | `/api/report-processing/runs/{processing_id}/resume` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @121 |
| POST | `/api/report-processing/runs/{processing_id}/evidence` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @130 |
| GET | `/api/report-processing/runs/{processing_id}/evidence-versions` | [ExtractESG/document/src/esg_document/api/report_processing.py](../../../ExtractESG/document/src/esg_document/api/report_processing.py) @136 |
| GET | `/api/report-assets` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @569 |
| POST | `/api/report-assets` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @604 |
| GET | `/api/report-assets/{asset_id}/file` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @618 |
| GET | `/api/report-sources/{source_sha256}/file` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @633 |
| GET | `/api/report-assets/{asset_id}/lifecycle` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @647 |
| POST | `/api/report-assets/{asset_id}/archive` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @655 |
| POST | `/api/report-assets/{asset_id}/restore` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @655 |
| POST | `/api/report-assets/{asset_id}/discard` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @655 |
| GET | `/api/report-assets-history` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @677 |
| POST | `/api/pipeline/tasks/ocr` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @689 |
| POST | `/api/pipeline/tasks/document-ir` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @765 |
| GET | `/api/models/status` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @911 |
| GET | `/api/storage/inventory` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @918 |
| POST | `/api/storage/deletion-plans` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @923 |
| POST | `/api/storage/deletion-plans/{plan_id}/execute` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @933 |
| GET | `/api/storage/trash` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @957 |
| POST | `/api/storage/trash/{cleanup_id}/restore` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @965 |
| POST | `/api/storage/trash/{cleanup_id}/purge` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @975 |
| GET | `/api/ocr/jobs` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1119 |
| POST | `/api/ocr/jobs` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @988 |
| GET | `/api/ocr/providers/status` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1114 |
| GET | `/api/ocr/jobs/{run_id}` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1145 |
| GET | `/api/ocr/jobs/{run_id}/manifest` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1165 |
| GET | `/api/ocr/jobs/{run_id}/artifacts` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1178 |
| GET | `/api/document-ir/jobs` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @35 |
| POST | `/api/document-ir/jobs` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1201 |
| POST | `/api/document-ir/jobs/{run_id}/patches/{patch_id}/decision` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1213 |
| POST | `/api/document-ir/jobs/{run_id}/review-tasks/{task_id}/decision` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1229 |
| POST | `/api/document-ir/jobs/{run_id}/repair` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1245 |
| POST | `/api/document-ir/jobs/{run_id}/reviews/retry` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1263 |
| GET | `/api/report-resources` | [ExtractESG/document/src/esg_document/api/report_resources.py](../../../ExtractESG/document/src/esg_document/api/report_resources.py) @50 |
| POST | `/api/report-resources/imports` | [ExtractESG/document/src/esg_document/api/report_resources.py](../../../ExtractESG/document/src/esg_document/api/report_resources.py) @79 |
| POST | `/api/report-resources/uploads` | [ExtractESG/document/src/esg_document/api/report_resources.py](../../../ExtractESG/document/src/esg_document/api/report_resources.py) @89 |
| GET | `/api/report-resources/{pdf_id}` | [ExtractESG/document/src/esg_document/api/report_resources.py](../../../ExtractESG/document/src/esg_document/api/report_resources.py) @102 |
| PUT | `/api/report-resources/{pdf_id}` | [ExtractESG/document/src/esg_document/api/report_resources.py](../../../ExtractESG/document/src/esg_document/api/report_resources.py) @107 |
| GET | `/api/evidence/{ir_run_id}` | [ExtractESG/document/src/esg_document/api/evidence.py](../../../ExtractESG/document/src/esg_document/api/evidence.py) @25 |
| POST | `/api/evidence/{ir_run_id}/search` | [ExtractESG/document/src/esg_document/api/evidence.py](../../../ExtractESG/document/src/esg_document/api/evidence.py) @29 |
| POST | `/api/evidence/{ir_run_id}/read` | [ExtractESG/document/src/esg_document/api/evidence.py](../../../ExtractESG/document/src/esg_document/api/evidence.py) @33 |
| GET | `/api/pipeline/status` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1394 |
| GET | `/api/operations/tasks` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1407 |
| GET | `/api/operations/tasks/{task_id}` | [ExtractESG/document/src/esg_document/api/main.py](../../../ExtractESG/document/src/esg_document/api/main.py) @1415 |
| GET | `/api/document-ir/review-worklist` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @54 |
| GET | `/api/document-ir/jobs/{run_id}/manifest` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @106 |
| GET | `/api/document-ir/jobs/{run_id}/quality-report` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @115 |
| GET | `/api/document-ir/jobs/{run_id}/validation-report` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @118 |
| GET | `/api/document-ir/jobs/{run_id}/document` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @121 |
| GET | `/api/document-ir/jobs/{run_id}/pages` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @124 |
| GET | `/api/document-ir/jobs/{run_id}/pages/{page_index}` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @130 |
| GET | `/api/document-ir/jobs/{run_id}/tables` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @140 |
| GET | `/api/document-ir/jobs/{run_id}/tables/{table_id}` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @143 |
| GET | `/api/document-ir/jobs/{run_id}/logical-tables` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @152 |
| GET | `/api/document-ir/jobs/{run_id}/logical-tables/{logical_table_id}` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @155 |
| GET | `/api/document-ir/jobs/{run_id}/figures` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @166 |
| GET | `/api/document-ir/jobs/{run_id}/structure-edges` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @172 |
| GET | `/api/document-ir/jobs/{run_id}/correction-patches` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @177 |
| GET | `/api/document-ir/jobs/{run_id}/model-calls` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @182 |
| GET | `/api/document-ir/jobs/{run_id}/reviewer-results` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @185 |
| GET | `/api/document-ir/jobs/{run_id}/atomic-patches` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @188 |
| GET | `/api/document-ir/jobs/{run_id}/patch-transactions` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @191 |
| GET | `/api/document-ir/jobs/{run_id}/guard-results` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @196 |
| GET | `/api/document-ir/jobs/{run_id}/verifier-results` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @199 |
| GET | `/api/document-ir/jobs/{run_id}/final-decisions` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @202 |
| GET | `/api/document-ir/jobs/{run_id}/candidate-revisions` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @205 |
| GET | `/api/document-ir/jobs/{run_id}/reviews/{task_id}` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @210 |
| GET | `/api/document-ir/jobs/{run_id}/conflict-groups` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @221 |
| GET | `/api/document-ir/jobs/{run_id}/artifact-index` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @224 |
| GET | `/api/document-ir/revisions/{ocr_run_id}` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @230 |
| GET | `/api/document-ir/documents` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @239 |
| GET | `/api/document-ir/documents/{document_id}/revisions` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @245 |
| GET | `/api/document-ir/jobs/{run_id}/review-tasks` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @254 |
| GET | `/api/document-ir/jobs/{run_id}/human-review/inbox` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @260 |
| GET | `/api/document-ir/jobs/{run_id}/artifacts` | [ExtractESG/document/src/esg_document/api/document_archive.py](../../../ExtractESG/document/src/esg_document/api/document_archive.py) @266 |
| GET | `/api/console/monitor` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @20 |
| GET | `/api/report-sessions/{key}/products` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @24 |
| POST | `/api/report-sessions/{key}/archive` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @29 |
| POST | `/api/report-sessions/{key}/restore` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @33 |
| GET | `/api/report-sessions/{key}/candidates` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @37 |
| POST | `/api/report-sessions/preview` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @44 |
| GET | `/api/report-sessions` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @54 |
| POST | `/api/report-sessions` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @49 |
| GET | `/api/report-sessions/{key}` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @58 |
| POST | `/api/report-sessions/{key}/cancel` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @62 |
| GET | `/api/report-sessions/{key}/facts` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @66 |
| GET | `/api/pipeline/clauses` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @73 |
| GET | `/api/pipeline/clauses/{key}` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @77 |
| GET | `/api/pipeline/clauses/{key}/organization` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @81 |
| GET | `/api/pipeline/organization-contract` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @85 |
| GET | `/api/report-sessions/{key}/organized` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @89 |
| GET | `/api/pipeline/clause-groups` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @100 |
| GET | `/api/pipeline/clause-selections` | [ConsoleESG/backend/src/esg_console/sessions.py](../../../ConsoleESG/backend/src/esg_console/sessions.py) @104 |

## 18180 — Clause extraction backbone（27路径/29操作）

| 方法 | 有效路径 | 定位文件与行 |
|---|---|---|
| GET | `/api/compatibility` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @115 |
| GET | `/health` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @126 |
| GET | `/api/engine` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @140 |
| GET | `/api/organization-contract` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @148 |
| GET | `/api/clauses` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @157 |
| GET | `/api/clause-contract` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @167 |
| POST | `/api/clauses/template` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @171 |
| POST | `/api/clauses/validate` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @179 |
| GET | `/api/clause-groups` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @187 |
| GET | `/api/clause-selections` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @196 |
| GET | `/api/clauses/{key}` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @200 |
| PUT | `/api/clauses/{key}` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @229 |
| GET | `/api/clauses/{key}/organization` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @204 |
| GET | `/api/clauses/{key}/reading-card` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @218 |
| POST | `/api/clause-selection-options` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @225 |
| POST | `/api/targets/freeze` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @241 |
| POST | `/api/runs/preview` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @247 |
| GET | `/api/runs` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @298 |
| POST | `/api/runs` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @262 |
| GET | `/api/runs/{run_id}` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @308 |
| GET | `/api/runs/{run_id}/targets` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @312 |
| POST | `/api/runs/{run_id}/cancel` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @318 |
| POST | `/api/runs/{run_id}/execution-terminal` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @330 |
| GET | `/api/runs/{run_id}/facts/{fact_id}` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @340 |
| GET | `/api/runs/{run_id}/facts` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @355 |
| GET | `/api/runs/{run_id}/organized` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @373 |
| GET | `/api/runs/{run_id}/candidates` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @394 |
| POST | `/api/runs/{run_id}/exports` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @403 |
| GET | `/api/runs/{run_id}/exports/{export_id}` | [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) @412 |

## 18280 — Independent extraction ingestion（30路径/32操作）

| 方法 | 有效路径 | 定位文件与行 |
|---|---|---|
| GET | `/health` | [database/src/esg_database/api.py](../../../database/src/esg_database/api.py) @41 |
| POST | `/v1/ingestion/exports` | [database/src/esg_database/api.py](../../../database/src/esg_database/api.py) @51 |
| GET | `/v1/ingestion/receipts/{export_id}` | [database/src/esg_database/api.py](../../../database/src/esg_database/api.py) @70 |
| GET | `/v1/ingestion/exports/{export_id}/facts` | [database/src/esg_database/api.py](../../../database/src/esg_database/api.py) @80 |
| GET | `/v1/research-contract` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @35 |
| GET | `/v1/ingestion/exports/{export_id}/projection` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @39 |
| POST | `/v1/ingestion/exports/{export_id}/projection` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @47 |
| GET | `/v1/ingestion/exports/{export_id}/products` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @43 |
| GET | `/v1/projections` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @55 |
| GET | `/v1/projections/{projection_id}/observations` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @59 |
| GET | `/v1/projections/{projection_id}/definitions` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @65 |
| GET | `/v1/projections/{projection_id}/export` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @69 |
| POST | `/v1/datasets/preview` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @74 |
| POST | `/v1/datasets/preview/observations` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @82 |
| POST | `/v1/datasets` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @88 |
| GET | `/v1/datasets` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @96 |
| GET | `/v1/datasets/{dataset_version_id}` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @100 |
| GET | `/v1/datasets/{dataset_version_id}/observations` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @104 |
| GET | `/v1/datasets/{dataset_version_id}/export` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @109 |
| POST | `/v1/datasets/{dataset_version_id}/withdraw` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @114 |
| POST | `/v1/restatements` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @118 |
| GET | `/v2/datasets` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @126 |
| GET | `/v2/sources/{pdf_sha256}` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @130 |
| GET | `/v2/datasets/{dataset_version_id}` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @134 |
| GET | `/v2/datasets/{dataset_version_id}/observations` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @138 |
| GET | `/v2/datasets/{dataset_version_id}/definitions` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @145 |
| GET | `/v2/datasets/{dataset_version_id}/observations/{observation_id}` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @149 |
| GET | `/v2/datasets/{dataset_version_id}/sources` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @153 |
| GET | `/v2/datasets/{dataset_version_id}/export` | [database/src/esg_database/products_api.py](../../../database/src/esg_database/products_api.py) @157 |
| POST | `/v1/sources/lifecycle/references` | [database/src/esg_database/sources_api.py](../../../database/src/esg_database/sources_api.py) @9 |
| POST | `/v1/sources/lifecycle/reserve` | [database/src/esg_database/sources_api.py](../../../database/src/esg_database/sources_api.py) @11 |
| POST | `/v1/sources/lifecycle/reservations/{reservation_id}` | [database/src/esg_database/sources_api.py](../../../database/src/esg_database/sources_api.py) @14 |

## 8765 — PlatESG 已发布数据（10路径/10操作）

| 方法 | 有效路径 | 定位文件与行 |
|---|---|---|
| GET | `/v2/datasets` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @18 |
| GET | `/v2/datasets/{id}` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @22 |
| GET | `/v2/datasets/{id}/observations` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @25 |
| GET | `/v2/datasets/{id}/definitions` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @30 |
| GET | `/v2/datasets/{id}/observations/{observation_id}` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @33 |
| GET | `/v2/datasets/{id}/sources` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @36 |
| GET | `/v2/datasets/{id}/export` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @39 |
| GET | `/v2/sources/{pdf_sha256}/pdf` | [PlatESG/backend/products.py](../../../PlatESG/backend/products.py) @46 |
| GET | `/health` | [PlatESG/backend/main.py](../../../PlatESG/backend/main.py) @34 |
| GET | `/health/compatibility` | [PlatESG/backend/main.py](../../../PlatESG/backend/main.py) @41 |
