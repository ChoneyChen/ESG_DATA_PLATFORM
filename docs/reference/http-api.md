# HTTP接口参考

由`ExtractESG/.local/python/bin/python tools/build_documentation.py`从当前注册源码生成；接口设计规则见[合同规范](../04-contracts.md)。

列出装饰器、显式add_api_route及字面量循环注册，合并APIRouter前缀。参数保留Python注解和默认值；模型字段以所链接源码及服务运行时`/openapi.json`为准。这里是源码声明，不能当作服务已启动或接口已实测。

Console静态服务的同源代理`/document`、`/extraction`、`/database`见[前端设计](../07-frontends.md)；它们不属于下面的FastAPI业务端口。Plat的旧路径410响应仍列出，用于明确退役边界。

## Plat · 8765

| 方法与路径 | 参数（含请求模型） | 返回模型 | 实现 |
|---|---|---|---|
| `GET /health` | `` | `见实现` | [health](../../PlatESG/backend/main.py#L34) |
| `GET /health/compatibility` | `` | `见实现` | [compatibility](../../PlatESG/backend/main.py#L41) |
| `DELETE /internal/v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L48) |
| `GET /internal/v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L48) |
| `PATCH /internal/v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L48) |
| `POST /internal/v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L48) |
| `PUT /internal/v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L48) |
| `GET /review` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L50) |
| `POST /review` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L50) |
| `GET /review/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L49) |
| `POST /review/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L49) |
| `DELETE /v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L47) |
| `GET /v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L47) |
| `PATCH /v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L47) |
| `POST /v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L47) |
| `PUT /v1/{rest:path}` | `rest=''` | `见实现` | [retired](../../PlatESG/backend/main.py#L47) |
| `GET /v2/datasets` | `limit: int=Query(100, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096)` | `DatasetPage` | [datasets](../../PlatESG/backend/products.py#L18) |
| `GET /v2/datasets/{id}` | `id: str` | `DatasetVersion` | [dataset](../../PlatESG/backend/products.py#L22) |
| `GET /v2/datasets/{id}/definitions` | `id: str` | `list[ProductDefinition]` | [definitions](../../PlatESG/backend/products.py#L30) |
| `GET /v2/datasets/{id}/export` | `id: str, format: str=Query('csv', pattern='^(csv&#124;parquet)$'), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160)` | `见实现` | [export](../../PlatESG/backend/products.py#L39) |
| `GET /v2/datasets/{id}/observations` | `id: str, limit: int=Query(100, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160), relation: str &#124; None=Query(None, pattern='^(match&#124;uncertain&#124;different)$')` | `ObservationPage` | [observations](../../PlatESG/backend/products.py#L25) |
| `GET /v2/datasets/{id}/observations/{observation_id}` | `id: str, observation_id: str` | `Observation` | [observation](../../PlatESG/backend/products.py#L33) |
| `GET /v2/datasets/{id}/sources` | `id: str` | `list[ObservationSource]` | [sources](../../PlatESG/backend/products.py#L36) |
| `GET /v2/sources/{pdf_sha256}/pdf` | `pdf_sha256: str=Path(pattern='^[a-f0-9]{64}$')` | `见实现` | [pdf](../../PlatESG/backend/products.py#L46) |

## 数据库 · 18280

| 方法与路径 | 参数（含请求模型） | 返回模型 | 实现 |
|---|---|---|---|
| `GET /health` | `` | `见实现` | [health](../../database/src/esg_database/api.py#L41) |
| `GET /v1/datasets` | `limit: int=Query(100, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096), export_id: str &#124; None=Query(None, max_length=160)` | `DatasetPage` | [internal_datasets](../../database/src/esg_database/products_api.py#L109) |
| `POST /v1/datasets` | `body: PublishRequest, http: Request` | `DatasetVersion` | [publish](../../database/src/esg_database/products_api.py#L101) |
| `POST /v1/datasets/preview` | `body: DatasetRequest, http: Request` | `DatasetPreview` | [preview](../../database/src/esg_database/products_api.py#L87) |
| `POST /v1/datasets/preview/observations` | `body: PreviewQuery, http: Request, limit: int=Query(200, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096)` | `ObservationPage` | [preview_rows](../../database/src/esg_database/products_api.py#L95) |
| `GET /v1/datasets/{dataset_version_id}` | `dataset_version_id: str` | `DatasetVersion` | [internal_dataset](../../database/src/esg_database/products_api.py#L113) |
| `GET /v1/datasets/{dataset_version_id}/export` | `dataset_version_id: str, format: str=Query('csv', pattern='^(csv&#124;parquet)$'), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160)` | `见实现` | [export_internal_dataset](../../database/src/esg_database/products_api.py#L122) |
| `GET /v1/datasets/{dataset_version_id}/observations` | `dataset_version_id: str, limit: int=Query(200, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160)` | `ObservationPage` | [internal_observations](../../database/src/esg_database/products_api.py#L117) |
| `POST /v1/datasets/{dataset_version_id}/withdraw` | `dataset_version_id: str, body: WithdrawalRequest, http: Request` | `DatasetVersion` | [withdraw](../../database/src/esg_database/products_api.py#L127) |
| `POST /v1/ingestion/exports` | `request: Request` | `IngestionReceipt` | [ingest](../../database/src/esg_database/api.py#L51) |
| `GET /v1/ingestion/exports/{export_id}/facts` | `export_id: str, limit: int=Query(200, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=2048), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160), relation: str &#124; None=Query(None, pattern='^(match&#124;different&#124;uncertain)$')` | `CaptureFactPage` | [facts](../../database/src/esg_database/api.py#L80) |
| `GET /v1/ingestion/exports/{export_id}/products` | `export_id: str` | `CaptureProductState` | [capture_products](../../database/src/esg_database/products_api.py#L43) |
| `GET /v1/ingestion/exports/{export_id}/projection` | `export_id: str` | `ProjectionStatus` | [projection](../../database/src/esg_database/products_api.py#L39) |
| `POST /v1/ingestion/exports/{export_id}/projection` | `export_id: str, http: Request` | `ProjectionStatus` | [retry_projection](../../database/src/esg_database/products_api.py#L47) |
| `GET /v1/ingestion/receipts/{export_id}` | `export_id: str` | `IngestionReceipt` | [receipt](../../database/src/esg_database/api.py#L70) |
| `GET /v1/projections` | `limit: int=Query(100, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096), archived: bool=False` | `ProjectionPage` | [projections](../../database/src/esg_database/products_api.py#L55) |
| `POST /v1/projections/archive` | `body: ProjectionSelection, http: Request` | `ProjectionCleanupResult` | [archive_projections](../../database/src/esg_database/products_api.py#L59) |
| `POST /v1/projections/purge` | `body: ProjectionSelection, http: Request` | `ProjectionPurgeResult` | [purge_projections](../../database/src/esg_database/products_api.py#L67) |
| `POST /v1/projections/restore` | `body: ProjectionSelection, http: Request` | `ProjectionCleanupResult` | [restore_projections](../../database/src/esg_database/products_api.py#L63) |
| `GET /v1/projections/{projection_id}/definitions` | `projection_id: str` | `list[ProductDefinition]` | [projection_definitions](../../database/src/esg_database/products_api.py#L78) |
| `GET /v1/projections/{projection_id}/export` | `projection_id: str, format: str=Query('csv', pattern='^(csv&#124;parquet)$'), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160)` | `见实现` | [export_projection](../../database/src/esg_database/products_api.py#L82) |
| `GET /v1/projections/{projection_id}/observations` | `projection_id: str, limit: int=Query(200, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160), relation: str &#124; None=Query(None, pattern='^(match&#124;uncertain&#124;different)$')` | `ObservationPage` | [projection_observations](../../database/src/esg_database/products_api.py#L72) |
| `GET /v1/research-contract` | `` | `见实现` | [research_schema](../../database/src/esg_database/products_api.py#L35) |
| `POST /v1/restatements` | `body: RestatementRequest, http: Request` | `见实现` | [restatement](../../database/src/esg_database/products_api.py#L131) |
| `POST /v1/sources/lifecycle/references` | `body: SourceScope` | `SourceReferences` | [references](../../database/src/esg_database/sources_api.py#L9) |
| `POST /v1/sources/lifecycle/reservations/{reservation_id}` | `reservation_id: str, body: UpdateSourceReservation, request: Request` | `见实现` | [update](../../database/src/esg_database/sources_api.py#L14) |
| `POST /v1/sources/lifecycle/reserve` | `body: ReserveSource, request: Request` | `见实现` | [reserve](../../database/src/esg_database/sources_api.py#L11) |
| `GET /v2/datasets` | `limit: int=Query(100, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096)` | `DatasetPage` | [public_datasets](../../database/src/esg_database/products_api.py#L139) |
| `GET /v2/datasets/{dataset_version_id}` | `dataset_version_id: str` | `DatasetVersion` | [public_dataset](../../database/src/esg_database/products_api.py#L147) |
| `GET /v2/datasets/{dataset_version_id}/definitions` | `dataset_version_id: str` | `list[ProductDefinition]` | [dataset_definitions](../../database/src/esg_database/products_api.py#L158) |
| `GET /v2/datasets/{dataset_version_id}/export` | `dataset_version_id: str, format: str=Query('csv', pattern='^(csv&#124;parquet)$'), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160)` | `见实现` | [export_dataset](../../database/src/esg_database/products_api.py#L170) |
| `GET /v2/datasets/{dataset_version_id}/observations` | `dataset_version_id: str, limit: int=Query(200, ge=1, le=1000), cursor: str &#124; None=Query(None, max_length=4096), period: str &#124; None=Query(None, max_length=160), clause_key: str &#124; None=Query(None, max_length=160), relation: str &#124; None=Query(None, pattern='^(match&#124;uncertain&#124;different)$')` | `ObservationPage` | [public_observations](../../database/src/esg_database/products_api.py#L151) |
| `GET /v2/datasets/{dataset_version_id}/observations/{observation_id}` | `dataset_version_id: str, observation_id: str` | `Observation` | [observation](../../database/src/esg_database/products_api.py#L162) |
| `GET /v2/datasets/{dataset_version_id}/sources` | `dataset_version_id: str` | `list[ObservationSource]` | [sources](../../database/src/esg_database/products_api.py#L166) |
| `GET /v2/sources/{pdf_sha256}` | `pdf_sha256: str=Path(pattern='^[a-f0-9]{64}$')` | `ObservationSource` | [source](../../database/src/esg_database/products_api.py#L143) |

## 文档与Console组合 · 18080

| 方法与路径 | 参数（含请求模型） | 返回模型 | 实现 |
|---|---|---|---|
| `GET /api/console/monitor` | `` | `MonitorSnapshot` | [monitor](../../ConsoleESG/backend/src/esg_console/sessions.py#L26) |
| `GET /api/document-ir/documents` | `self` | `list[dict[str, object]]` | [self.list_document_ir_documents](../../ExtractESG/document/src/esg_document/api/document_archive.py#L546) |
| `GET /api/document-ir/documents/{document_id}/revisions` | `self, document_id: str` | `list[dict[str, object]]` | [self.list_document_revisions](../../ExtractESG/document/src/esg_document/api/document_archive.py#L551) |
| `GET /api/document-ir/jobs` | `self` | `list[DocumentIrJobState]` | [self.list_document_ir_jobs](../../ExtractESG/document/src/esg_document/api/document_archive.py#L410) |
| `POST /api/document-ir/jobs` | `request: DocumentIrBuildRequest=Body(...)` | `DocumentIrJobState` | [create_document_ir_job](../../ExtractESG/document/src/esg_document/api/main.py#L1202) |
| `DELETE /api/document-ir/jobs/{run_id}` | `run_id: str, request: LibraryDeleteRequest, authorization: str &#124; None=Header(default=None)` | `见实现` | [delete_ir](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L102) |
| `GET /api/document-ir/jobs/{run_id}` | `self, run_id: str` | `DocumentIrJobState` | [self.get_document_ir_job](../../ExtractESG/document/src/esg_document/api/document_archive.py#L418) |
| `GET /api/document-ir/jobs/{run_id}/artifact-index` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_artifact_index](../../ExtractESG/document/src/esg_document/api/document_archive.py#L536) |
| `GET /api/document-ir/jobs/{run_id}/artifacts` | `self, run_id: str` | `dict[str, object]` | [self.list_document_ir_artifacts](../../ExtractESG/document/src/esg_document/api/document_archive.py#L566) |
| `GET /api/document-ir/jobs/{run_id}/atomic-patches` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_atomic_patches](../../ExtractESG/document/src/esg_document/api/document_archive.py#L496) |
| `GET /api/document-ir/jobs/{run_id}/candidate-revisions` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_candidate_revisions](../../ExtractESG/document/src/esg_document/api/document_archive.py#L521) |
| `GET /api/document-ir/jobs/{run_id}/conflict-groups` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_conflict_groups](../../ExtractESG/document/src/esg_document/api/document_archive.py#L531) |
| `GET /api/document-ir/jobs/{run_id}/correction-patches` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_correction_patches](../../ExtractESG/document/src/esg_document/api/document_archive.py#L481) |
| `GET /api/document-ir/jobs/{run_id}/deletion` | `run_id: str, authorization: str &#124; None=Header(default=None)` | `见实现` | [ir_description](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L97) |
| `GET /api/document-ir/jobs/{run_id}/document` | `self, run_id: str` | `dict[str, object]` | [self.get_document_ir_document](../../ExtractESG/document/src/esg_document/api/document_archive.py#L436) |
| `GET /api/document-ir/jobs/{run_id}/figures` | `self, run_id: str` | `list[dict[str, object]]` | [self.list_document_ir_figures](../../ExtractESG/document/src/esg_document/api/document_archive.py#L471) |
| `GET /api/document-ir/jobs/{run_id}/final-decisions` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_final_decisions](../../ExtractESG/document/src/esg_document/api/document_archive.py#L516) |
| `GET /api/document-ir/jobs/{run_id}/guard-results` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_guard_results](../../ExtractESG/document/src/esg_document/api/document_archive.py#L506) |
| `GET /api/document-ir/jobs/{run_id}/human-review/inbox` | `self, run_id: str` | `dict[str, object]` | [self.get_document_ir_human_review_inbox](../../ExtractESG/document/src/esg_document/api/document_archive.py#L561) |
| `GET /api/document-ir/jobs/{run_id}/logical-tables` | `self, run_id: str` | `list[dict[str, object]]` | [self.list_document_ir_logical_tables](../../ExtractESG/document/src/esg_document/api/document_archive.py#L461) |
| `GET /api/document-ir/jobs/{run_id}/logical-tables/{logical_table_id}` | `self, run_id: str, logical_table_id: str` | `dict[str, object]` | [self.get_document_ir_logical_table](../../ExtractESG/document/src/esg_document/api/document_archive.py#L466) |
| `GET /api/document-ir/jobs/{run_id}/manifest` | `self, run_id: str` | `dict[str, object]` | [self.get_document_ir_manifest](../../ExtractESG/document/src/esg_document/api/document_archive.py#L421) |
| `GET /api/document-ir/jobs/{run_id}/model-calls` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_model_calls](../../ExtractESG/document/src/esg_document/api/document_archive.py#L486) |
| `GET /api/document-ir/jobs/{run_id}/pages` | `self, run_id: str` | `list[dict[str, object]]` | [self.list_document_ir_pages](../../ExtractESG/document/src/esg_document/api/document_archive.py#L441) |
| `GET /api/document-ir/jobs/{run_id}/pages/{page_index}` | `self, run_id: str, page_index: int` | `dict[str, object]` | [self.get_document_ir_page](../../ExtractESG/document/src/esg_document/api/document_archive.py#L446) |
| `GET /api/document-ir/jobs/{run_id}/patch-transactions` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_patch_transactions](../../ExtractESG/document/src/esg_document/api/document_archive.py#L501) |
| `POST /api/document-ir/jobs/{run_id}/patches/{patch_id}/decision` | `run_id: str, patch_id: str, request: PatchDecisionRequest=Body(...)` | `dict[str, object]` | [decide_document_ir_patch](../../ExtractESG/document/src/esg_document/api/main.py#L1214) |
| `GET /api/document-ir/jobs/{run_id}/quality-report` | `self, run_id: str` | `dict[str, object]` | [self.get_document_ir_quality_report](../../ExtractESG/document/src/esg_document/api/document_archive.py#L426) |
| `POST /api/document-ir/jobs/{run_id}/repair` | `run_id: str, request: DocumentIrRepairRequest=Body(...)` | `DocumentIrJobState` | [repair_document_ir](../../ExtractESG/document/src/esg_document/api/main.py#L1246) |
| `GET /api/document-ir/jobs/{run_id}/review-tasks` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_review_tasks](../../ExtractESG/document/src/esg_document/api/document_archive.py#L556) |
| `POST /api/document-ir/jobs/{run_id}/review-tasks/{task_id}/decision` | `run_id: str, task_id: str, request: PatchDecisionRequest=Body(...)` | `dict[str, object]` | [decide_document_ir_review_task](../../ExtractESG/document/src/esg_document/api/main.py#L1230) |
| `GET /api/document-ir/jobs/{run_id}/reviewer-results` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_reviewer_results](../../ExtractESG/document/src/esg_document/api/document_archive.py#L491) |
| `POST /api/document-ir/jobs/{run_id}/reviews/retry` | `run_id: str, request: ReviewRetryRequest=Body(...)` | `DocumentIrJobState` | [retry_document_ir_reviews](../../ExtractESG/document/src/esg_document/api/main.py#L1264) |
| `GET /api/document-ir/jobs/{run_id}/reviews/{task_id}` | `self, run_id: str, task_id: str` | `dict[str, object]` | [self.get_document_ir_review_bundle](../../ExtractESG/document/src/esg_document/api/document_archive.py#L526) |
| `GET /api/document-ir/jobs/{run_id}/structure-edges` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_structure_edges](../../ExtractESG/document/src/esg_document/api/document_archive.py#L476) |
| `GET /api/document-ir/jobs/{run_id}/tables` | `self, run_id: str` | `list[dict[str, object]]` | [self.list_document_ir_tables](../../ExtractESG/document/src/esg_document/api/document_archive.py#L451) |
| `GET /api/document-ir/jobs/{run_id}/tables/{table_id}` | `self, run_id: str, table_id: str` | `dict[str, object]` | [self.get_document_ir_table](../../ExtractESG/document/src/esg_document/api/document_archive.py#L456) |
| `GET /api/document-ir/jobs/{run_id}/validation-report` | `self, run_id: str` | `dict[str, object]` | [self.get_document_ir_validation_report](../../ExtractESG/document/src/esg_document/api/document_archive.py#L431) |
| `GET /api/document-ir/jobs/{run_id}/verifier-results` | `self, run_id: str` | `list[dict[str, object]]` | [self.get_document_ir_verifier_results](../../ExtractESG/document/src/esg_document/api/document_archive.py#L511) |
| `GET /api/document-ir/review-worklist` | `self` | `dict[str, object]` | [self.list_document_ir_review_worklist](../../ExtractESG/document/src/esg_document/api/document_archive.py#L413) |
| `GET /api/document-ir/revisions/{ocr_run_id}` | `self, ocr_run_id: str` | `list[dict[str, object]]` | [self.list_document_ir_revisions](../../ExtractESG/document/src/esg_document/api/document_archive.py#L541) |
| `GET /api/evidence/{ir_run_id}` | `ir_run_id: str` | `DocumentReference` | [document](../../ExtractESG/document/src/esg_document/api/evidence.py#L32) |
| `POST /api/evidence/{ir_run_id}/locate-pages` | `ir_run_id: str, request: PageLocationRequest` | `见实现` | [locate_pages](../../ExtractESG/document/src/esg_document/api/evidence.py#L44) |
| `POST /api/evidence/{ir_run_id}/read` | `ir_run_id: str, request: ReadRequest` | `EvidenceFrame` | [read](../../ExtractESG/document/src/esg_document/api/evidence.py#L40) |
| `POST /api/evidence/{ir_run_id}/search` | `ir_run_id: str, request: SearchIntent` | `ObjectSearchPage` | [search](../../ExtractESG/document/src/esg_document/api/evidence.py#L36) |
| `GET /api/models/status` | `provider: Literal['qiniu', 'local_nuextract', 'local_qwen'] &#124; None=None` | `dict[str, object]` | [get_model_status](../../ExtractESG/document/src/esg_document/api/main.py#L912) |
| `GET /api/ocr-library` | `authorization: str &#124; None=Header(default=None)` | `见实现` | [library](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L79) |
| `DELETE /api/ocr-library/{run_id}` | `run_id: str, request: LibraryDeleteRequest, authorization: str &#124; None=Header(default=None)` | `见实现` | [delete_ocr](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L88) |
| `GET /api/ocr-library/{run_id}` | `run_id: str, authorization: str &#124; None=Header(default=None)` | `见实现` | [description](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L83) |
| `GET /api/ocr/jobs` | `` | `list[OcrJobState]` | [list_ocr_jobs](../../ExtractESG/document/src/esg_document/api/main.py#L1120) |
| `POST /api/ocr/jobs` | `file: Annotated[UploadFile &#124; None, File()]=None, file_url: Annotated[str &#124; None, Form()]=None, token: Annotated[str &#124; None, Form()]=None, model: Annotated[str, Form()]=settings.paddle_model, ocr_provider: Annotated[str, Form()]=settings.default_ocr_provider, allow_api_fallback: Annotated[bool, Form()]=False, useDocOrientationClassify: Annotated[bool, Form()]=False, useDocUnwarping: Annotated[bool, Form()]=False, useChartRecognition: Annotated[bool, Form()]=False, poll_interval_seconds: Annotated[float, Form()]=settings.poll_interval_seconds, page_selection: Annotated[str, Form()]='all', page_indices: Annotated[str &#124; None, Form(description='Comma-separated original 0-based PDF page indices')]=None` | `OcrJobState` | [create_ocr_job](../../ExtractESG/document/src/esg_document/api/main.py#L989) |
| `GET /api/ocr/jobs/{run_id}` | `run_id: str` | `OcrJobState` | [get_ocr_job](../../ExtractESG/document/src/esg_document/api/main.py#L1146) |
| `GET /api/ocr/jobs/{run_id}/artifacts` | `run_id: str` | `dict[str, object]` | [list_ocr_artifacts](../../ExtractESG/document/src/esg_document/api/main.py#L1179) |
| `GET /api/ocr/jobs/{run_id}/manifest` | `run_id: str` | `dict[str, object]` | [get_ocr_manifest](../../ExtractESG/document/src/esg_document/api/main.py#L1166) |
| `GET /api/ocr/providers/status` | `` | `dict[str, object]` | [get_ocr_provider_status](../../ExtractESG/document/src/esg_document/api/main.py#L1115) |
| `GET /api/operations/tasks` | `` | `list[TaskObservation]` | [operation_tasks](../../ExtractESG/document/src/esg_document/api/main.py#L1416) |
| `GET /api/operations/tasks/{task_id}` | `task_id: str` | `TaskObservation` | [operation_task](../../ExtractESG/document/src/esg_document/api/main.py#L1424) |
| `GET /api/pdf-library` | `` | `见实现` | [pdf_library](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L55) |
| `GET /api/pipeline/clause-groups` | `` | `见实现` | [groups](../../ConsoleESG/backend/src/esg_console/sessions.py#L135) |
| `GET /api/pipeline/clause-selections` | `` | `见实现` | [selections](../../ConsoleESG/backend/src/esg_console/sessions.py#L139) |
| `GET /api/pipeline/clauses` | `` | `见实现` | [clauses](../../ConsoleESG/backend/src/esg_console/sessions.py#L110) |
| `GET /api/pipeline/clauses/{key}` | `key: str` | `见实现` | [clause](../../ConsoleESG/backend/src/esg_console/sessions.py#L114) |
| `GET /api/pipeline/clauses/{key}/organization` | `key: str` | `见实现` | [organization](../../ConsoleESG/backend/src/esg_console/sessions.py#L118) |
| `GET /api/pipeline/events/stream` | `request: Request, after: int=Query(0, ge=0), task_id: str &#124; None=None, latest: bool=Query(False)` | `见实现` | [event_stream](../../ExtractESG/pipeline/src/esg_pipeline/execution_api.py#L51) |
| `GET /api/pipeline/history` | `limit: int=Query(300, ge=1, le=1000)` | `见实现` | [history](../../ExtractESG/pipeline/src/esg_pipeline/execution_api.py#L19) |
| `GET /api/pipeline/organization-contract` | `` | `见实现` | [organization_contract](../../ConsoleESG/backend/src/esg_console/sessions.py#L122) |
| `GET /api/pipeline/status` | `` | `见实现` | [current_pipeline_status](../../ExtractESG/document/src/esg_document/api/main.py#L1403) |
| `GET /api/pipeline/tasks` | `limit: int=Query(300, ge=1, le=1000), include_archived: bool=False` | `list[PipelineTask]` | [list_pipeline_tasks](../../ExtractESG/document/src/esg_document/api/main.py#L378) |
| `POST /api/pipeline/tasks/document-ir` | `request: DocumentIrPipelineTaskRequest=Body(...)` | `PipelineTask` | [queue_document_ir_task](../../ExtractESG/document/src/esg_document/api/main.py#L766) |
| `POST /api/pipeline/tasks/native/{native_id}/cancel` | `native_id: str` | `dict[str, object]` | [cancel_native_pipeline_task](../../ExtractESG/document/src/esg_document/api/main.py#L453) |
| `POST /api/pipeline/tasks/ocr` | `request: OcrPipelineTaskRequest=Body(...)` | `PipelineTask` | [queue_ocr_task](../../ExtractESG/document/src/esg_document/api/main.py#L690) |
| `PUT /api/pipeline/tasks/order` | `request: PipelineOrderRequest=Body(...)` | `list[PipelineTask]` | [reorder_pipeline_tasks](../../ExtractESG/document/src/esg_document/api/main.py#L423) |
| `GET /api/pipeline/tasks/{task_id}` | `task_id: str` | `PipelineTask` | [get_pipeline_task](../../ExtractESG/document/src/esg_document/api/main.py#L403) |
| `POST /api/pipeline/tasks/{task_id}/archive` | `task_id: str, request: HistoryCommand` | `见实现` | [archive_task](../../ExtractESG/pipeline/src/esg_pipeline/execution_api.py#L43) |
| `POST /api/pipeline/tasks/{task_id}/cancel` | `task_id: str` | `PipelineTask` | [cancel_pipeline_task](../../ExtractESG/document/src/esg_document/api/main.py#L439) |
| `GET /api/pipeline/tasks/{task_id}/events` | `task_id: str, after: int=Query(0, ge=0), limit: int=Query(1000, ge=1, le=2000)` | `list[dict[str, object]]` | [get_pipeline_task_events](../../ExtractESG/document/src/esg_document/api/main.py#L411) |
| `POST /api/pipeline/tasks/{task_id}/restore` | `task_id: str, request: HistoryCommand` | `见实现` | [restore_task](../../ExtractESG/pipeline/src/esg_pipeline/execution_api.py#L47) |
| `GET /api/processing-status` | `` | `见实现` | [status](../../ExtractESG/document/src/esg_document/api/processing_status.py#L31) |
| `GET /api/report-assets` | `` | `dict[str, object]` | [list_report_assets](../../ExtractESG/document/src/esg_document/api/main.py#L570) |
| `POST /api/report-assets` | `file: Annotated[UploadFile, File(...)]` | `dict[str, object]` | [upload_report_asset](../../ExtractESG/document/src/esg_document/api/main.py#L605) |
| `GET /api/report-assets-history` | `` | `见实现` | [report_asset_history](../../ExtractESG/document/src/esg_document/api/main.py#L678) |
| `DELETE /api/report-assets/{asset_id}` | `asset_id: str, request: PdfDeleteRequest, authorization: str &#124; None=Header(default=None)` | `见实现` | [delete_pdf](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L118) |
| `POST /api/report-assets/{asset_id}/archive` | `见注册函数` | `见实现` | [action_endpoint(action)](../../ExtractESG/document/src/esg_document/api/main.py#L667) |
| `GET /api/report-assets/{asset_id}/deletion` | `asset_id: str, authorization: str &#124; None=Header(default=None)` | `见实现` | [pdf_description](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L111) |
| `POST /api/report-assets/{asset_id}/discard` | `见注册函数` | `见实现` | [action_endpoint(action)](../../ExtractESG/document/src/esg_document/api/main.py#L667) |
| `GET /api/report-assets/{asset_id}/file` | `asset_id: str` | `FileResponse` | [get_report_asset_file](../../ExtractESG/document/src/esg_document/api/main.py#L619) |
| `GET /api/report-assets/{asset_id}/lifecycle` | `asset_id: str` | `见实现` | [get_report_asset_lifecycle](../../ExtractESG/document/src/esg_document/api/main.py#L648) |
| `POST /api/report-assets/{asset_id}/restore` | `见注册函数` | `见实现` | [action_endpoint(action)](../../ExtractESG/document/src/esg_document/api/main.py#L667) |
| `GET /api/report-library` | `` | `见实现` | [report_library](../../ExtractESG/document/src/esg_document/api/library_deletion.py#L59) |
| `GET /api/report-processing/runs` | `include_archived: bool=False` | `见实现` | [runs](../../ExtractESG/document/src/esg_document/api/report_processing.py#L77) |
| `POST /api/report-processing/runs` | `request: ProcessingRequest` | `见实现` | [create](../../ExtractESG/document/src/esg_document/api/report_processing.py#L84) |
| `GET /api/report-processing/runs/{processing_id}` | `processing_id: str` | `见实现` | [run](../../ExtractESG/document/src/esg_document/api/report_processing.py#L89) |
| `POST /api/report-processing/runs/{processing_id}/archive` | `见注册函数` | `见实现` | [endpoint(action)](../../ExtractESG/document/src/esg_document/storage/processing_lifecycle.py#L409) |
| `POST /api/report-processing/runs/{processing_id}/cancel` | `processing_id: str, request: ProcessingActionRequest &#124; None=Body(default=None)` | `见实现` | [cancel](../../ExtractESG/document/src/esg_document/api/report_processing.py#L113) |
| `POST /api/report-processing/runs/{processing_id}/discard` | `见注册函数` | `见实现` | [endpoint(action)](../../ExtractESG/document/src/esg_document/storage/processing_lifecycle.py#L409) |
| `GET /api/report-processing/runs/{processing_id}/events` | `processing_id: str, after: int=Query(default=0, ge=0)` | `见实现` | [events](../../ExtractESG/document/src/esg_document/api/report_processing.py#L93) |
| `POST /api/report-processing/runs/{processing_id}/evidence` | `processing_id: str, request: EvidenceTransitionRequest` | `见实现` | [transition](../../ExtractESG/document/src/esg_document/api/report_processing.py#L130) |
| `GET /api/report-processing/runs/{processing_id}/evidence-versions` | `processing_id: str` | `见实现` | [versions](../../ExtractESG/document/src/esg_document/api/report_processing.py#L136) |
| `GET /api/report-processing/runs/{processing_id}/lifecycle` | `processing_id: str` | `见实现` | [description](../../ExtractESG/document/src/esg_document/storage/processing_lifecycle.py#L394) |
| `GET /api/report-processing/runs/{processing_id}/pages` | `processing_id: str` | `见实现` | [pages](../../ExtractESG/document/src/esg_document/api/report_processing.py#L105) |
| `GET /api/report-processing/runs/{processing_id}/pages/{page_index}` | `processing_id: str, page_index: int` | `见实现` | [page](../../ExtractESG/document/src/esg_document/api/report_processing.py#L109) |
| `POST /api/report-processing/runs/{processing_id}/restore` | `见注册函数` | `见实现` | [endpoint(action)](../../ExtractESG/document/src/esg_document/storage/processing_lifecycle.py#L409) |
| `POST /api/report-processing/runs/{processing_id}/resume` | `processing_id: str, request: ProcessingActionRequest &#124; None=Body(default=None)` | `见实现` | [resume](../../ExtractESG/document/src/esg_document/api/report_processing.py#L121) |
| `GET /api/report-resources/folders` | `` | `ReportFolderList` | [folders](../../ExtractESG/document/src/esg_document/api/report_resources.py#L92) |
| `GET /api/report-resources/folders/{folder_id}` | `folder_id: str` | `ReportFolderSummary` | [folder](../../ExtractESG/document/src/esg_document/api/report_resources.py#L96) |
| `POST /api/report-resources/imports` | `request: Import, http: Request` | `见实现` | [intake](../../ExtractESG/document/src/esg_document/api/report_resources.py#L82) |
| `POST /api/report-resources/uploads` | `http: Request, file: UploadFile=File(...), metadata: str=Form(...)` | `见实现` | [upload](../../ExtractESG/document/src/esg_document/api/report_resources.py#L103) |
| `GET /api/report-resources/{pdf_id}` | `pdf_id: str` | `见实现` | [get](../../ExtractESG/document/src/esg_document/api/report_resources.py#L116) |
| `PUT /api/report-resources/{pdf_id}` | `pdf_id: str, request: Update, http: Request` | `见实现` | [update](../../ExtractESG/document/src/esg_document/api/report_resources.py#L121) |
| `GET /api/report-sessions` | `archived: bool=False` | `见实现` | [runs](../../ConsoleESG/backend/src/esg_console/sessions.py#L72) |
| `POST /api/report-sessions` | `request: ExtractionPlanRequest` | `见实现` | [create](../../ConsoleESG/backend/src/esg_console/sessions.py#L67) |
| `POST /api/report-sessions/archive` | `request: SessionSelection` | `见实现` | [archive_selected](../../ConsoleESG/backend/src/esg_console/sessions.py#L43) |
| `POST /api/report-sessions/preview` | `request: ExtractionPlanRequest` | `见实现` | [preview](../../ConsoleESG/backend/src/esg_console/sessions.py#L62) |
| `POST /api/report-sessions/purge` | `request: SessionSelection` | `见实现` | [purge_selected](../../ConsoleESG/backend/src/esg_console/sessions.py#L51) |
| `POST /api/report-sessions/restore` | `request: SessionSelection` | `见实现` | [restore_selected](../../ConsoleESG/backend/src/esg_console/sessions.py#L47) |
| `GET /api/report-sessions/{key}` | `key: str` | `见实现` | [run](../../ConsoleESG/backend/src/esg_console/sessions.py#L76) |
| `POST /api/report-sessions/{key}/archive` | `key: str` | `见实现` | [archive](../../ConsoleESG/backend/src/esg_console/sessions.py#L35) |
| `POST /api/report-sessions/{key}/cancel` | `key: str` | `见实现` | [cancel](../../ConsoleESG/backend/src/esg_console/sessions.py#L80) |
| `GET /api/report-sessions/{key}/candidates` | `key: str` | `见实现` | [candidates](../../ConsoleESG/backend/src/esg_console/sessions.py#L55) |
| `GET /api/report-sessions/{key}/facts` | `key: str, limit: int=50, cursor: str &#124; None=None, clause_key: str &#124; None=None, period: str &#124; None=None, relation: str &#124; None=None, unit: str &#124; None=None, q: str &#124; None=None, fact_id: str &#124; None=None` | `FactPage` | [facts](../../ConsoleESG/backend/src/esg_console/sessions.py#L99) |
| `GET /api/report-sessions/{key}/organized/{clause_key}` | `key: str, clause_key: str, limit: int=50, cursor: str &#124; None=None, period: str &#124; None=None, relation: str &#124; None=None, unit: str &#124; None=None, q: str &#124; None=None` | `OrganizedPage` | [organized](../../ConsoleESG/backend/src/esg_console/sessions.py#L126) |
| `GET /api/report-sessions/{key}/products` | `key: str` | `SessionProducts` | [product_state](../../ConsoleESG/backend/src/esg_console/sessions.py#L30) |
| `POST /api/report-sessions/{key}/restore` | `key: str` | `见实现` | [restore](../../ConsoleESG/backend/src/esg_console/sessions.py#L39) |
| `GET /api/report-sessions/{key}/results` | `key: str` | `ResultOverview` | [result_overview](../../ConsoleESG/backend/src/esg_console/sessions.py#L92) |
| `POST /api/report-sessions/{key}/resume` | `key: str` | `见实现` | [resume](../../ConsoleESG/backend/src/esg_console/sessions.py#L84) |
| `GET /api/report-sources/{source_sha256}/file` | `source_sha256: str` | `FileResponse` | [get_report_source_file](../../ExtractESG/document/src/esg_document/api/main.py#L634) |
| `GET /api/reports` | `` | `见实现` | [reports](../../ExtractESG/document/src/esg_document/api/report_processing.py#L47) |
| `POST /api/reports` | `request: RegisterReportRequest` | `见实现` | [register](../../ExtractESG/document/src/esg_document/api/report_processing.py#L54) |
| `GET /api/reports/{report_id}` | `report_id: str` | `见实现` | [report](../../ExtractESG/document/src/esg_document/api/report_processing.py#L60) |
| `PATCH /api/reports/{report_id}` | `report_id: str, request: ReportMetadataPatch` | `见实现` | [update_report](../../ExtractESG/document/src/esg_document/api/report_processing.py#L64) |
| `POST /api/reports/{report_id}/versions` | `report_id: str, request: ReportVersionRequest` | `见实现` | [attach_version](../../ExtractESG/document/src/esg_document/api/report_processing.py#L68) |
| `POST /api/storage/deletion-plans` | `request: DeletionPlanRequest=Body(...)` | `dict[str, object]` | [create_storage_deletion_plan](../../ExtractESG/document/src/esg_document/api/main.py#L924) |
| `POST /api/storage/deletion-plans/{plan_id}/execute` | `plan_id: str, request: LifecycleCommand &#124; None=Body(default=None)` | `dict[str, object]` | [execute_storage_deletion_plan](../../ExtractESG/document/src/esg_document/api/main.py#L934) |
| `GET /api/storage/inventory` | `` | `dict[str, object]` | [get_storage_inventory](../../ExtractESG/document/src/esg_document/api/main.py#L919) |
| `GET /api/storage/trash` | `` | `见实现` | [list_storage_trash](../../ExtractESG/document/src/esg_document/api/main.py#L958) |
| `POST /api/storage/trash/{cleanup_id}/purge` | `cleanup_id: str, request: LifecycleCommand` | `见实现` | [purge_storage_trash](../../ExtractESG/document/src/esg_document/api/main.py#L976) |
| `POST /api/storage/trash/{cleanup_id}/restore` | `cleanup_id: str, request: LifecycleCommand` | `见实现` | [restore_storage_trash](../../ExtractESG/document/src/esg_document/api/main.py#L966) |
| `GET /health` | `` | `dict[str, object]` | [health](../../ExtractESG/document/src/esg_document/api/main.py#L246) |
| `GET /health/compatibility` | `` | `dict` | [get_compatibility](../../ExtractESG/document/src/esg_document/api/main.py#L263) |

## 条款与提取 · 18180

| 方法与路径 | 参数（含请求模型） | 返回模型 | 实现 |
|---|---|---|---|
| `GET /api/clause-contract` | `` | `见实现` | [clause_contract](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L181) |
| `GET /api/clause-groups` | `` | `见实现` | [groups](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L193) |
| `POST /api/clause-selection-options` | `keys: list[str]=Body(..., min_length=1, max_length=2000)` | `见实现` | [selection_options](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L231) |
| `GET /api/clause-selections` | `` | `见实现` | [selections](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L202) |
| `GET /api/clauses` | `` | `ClauseCatalog` | [clauses](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L171) |
| `POST /api/clauses/template` | `request: ClauseTemplate` | `见实现` | [clause_template](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L185) |
| `GET /api/clauses/{key}` | `key: str` | `见实现` | [clause](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L206) |
| `PUT /api/clauses/{key}` | `key: str, request: SaveClause` | `见实现` | [save](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L235) |
| `GET /api/clauses/{key}/organization` | `key: str` | `见实现` | [clause_organization](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L210) |
| `GET /api/clauses/{key}/reading-card` | `key: str` | `见实现` | [reading_card](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L224) |
| `GET /api/compatibility` | `` | `见实现` | [compatibility](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L129) |
| `GET /api/engine` | `` | `见实现` | [engine](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L154) |
| `GET /api/organization-contract` | `` | `见实现` | [organization_contract](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L162) |
| `GET /api/runs` | `` | `见实现` | [runs](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L308) |
| `POST /api/runs` | `request: CreateRun` | `NativeRun` | [create](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L268) |
| `POST /api/runs/preview` | `request: RunRequest` | `见实现` | [preview](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L253) |
| `POST /api/runs/purge` | `request: PurgeRuns` | `见实现` | [purge_runs](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L321) |
| `POST /api/runs/status` | `query: RunStatusQuery` | `见实现` | [statuses](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L313) |
| `GET /api/runs/{run_id}` | `run_id: str` | `NativeRun` | [run](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L317) |
| `POST /api/runs/{run_id}/cancel` | `run_id: str` | `NativeRun` | [cancel](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L335) |
| `GET /api/runs/{run_id}/candidates` | `run_id: str` | `list[CandidateRejection]` | [candidates](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L460) |
| `POST /api/runs/{run_id}/execution-terminal` | `run_id: str, request: ExecutionTerminal` | `NativeRun` | [execution_terminal](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L384) |
| `POST /api/runs/{run_id}/exports` | `run_id: str` | `见实现` | [export](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L469) |
| `GET /api/runs/{run_id}/exports/{export_id}` | `run_id: str, export_id: str` | `见实现` | [download_export](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L478) |
| `GET /api/runs/{run_id}/facts` | `run_id: str, limit: int=Query(50, ge=1, le=500), cursor: str &#124; None=None, clause_key: str &#124; None=None, period: str &#124; None=None, relation: str &#124; None=None, unit: str &#124; None=None, q: str &#124; None=None, fact_id: str &#124; None=None` | `FactPage` | [facts](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L424) |
| `GET /api/runs/{run_id}/facts/{fact_id}` | `run_id: str, fact_id: str` | `见实现` | [fact](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L394) |
| `GET /api/runs/{run_id}/organized/{clause_key}` | `run_id: str, clause_key: str, limit: int=Query(50, ge=1, le=500), cursor: str &#124; None=None, period: str &#124; None=None, relation: str &#124; None=None, unit: str &#124; None=None, q: str &#124; None=None` | `OrganizedPage` | [organized](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L437) |
| `GET /api/runs/{run_id}/results` | `run_id: str` | `ResultOverview` | [result_overview](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L409) |
| `POST /api/runs/{run_id}/resume` | `run_id: str, request: ResumeRun` | `NativeRun` | [resume](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L347) |
| `GET /api/runs/{run_id}/targets` | `run_id: str` | `见实现` | [run_targets](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L330) |
| `POST /api/targets/freeze` | `request: RunRequest` | `见实现` | [freeze](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L247) |
| `GET /health` | `` | `见实现` | [health](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py#L140) |
