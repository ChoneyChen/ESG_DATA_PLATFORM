# 源码索引

由`ExtractESG/.local/python/bin/python tools/build_documentation.py`生成；编辑职责说明请修改[模块设计](../03-modules.md)，不要手改本索引。

只读取第一方源码，排除CrawlESG、私有运行数据、依赖与构建目录。单条JSON资产由ClauseESG自己的目录索引管理；生成Schema和TypeScript类型见合同规范，不在这里复制。

## ClauseESG

| 文件 | 源码说明 |
|---|---|
| [ClauseESG/pyproject.toml](../../ClauseESG/pyproject.toml) | 源码/配置；职责见模块设计 |
| [ClauseESG/src/esg_clauses/__init__.py](../../ClauseESG/src/esg_clauses/__init__.py) | Clause contracts and their independent repository interface. |
| [ClauseESG/src/esg_clauses/attribute_templates.py](../../ClauseESG/src/esg_clauses/attribute_templates.py) | Authoring starting points, copied into a clause; readers consume the saved clause only. |
| [ClauseESG/src/esg_clauses/attributes.py](../../ClauseESG/src/esg_clauses/attributes.py) | One clause-owned definition per reported attribute, independent of display columns. |
| [ClauseESG/src/esg_clauses/base.py](../../ClauseESG/src/esg_clauses/base.py) | 源码/配置；职责见模块设计 |
| [ClauseESG/src/esg_clauses/catalog.py](../../ClauseESG/src/esg_clauses/catalog.py) | Public clause navigation contracts contain identities, never storage paths. |
| [ClauseESG/src/esg_clauses/contract.py](../../ClauseESG/src/esg_clauses/contract.py) | The independent clause contract; no extraction engine or aggregate package dependency. |
| [ClauseESG/src/esg_clauses/descriptor.py](../../ClauseESG/src/esg_clauses/descriptor.py) | Navigable standard structure; deliberately not an executable value contract. |
| [ClauseESG/src/esg_clauses/evidence.py](../../ClauseESG/src/esg_clauses/evidence.py) | Evaluate clause confirmation requirements against value-only source proofs. |
| [ClauseESG/src/esg_clauses/parts.py](../../ClauseESG/src/esg_clauses/parts.py) | 源码/配置；职责见模块设计 |
| [ClauseESG/src/esg_clauses/reading.py](../../ClauseESG/src/esg_clauses/reading.py) | Generic interpreter for clause-owned reading rules and vocabulary. |
| [ClauseESG/src/esg_clauses/repository.py](../../ClauseESG/src/esg_clauses/repository.py) | One directory is one clause. Group folders contain presentation metadata only. |
| [ClauseESG/src/esg_clauses/scope.py](../../ClauseESG/src/esg_clauses/scope.py) | Controlled scope declarations. Unspecified axes constrain neither admission nor matching. |
| [ClauseESG/src/esg_clauses/search.py](../../ClauseESG/src/esg_clauses/search.py) | Compile every selected clause's search fields once; consumers own execution. |
| [ClauseESG/src/esg_clauses/templates.py](../../ClauseESG/src/esg_clauses/templates.py) | Minimal independent clause templates owned by the clause module. |
| [ClauseESG/tests/test_descriptors.py](../../ClauseESG/tests/test_descriptors.py) | 测试/样例 |
| [ClauseESG/tests/test_independence.py](../../ClauseESG/tests/test_independence.py) | 测试/样例 |
| [ClauseESG/tests/test_independent_repository.py](../../ClauseESG/tests/test_independent_repository.py) | 测试/样例 |

## ConsoleESG

| 文件 | 源码说明 |
|---|---|
| [ConsoleESG/backend/pyproject.toml](../../ConsoleESG/backend/pyproject.toml) | 源码/配置；职责见模块设计 |
| [ConsoleESG/backend/src/esg_console/__init__.py](../../ConsoleESG/backend/src/esg_console/__init__.py) | 源码/配置；职责见模块设计 |
| [ConsoleESG/backend/src/esg_console/application.py](../../ConsoleESG/backend/src/esg_console/application.py) | Local composition root. Document algorithms do not import the Console facade. |
| [ConsoleESG/backend/src/esg_console/monitor.py](../../ConsoleESG/backend/src/esg_console/monitor.py) | Safe, versioned Console projection. No scheduler payload crosses this port. |
| [ConsoleESG/backend/src/esg_console/products.py](../../ConsoleESG/backend/src/esg_console/products.py) | Session-facing status is composed once on the backend, reused by page and monitor. |
| [ConsoleESG/backend/src/esg_console/sessions.py](../../ConsoleESG/backend/src/esg_console/sessions.py) | 源码/配置；职责见模块设计 |
| [ConsoleESG/backend/tests/test_monitor.py](../../ConsoleESG/backend/tests/test_monitor.py) | 测试/样例 |
| [ConsoleESG/backend/tests/test_products.py](../../ConsoleESG/backend/tests/test_products.py) | One backend workflow projection is reused by session details and the monitor. |
| [ConsoleESG/frontend/index.html](../../ConsoleESG/frontend/index.html) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/package-lock.json](../../ConsoleESG/frontend/package-lock.json) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/package.json](../../ConsoleESG/frontend/package.json) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/app/App.tsx](../../ConsoleESG/frontend/src/app/App.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/app/routes.ts](../../ConsoleESG/frontend/src/app/routes.ts) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/app/workflow.test.tsx](../../ConsoleESG/frontend/src/app/workflow.test.tsx) | 测试/样例 |
| [ConsoleESG/frontend/src/features/clauses/editor.tsx](../../ConsoleESG/frontend/src/features/clauses/editor.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/clauses/pages.tsx](../../ConsoleESG/frontend/src/features/clauses/pages.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/extractions/create.tsx](../../ConsoleESG/frontend/src/features/extractions/create.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/extractions/detail.tsx](../../ConsoleESG/frontend/src/features/extractions/detail.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/extractions/list.tsx](../../ConsoleESG/frontend/src/features/extractions/list.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/extractions/result-pages.tsx](../../ConsoleESG/frontend/src/features/extractions/result-pages.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/extractions/results.test.tsx](../../ConsoleESG/frontend/src/features/extractions/results.test.tsx) | 测试/样例 |
| [ConsoleESG/frontend/src/features/extractions/results.tsx](../../ConsoleESG/frontend/src/features/extractions/results.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/publication/create.tsx](../../ConsoleESG/frontend/src/features/publication/create.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/publication/data.tsx](../../ConsoleESG/frontend/src/features/publication/data.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/publication/observations.tsx](../../ConsoleESG/frontend/src/features/publication/observations.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/publication/pages.tsx](../../ConsoleESG/frontend/src/features/publication/pages.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/publication/workflow.test.tsx](../../ConsoleESG/frontend/src/features/publication/workflow.test.tsx) | 测试/样例 |
| [ConsoleESG/frontend/src/features/reports/dialogs.tsx](../../ConsoleESG/frontend/src/features/reports/dialogs.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/reports/pages.tsx](../../ConsoleESG/frontend/src/features/reports/pages.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/runtime/monitor.test.tsx](../../ConsoleESG/frontend/src/features/runtime/monitor.test.tsx) | 测试/样例 |
| [ConsoleESG/frontend/src/features/runtime/monitor.tsx](../../ConsoleESG/frontend/src/features/runtime/monitor.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/features/runtime/pages.tsx](../../ConsoleESG/frontend/src/features/runtime/pages.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/main.tsx](../../ConsoleESG/frontend/src/main.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/shared/api.test.ts](../../ConsoleESG/frontend/src/shared/api.test.ts) | 测试/样例 |
| [ConsoleESG/frontend/src/shared/api.ts](../../ConsoleESG/frontend/src/shared/api.ts) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/shared/contracts.ts](../../ConsoleESG/frontend/src/shared/contracts.ts) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/shared/format.ts](../../ConsoleESG/frontend/src/shared/format.ts) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/shared/resource.test.tsx](../../ConsoleESG/frontend/src/shared/resource.test.tsx) | 测试/样例 |
| [ConsoleESG/frontend/src/shared/resource.ts](../../ConsoleESG/frontend/src/shared/resource.ts) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/shared/ui.test.tsx](../../ConsoleESG/frontend/src/shared/ui.test.tsx) | 测试/样例 |
| [ConsoleESG/frontend/src/shared/ui.tsx](../../ConsoleESG/frontend/src/shared/ui.tsx) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/src/styles.css](../../ConsoleESG/frontend/src/styles.css) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/tsconfig.json](../../ConsoleESG/frontend/tsconfig.json) | 源码/配置；职责见模块设计 |
| [ConsoleESG/frontend/vite.config.ts](../../ConsoleESG/frontend/vite.config.ts) | 源码/配置；职责见模块设计 |
| [ConsoleESG/scripts/install_desktop_launcher.py](../../ConsoleESG/scripts/install_desktop_launcher.py) | Install the single current desktop entry atomically, without retaining old launchers. |
| [ConsoleESG/scripts/install_local.sh](../../ConsoleESG/scripts/install_local.sh) | 源码/配置；职责见模块设计 |
| [ConsoleESG/scripts/install_worker.py](../../ConsoleESG/scripts/install_worker.py) | Install current worker distributions into the configured local model Pythons, offline. |
| [ConsoleESG/scripts/run_service.sh](../../ConsoleESG/scripts/run_service.sh) | 源码/配置；职责见模块设计 |
| [ConsoleESG/scripts/serve_workbench_frontend.py](../../ConsoleESG/scripts/serve_workbench_frontend.py) | Static local workbench server with a boot-frozen compatibility endpoint. |
| [ConsoleESG/scripts/start.sh](../../ConsoleESG/scripts/start.sh) | 源码/配置；职责见模块设计 |
| [ConsoleESG/scripts/upgrade_pending_execution.py](../../ConsoleESG/scripts/upgrade_pending_execution.py) | Explicit code-only handoff for pending runs; frozen business inputs stay intact. |
| [ConsoleESG/scripts/workbench.py](../../ConsoleESG/scripts/workbench.py) | Local workbench controller. Detached services, explicit ownership and no database migration. |

## ExtractESG/contracts

| 文件 | 源码说明 |
|---|---|
| [ExtractESG/contracts/examples/report-batch/manifest.template.json](../../ExtractESG/contracts/examples/report-batch/manifest.template.json) | 源码/配置；职责见模块设计 |
| [ExtractESG/contracts/pyproject.toml](../../ExtractESG/contracts/pyproject.toml) | 源码/配置；职责见模块设计 |
| [ExtractESG/contracts/src/esg_contracts/annotations.py](../../ExtractESG/contracts/src/esg_contracts/annotations.py) | Canonical, rebuildable annotations projected from authoritative source facts. |
| [ExtractESG/contracts/src/esg_contracts/data_kinds/__init__.py](../../ExtractESG/contracts/src/esg_contracts/data_kinds/__init__.py) | Two data contracts. Unknown annotations are retained under extensions. |
| [ExtractESG/contracts/src/esg_contracts/data_kinds/qualitative.py](../../ExtractESG/contracts/src/esg_contracts/data_kinds/qualitative.py) | Qualitative value contract: the literal statement is mandatory; annotations extend it. |
| [ExtractESG/contracts/src/esg_contracts/data_kinds/quantitative.py](../../ExtractESG/contracts/src/esg_contracts/data_kinds/quantitative.py) | Quantitative value contract: printed value first, structured fields are optional. |
| [ExtractESG/contracts/src/esg_contracts/document.py](../../ExtractESG/contracts/src/esg_contracts/document.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/contracts/src/esg_contracts/evidence.py](../../ExtractESG/contracts/src/esg_contracts/evidence.py) | The document service exposes evidence objects, never storage directories. |
| [ExtractESG/contracts/src/esg_contracts/exports.py](../../ExtractESG/contracts/src/esg_contracts/exports.py) | Sealed producer-to-database handoff; no service paths or presentation objects. |
| [ExtractESG/contracts/src/esg_contracts/facts.py](../../ExtractESG/contracts/src/esg_contracts/facts.py) | Pipeline records: evidence/facts/goals are independent contracts. |
| [ExtractESG/contracts/src/esg_contracts/operations.py](../../ExtractESG/contracts/src/esg_contracts/operations.py) | Operational observations expose no dispatch payload, local paths or runtime secrets. |
| [ExtractESG/contracts/src/esg_contracts/results.py](../../ExtractESG/contracts/src/esg_contracts/results.py) | Extraction delivery is an independent, validated collection of raw facts and links. |
| [ExtractESG/contracts/src/esg_contracts/search.py](../../ExtractESG/contracts/src/esg_contracts/search.py) | Document-service search input. No standard, selection-set or package imports. |
| [ExtractESG/contracts/src/esg_contracts/sessions.py](../../ExtractESG/contracts/src/esg_contracts/sessions.py) | Public extraction operation views. Scientific facts retain their own contract. |
| [ExtractESG/contracts/src/esg_contracts/worker.py](../../ExtractESG/contracts/src/esg_contracts/worker.py) | Line protocol shared by isolated producers and the queue consumer. |
| [ExtractESG/contracts/src/esg_data/__init__.py](../../ExtractESG/contracts/src/esg_data/__init__.py) | Neutral data primitives: literal values, units and text normalization. |
| [ExtractESG/contracts/src/esg_data/categories.py](../../ExtractESG/contracts/src/esg_data/categories.py) | Literal category matching shared by clause attributes and organized values. |
| [ExtractESG/contracts/src/esg_data/hashing.py](../../ExtractESG/contracts/src/esg_data/hashing.py) | Canonical JSON content identity shared by module interfaces. |
| [ExtractESG/contracts/src/esg_data/literals.py](../../ExtractESG/contracts/src/esg_data/literals.py) | Literal parsing is physical data normalization, independent of clauses and workflows. |
| [ExtractESG/contracts/src/esg_data/normalize.py](../../ExtractESG/contracts/src/esg_data/normalize.py) | Traditional→simplified folding for matching only; source text stays verbatim. |
| [ExtractESG/contracts/src/esg_data/organization.py](../../ExtractESG/contracts/src/esg_data/organization.py) | General extraction table contract; domain fields belong to independent clauses. |
| [ExtractESG/contracts/src/esg_data/periods.py](../../ExtractESG/contracts/src/esg_data/periods.py) | Reported time, independent of publication year and clause identity. |
| [ExtractESG/contracts/src/esg_data/quantities.py](../../ExtractESG/contracts/src/esg_data/quantities.py) | Deterministic unit families and scale factors for cross-source agreement. |
| [ExtractESG/contracts/src/esg_data/sanitization.py](../../ExtractESG/contracts/src/esg_data/sanitization.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/contracts/src/esg_data/text_search.py](../../ExtractESG/contracts/src/esg_data/text_search.py) | Lexical fallback for clause phrases; no indicator names or model meanings. |
| [ExtractESG/contracts/src/esg_data/units.py](../../ExtractESG/contracts/src/esg_data/units.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/contracts/src/esg_reports/__init__.py](../../ExtractESG/contracts/src/esg_reports/__init__.py) | Independent report resource contracts and folder import interface. |
| [ExtractESG/contracts/src/esg_reports/contract.py](../../ExtractESG/contracts/src/esg_reports/contract.py) | Report resource identity is PDF-level, distinct from measurement identity. |
| [ExtractESG/contracts/src/esg_reports/importer.py](../../ExtractESG/contracts/src/esg_reports/importer.py) | Import manifest-listed PDFs and persist their folder membership separately. |
| [ExtractESG/contracts/src/esg_reports/repository.py](../../ExtractESG/contracts/src/esg_reports/repository.py) | PDF resource metadata store. No clause, extraction engine or publication dependency. |

## ExtractESG/document

| 文件 | 源码说明 |
|---|---|
| [ExtractESG/document/src/esg_document/api/__init__.py](../../ExtractESG/document/src/esg_document/api/__init__.py) | FastAPI application package. |
| [ExtractESG/document/src/esg_document/api/document_archive.py](../../ExtractESG/document/src/esg_document/api/document_archive.py) | Document archive queries own physical evidence reads; the API composes dependencies. |
| [ExtractESG/document/src/esg_document/api/error_contract.py](../../ExtractESG/document/src/esg_document/api/error_contract.py) | Keep uncaught service errors machine readable without exposing internal state. |
| [ExtractESG/document/src/esg_document/api/evidence.py](../../ExtractESG/document/src/esg_document/api/evidence.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/api/library_deletion.py](../../ExtractESG/document/src/esg_document/api/library_deletion.py) | Authenticated library endpoints. Authorization is supplied by the app. |
| [ExtractESG/document/src/esg_document/api/main.py](../../ExtractESG/document/src/esg_document/api/main.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/api/operator_auth.py](../../ExtractESG/document/src/esg_document/api/operator_auth.py) | Local operator boundary. Document operations do not depend on Plat or a token file. |
| [ExtractESG/document/src/esg_document/api/processing_status.py](../../ExtractESG/document/src/esg_document/api/processing_status.py) | Small read projection for the global processing window, no execution owner. |
| [ExtractESG/document/src/esg_document/api/report_processing.py](../../ExtractESG/document/src/esg_document/api/report_processing.py) | Report-processing routes, separate from extraction and service readiness. |
| [ExtractESG/document/src/esg_document/api/report_resources.py](../../ExtractESG/document/src/esg_document/api/report_resources.py) | Report resource metadata and batch intake. The report module owns these files. |
| [ExtractESG/document/src/esg_document/assets/__init__.py](../../ExtractESG/document/src/esg_document/assets/__init__.py) | Local report-asset catalog owned by the development workbench. |
| [ExtractESG/document/src/esg_document/assets/library_deletion.py](../../ExtractESG/document/src/esg_document/assets/library_deletion.py) | PDF and OCR libraries: explicit, guarded permanent deletion. |
| [ExtractESG/document/src/esg_document/assets/report_catalog.py](../../ExtractESG/document/src/esg_document/assets/report_catalog.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/cli.py](../../ExtractESG/document/src/esg_document/cli.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/config.py](../../ExtractESG/document/src/esg_document/config.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/__init__.py](../../ExtractESG/document/src/esg_document/document/__init__.py) | Document intelligence components for ESG v2. |
| [ExtractESG/document/src/esg_document/document/artifact_loader.py](../../ExtractESG/document/src/esg_document/document/artifact_loader.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/catalog.py](../../ExtractESG/document/src/esg_document/document/catalog.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/chart_evidence.py](../../ExtractESG/document/src/esg_document/document/chart_evidence.py) | Point-level source support, independent from transaction/verifier acceptance. |
| [ExtractESG/document/src/esg_document/document/chart_spec_normalizer.py](../../ExtractESG/document/src/esg_document/document/chart_spec_normalizer.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/contracts.py](../../ExtractESG/document/src/esg_document/document/contracts.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/convergence_engine.py](../../ExtractESG/document/src/esg_document/document/convergence_engine.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/coordinate_mapper.py](../../ExtractESG/document/src/esg_document/document/coordinate_mapper.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/crop_builder.py](../../ExtractESG/document/src/esg_document/document/crop_builder.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/deduplicator.py](../../ExtractESG/document/src/esg_document/document/deduplicator.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/evidence_capabilities.py](../../ExtractESG/document/src/esg_document/document/evidence_capabilities.py) | Reading capabilities derived from real geometry, never reconstructed cell boxes. |
| [ExtractESG/document/src/esg_document/document/evidence_references.py](../../ExtractESG/document/src/esg_document/document/evidence_references.py) | Resolve registered source identities without promoting context into point proof. |
| [ExtractESG/document/src/esg_document/document/evidence_service.py](../../ExtractESG/document/src/esg_document/document/evidence_service.py) | Document evidence port. Requests contain lexical intent and object references. |
| [ExtractESG/document/src/esg_document/document/geometry.py](../../ExtractESG/document/src/esg_document/document/geometry.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/html_table_parser.py](../../ExtractESG/document/src/esg_document/document/html_table_parser.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/identifier_normalizer.py](../../ExtractESG/document/src/esg_document/document/identifier_normalizer.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/identifiers.py](../../ExtractESG/document/src/esg_document/document/identifiers.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/identity.py](../../ExtractESG/document/src/esg_document/document/identity.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/layout_extractor.py](../../ExtractESG/document/src/esg_document/document/layout_extractor.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/literals.py](../../ExtractESG/document/src/esg_document/document/literals.py) | Neutral literal inventory; semantics and clause matching belong to the reader. |
| [ExtractESG/document/src/esg_document/document/local_forensics.py](../../ExtractESG/document/src/esg_document/document/local_forensics.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/logical_table_builder.py](../../ExtractESG/document/src/esg_document/document/logical_table_builder.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/model_json_decoder.py](../../ExtractESG/document/src/esg_document/document/model_json_decoder.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/model_runner.py](../../ExtractESG/document/src/esg_document/document/model_runner.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/native_text_comparison.py](../../ExtractESG/document/src/esg_document/document/native_text_comparison.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/operation_payloads.py](../../ExtractESG/document/src/esg_document/document/operation_payloads.py) | Current review-operation codecs and normalization of fresh provider responses. |
| [ExtractESG/document/src/esg_document/document/operation_registry.py](../../ExtractESG/document/src/esg_document/document/operation_registry.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/paddleocr_converter.py](../../ExtractESG/document/src/esg_document/document/paddleocr_converter.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/page_renderer.py](../../ExtractESG/document/src/esg_document/document/page_renderer.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/parser_fusion.py](../../ExtractESG/document/src/esg_document/document/parser_fusion.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/patch_guard.py](../../ExtractESG/document/src/esg_document/document/patch_guard.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/physical.py](../../ExtractESG/document/src/esg_document/document/physical.py) | Compact, model-readable views of fixed Document IR tables. |
| [ExtractESG/document/src/esg_document/document/provenance.py](../../ExtractESG/document/src/esg_document/document/provenance.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/quality_router.py](../../ExtractESG/document/src/esg_document/document/quality_router.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/reader.py](../../ExtractESG/document/src/esg_document/document/reader.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/report_period.py](../../ExtractESG/document/src/esg_document/document/report_period.py) | Reporting-period evidence from the report itself, never from the largest year. |
| [ExtractESG/document/src/esg_document/document/review_context.py](../../ExtractESG/document/src/esg_document/document/review_context.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/review_orchestrator.py](../../ExtractESG/document/src/esg_document/document/review_orchestrator.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/review_plan_compiler.py](../../ExtractESG/document/src/esg_document/document/review_plan_compiler.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/review_policy.py](../../ExtractESG/document/src/esg_document/document/review_policy.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/review_response_adapter.py](../../ExtractESG/document/src/esg_document/document/review_response_adapter.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/review_scheduler.py](../../ExtractESG/document/src/esg_document/document/review_scheduler.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/revision_service.py](../../ExtractESG/document/src/esg_document/document/revision_service.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/search_ranking.py](../../ExtractESG/document/src/esg_document/document/search_ranking.py) | Rank evidence for an explicit query; keep sibling table concepts separate. |
| [ExtractESG/document/src/esg_document/document/source.py](../../ExtractESG/document/src/esg_document/document/source.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/spread_builder.py](../../ExtractESG/document/src/esg_document/document/spread_builder.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/spread_pair_analysis.py](../../ExtractESG/document/src/esg_document/document/spread_pair_analysis.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/spread_preflight.py](../../ExtractESG/document/src/esg_document/document/spread_preflight.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/structure_reconstruction.py](../../ExtractESG/document/src/esg_document/document/structure_reconstruction.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/table_geometry_resolver.py](../../ExtractESG/document/src/esg_document/document/table_geometry_resolver.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/table_graph_builder.py](../../ExtractESG/document/src/esg_document/document/table_graph_builder.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/text_normalization.py](../../ExtractESG/document/src/esg_document/document/text_normalization.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/transaction_coordinator.py](../../ExtractESG/document/src/esg_document/document/transaction_coordinator.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/validator.py](../../ExtractESG/document/src/esg_document/document/validator.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/versioning.py](../../ExtractESG/document/src/esg_document/document/versioning.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/visual_region_builder.py](../../ExtractESG/document/src/esg_document/document/visual_region_builder.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/visual_semantic_grouper.py](../../ExtractESG/document/src/esg_document/document/visual_semantic_grouper.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/document/writer.py](../../ExtractESG/document/src/esg_document/document/writer.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/__init__.py](../../ExtractESG/document/src/esg_document/models/__init__.py) | Cloud model adapters for ESG v2. |
| [ExtractESG/document/src/esg_document/models/contracts.py](../../ExtractESG/document/src/esg_document/models/contracts.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/local_nuextract_adapter.py](../../ExtractESG/document/src/esg_document/models/local_nuextract_adapter.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/local_nuextract_registry.py](../../ExtractESG/document/src/esg_document/models/local_nuextract_registry.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/local_nuextract_worker.py](../../ExtractESG/document/src/esg_document/models/local_nuextract_worker.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/local_qwen_registry.py](../../ExtractESG/document/src/esg_document/models/local_qwen_registry.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/model_registry.py](../../ExtractESG/document/src/esg_document/models/model_registry.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/provider_error.py](../../ExtractESG/document/src/esg_document/models/provider_error.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/provider_rate_limit.py](../../ExtractESG/document/src/esg_document/models/provider_rate_limit.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/qiniu_adapter.py](../../ExtractESG/document/src/esg_document/models/qiniu_adapter.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/review_provider.py](../../ExtractESG/document/src/esg_document/models/review_provider.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/models/vision_input.py](../../ExtractESG/document/src/esg_document/models/vision_input.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/__init__.py](../../ExtractESG/document/src/esg_document/ocr/__init__.py) | OCR parser adapters. |
| [ExtractESG/document/src/esg_document/ocr/client.py](../../ExtractESG/document/src/esg_document/ocr/client.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/local_worker.py](../../ExtractESG/document/src/esg_document/ocr/local_worker.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/loopback.py](../../ExtractESG/document/src/esg_document/ocr/loopback.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/output_writer.py](../../ExtractESG/document/src/esg_document/ocr/output_writer.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/page_selection.py](../../ExtractESG/document/src/esg_document/ocr/page_selection.py) | Text-layer page selection for page-targeted OCR. |
| [ExtractESG/document/src/esg_document/ocr/pdf_preflight.py](../../ExtractESG/document/src/esg_document/ocr/pdf_preflight.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/providers.py](../../ExtractESG/document/src/esg_document/ocr/providers.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/resources.py](../../ExtractESG/document/src/esg_document/ocr/resources.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/text_layer.py](../../ExtractESG/document/src/esg_document/ocr/text_layer.py) | Page results for digitally produced PDF pages, read from their text layer. |
| [ExtractESG/document/src/esg_document/ocr/vision_ocr.swift](../../ExtractESG/document/src/esg_document/ocr/vision_ocr.swift) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/ocr/vision_prescan.py](../../ExtractESG/document/src/esg_document/ocr/vision_prescan.py) | Page text for scanned PDFs from the macOS Vision framework (on-device). |
| [ExtractESG/document/src/esg_document/runtime_compatibility.py](../../ExtractESG/document/src/esg_document/runtime_compatibility.py) | Workspace release contract, captured before the API starts serving. |
| [ExtractESG/document/src/esg_document/runtime_telemetry.py](../../ExtractESG/document/src/esg_document/runtime_telemetry.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/storage/__init__.py](../../ExtractESG/document/src/esg_document/storage/__init__.py) | Local development storage helpers. |
| [ExtractESG/document/src/esg_document/storage/copy_on_write.py](../../ExtractESG/document/src/esg_document/storage/copy_on_write.py) | Copy independent revision assets, using filesystem cloning when available. |
| [ExtractESG/document/src/esg_document/storage/inventory.py](../../ExtractESG/document/src/esg_document/storage/inventory.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/storage/ir_retention.py](../../ExtractESG/document/src/esg_document/storage/ir_retention.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/storage/job_store.py](../../ExtractESG/document/src/esg_document/storage/job_store.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/storage/package_layout.py](../../ExtractESG/document/src/esg_document/storage/package_layout.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/storage/package_validator.py](../../ExtractESG/document/src/esg_document/storage/package_validator.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/storage/processing_lifecycle.py](../../ExtractESG/document/src/esg_document/storage/processing_lifecycle.py) | Lifecycle of report-processing records; stage files have separate owners. |
| [ExtractESG/document/src/esg_document/storage/queue_lifecycle.py](../../ExtractESG/document/src/esg_document/storage/queue_lifecycle.py) | Explicit release/restoration of standalone queue source pins. |
| [ExtractESG/document/src/esg_document/utils/__init__.py](../../ExtractESG/document/src/esg_document/utils/__init__.py) | Utility helpers. |
| [ExtractESG/document/src/esg_document/utils/hashing.py](../../ExtractESG/document/src/esg_document/utils/hashing.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/workflows/__init__.py](../../ExtractESG/document/src/esg_document/workflows/__init__.py) | Workflow services. |
| [ExtractESG/document/src/esg_document/workflows/ir_workflow.py](../../ExtractESG/document/src/esg_document/workflows/ir_workflow.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/src/esg_document/workflows/ocr_workflow.py](../../ExtractESG/document/src/esg_document/workflows/ocr_workflow.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/document/tests/conftest.py](../../ExtractESG/document/tests/conftest.py) | Every OCR regression test owns its PDF resource metadata store. |
| [ExtractESG/document/tests/current_api_fixture.py](../../ExtractESG/document/tests/current_api_fixture.py) | Isolated current document API stores; no report producer or historical adapter. |
| [ExtractESG/document/tests/test_api_error_contract_smoke.py](../../ExtractESG/document/tests/test_api_error_contract_smoke.py) | 测试/样例 |
| [ExtractESG/document/tests/test_asset_lifecycle.py](../../ExtractESG/document/tests/test_asset_lifecycle.py) | 测试/样例 |
| [ExtractESG/document/tests/test_asymmetric_table_rows.py](../../ExtractESG/document/tests/test_asymmetric_table_rows.py) | One canonical row mapping for physical continuations and extraction evidence. |
| [ExtractESG/document/tests/test_budget_error_progress.py](../../ExtractESG/document/tests/test_budget_error_progress.py) | 测试/样例 |
| [ExtractESG/document/tests/test_business_progress.py](../../ExtractESG/document/tests/test_business_progress.py) | 测试/样例 |
| [ExtractESG/document/tests/test_chart_spec_normalizer.py](../../ExtractESG/document/tests/test_chart_spec_normalizer.py) | 测试/样例 |
| [ExtractESG/document/tests/test_copy_on_write.py](../../ExtractESG/document/tests/test_copy_on_write.py) | 测试/样例 |
| [ExtractESG/document/tests/test_desktop_launcher.py](../../ExtractESG/document/tests/test_desktop_launcher.py) | The desktop controller must not stop unrelated or active work. |
| [ExtractESG/document/tests/test_document_identity.py](../../ExtractESG/document/tests/test_document_identity.py) | 测试/样例 |
| [ExtractESG/document/tests/test_document_ir_hardening.py](../../ExtractESG/document/tests/test_document_ir_hardening.py) | 测试/样例 |
| [ExtractESG/document/tests/test_document_ir_pipeline.py](../../ExtractESG/document/tests/test_document_ir_pipeline.py) | 测试/样例 |
| [ExtractESG/document/tests/test_evidence_capabilities.py](../../ExtractESG/document/tests/test_evidence_capabilities.py) | 测试/样例 |
| [ExtractESG/document/tests/test_extraction_api.py](../../ExtractESG/document/tests/test_extraction_api.py) | 测试/样例 |
| [ExtractESG/document/tests/test_extraction_coordinator.py](../../ExtractESG/document/tests/test_extraction_coordinator.py) | 测试/样例 |
| [ExtractESG/document/tests/test_extraction_source_reservations.py](../../ExtractESG/document/tests/test_extraction_source_reservations.py) | Isolated source guards; no report processing, old fixtures or live services. |
| [ExtractESG/document/tests/test_horizontal_spreads.py](../../ExtractESG/document/tests/test_horizontal_spreads.py) | 测试/样例 |
| [ExtractESG/document/tests/test_html_table_parser.py](../../ExtractESG/document/tests/test_html_table_parser.py) | 测试/样例 |
| [ExtractESG/document/tests/test_ir_retention.py](../../ExtractESG/document/tests/test_ir_retention.py) | 测试/样例 |
| [ExtractESG/document/tests/test_local_evidence_availability.py](../../ExtractESG/document/tests/test_local_evidence_availability.py) | 测试/样例 |
| [ExtractESG/document/tests/test_model_json_decoder.py](../../ExtractESG/document/tests/test_model_json_decoder.py) | 测试/样例 |
| [ExtractESG/document/tests/test_native_dispatch.py](../../ExtractESG/document/tests/test_native_dispatch.py) | Fault injection before durable queue commit; no OCR/model execution. |
| [ExtractESG/document/tests/test_ocr_package.py](../../ExtractESG/document/tests/test_ocr_package.py) | 测试/样例 |
| [ExtractESG/document/tests/test_ocr_providers.py](../../ExtractESG/document/tests/test_ocr_providers.py) | 测试/样例 |
| [ExtractESG/document/tests/test_operation_payload_contracts.py](../../ExtractESG/document/tests/test_operation_payload_contracts.py) | Offline contract/evidence regression; no provider, model or live IR is mutated. |
| [ExtractESG/document/tests/test_operator_auth_smoke.py](../../ExtractESG/document/tests/test_operator_auth_smoke.py) | Local operator checks never call another service or keep a credential. |
| [ExtractESG/document/tests/test_page_targeted_ocr.py](../../ExtractESG/document/tests/test_page_targeted_ocr.py) | Page-targeted OCR → Document IR keeps original page identities end to end. |
| [ExtractESG/document/tests/test_patch_guard.py](../../ExtractESG/document/tests/test_patch_guard.py) | 测试/样例 |
| [ExtractESG/document/tests/test_pipeline_control_plane.py](../../ExtractESG/document/tests/test_pipeline_control_plane.py) | 测试/样例 |
| [ExtractESG/document/tests/test_processing_lifecycle.py](../../ExtractESG/document/tests/test_processing_lifecycle.py) | 测试/样例 |
| [ExtractESG/document/tests/test_queue_reference_lifecycle.py](../../ExtractESG/document/tests/test_queue_reference_lifecycle.py) | 测试/样例 |
| [ExtractESG/document/tests/test_report_asset_file_headers.py](../../ExtractESG/document/tests/test_report_asset_file_headers.py) | 测试/样例 |
| [ExtractESG/document/tests/test_report_library.py](../../ExtractESG/document/tests/test_report_library.py) | Generic source lineage and deletion checks using temporary synthetic bytes. |
| [ExtractESG/document/tests/test_report_processing.py](../../ExtractESG/document/tests/test_report_processing.py) | Contract, dependency and recovery tests with isolated fake stage workers. |
| [ExtractESG/document/tests/test_report_resource_api.py](../../ExtractESG/document/tests/test_report_resource_api.py) | 测试/样例 |
| [ExtractESG/document/tests/test_review_kernel_regressions.py](../../ExtractESG/document/tests/test_review_kernel_regressions.py) | 测试/样例 |
| [ExtractESG/document/tests/test_review_provider.py](../../ExtractESG/document/tests/test_review_provider.py) | 测试/样例 |
| [ExtractESG/document/tests/test_review_reference_state_contract.py](../../ExtractESG/document/tests/test_review_reference_state_contract.py) | Recorded real responses replayed against anonymous candidates; no live model/service. |
| [ExtractESG/document/tests/test_review_routing.py](../../ExtractESG/document/tests/test_review_routing.py) | 测试/样例 |
| [ExtractESG/document/tests/test_review_worklist.py](../../ExtractESG/document/tests/test_review_worklist.py) | 测试/样例 |
| [ExtractESG/document/tests/test_revision_service.py](../../ExtractESG/document/tests/test_revision_service.py) | 测试/样例 |
| [ExtractESG/document/tests/test_runtime_outcomes.py](../../ExtractESG/document/tests/test_runtime_outcomes.py) | 测试/样例 |
| [ExtractESG/document/tests/test_runtime_telemetry.py](../../ExtractESG/document/tests/test_runtime_telemetry.py) | 测试/样例 |
| [ExtractESG/document/tests/test_simple_library_smoke.py](../../ExtractESG/document/tests/test_simple_library_smoke.py) | Synthetic owned files only; no live report, model or production deletion. |
| [ExtractESG/document/tests/test_storage_inventory.py](../../ExtractESG/document/tests/test_storage_inventory.py) | 测试/样例 |
| [ExtractESG/document/tests/test_text_layer_route.py](../../ExtractESG/document/tests/test_text_layer_route.py) | Text-layer route of page-targeted OCR and the scan prescan of the page selector. |

## ExtractESG/extraction

| 文件 | 源码说明 |
|---|---|
| [ExtractESG/extraction/src/esg_extraction/__init__.py](../../ExtractESG/extraction/src/esg_extraction/__init__.py) | ESG extraction backbone and its neutral service adapters. |
| [ExtractESG/extraction/src/esg_extraction/models/__init__.py](../../ExtractESG/extraction/src/esg_extraction/models/__init__.py) | Local model adapters; all downstream code depends on protocols only. |
| [ExtractESG/extraction/src/esg_extraction/models/capabilities.py](../../ExtractESG/extraction/src/esg_extraction/models/capabilities.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/extraction/src/esg_extraction/models/errors.py](../../ExtractESG/extraction/src/esg_extraction/models/errors.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/extraction/src/esg_extraction/models/mlx_runtime.py](../../ExtractESG/extraction/src/esg_extraction/models/mlx_runtime.py) | Shared MLX execution; no NuExtract or Qwen prompt assumptions. |
| [ExtractESG/extraction/src/esg_extraction/models/schema_decoding.py](../../ExtractESG/extraction/src/esg_extraction/models/schema_decoding.py) | Explicit decoding projection; the task receiver retains the full contract. |
| [ExtractESG/extraction/src/esg_extraction/models/structured_template.py](../../ExtractESG/extraction/src/esg_extraction/models/structured_template.py) | Compile a small output contract for each provider's actual prompt protocol. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/__init__.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/__init__.py) | One extraction backbone, independent clause goals, two produced data kinds. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/adapters/__init__.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/adapters/__init__.py) | Local implementations of pipeline ports; storage locations stay here. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/adapters/artifacts.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/adapters/artifacts.py) | Artifacts are a producer-owned store addressed by run and artifact IDs. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/adapters/document.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/adapters/document.py) | HTTP document adapter: only IR IDs and stable evidence object references cross services. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/adapters/local_model.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/adapters/local_model.py) | Local general-purpose Qwen VLM; no NuExtract-specific template tokens. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/api.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/api.py) | Current extraction and independent clause API. No package/version routes. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/binding.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/binding.py) | Evidence binding shared by every data reader. No clause identities are authored here. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/decisions.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/decisions.py) | Model proposals name evidence; they cannot supply numerical values. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/delivery.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/delivery.py) | Automatic, idempotent raw-data outbox. Database acceptance is not publishing. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/engine.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/engine.py) | One backbone. Clause targets are values; readers differ only by data kind. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/enrichment.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/enrichment.py) | Read clause-declared attributes after capturing values; never regenerate values. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/exporting.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/exporting.py) | Immutable, self-contained export. Views and filesystem layout are not APIs. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/frames.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/frames.py) | Subdivide bounded evidence without recomputing values or changing locations. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/organization.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/organization.py) | Project clause-owned result layouts over referenced source facts. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/period_binding.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/period_binding.py) | Bind local time evidence; a report's main year is never a default identity. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/ports.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/ports.py) | Backbone dependencies are value interfaces; implementations own I/O. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/reading_frame.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/reading_frame.py) | Compact model input; full physical frames remain immutable binding evidence. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/reading_policy.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/reading_policy.py) | Bounded reading and task-local progress, shared by both data readers. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/result_store.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/result_store.py) | Producer-owned facts with a lightweight index; only changed records are written. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/retrieval.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/retrieval.py) | Stream clause candidates; relax object terms when the target has not been found. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/runtime.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/runtime.py) | Composition root: filesystem, HTTP and model paths stop here. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/selection.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/selection.py) | Select reading candidates from clause-owned rules before a model call. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/state.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/state.py) | Current run state. Atomic transitions, frozen targets and no historical-route adapter. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/statistics.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/statistics.py) | Incremental run statistics. No facts, evidence or publication decisions are rewritten. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/structure.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/structure.py) | Physical columns, with no report-year filter or target-specific vocabulary. |
| [ExtractESG/extraction/src/esg_extraction/pipeline/worker.py](../../ExtractESG/extraction/src/esg_extraction/pipeline/worker.py) | One current run executed by the unified document queue. |
| [ExtractESG/extraction/tests/conftest.py](../../ExtractESG/extraction/tests/conftest.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_backbone.py](../../ExtractESG/extraction/tests/test_backbone.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_boundaries.py](../../ExtractESG/extraction/tests/test_boundaries.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_capture_semantics.py](../../ExtractESG/extraction/tests/test_capture_semantics.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_clause_api.py](../../ExtractESG/extraction/tests/test_clause_api.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_exporting.py](../../ExtractESG/extraction/tests/test_exporting.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_frame_budget.py](../../ExtractESG/extraction/tests/test_frame_budget.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_organization.py](../../ExtractESG/extraction/tests/test_organization.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_report_resources.py](../../ExtractESG/extraction/tests/test_report_resources.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_result_contract.py](../../ExtractESG/extraction/tests/test_result_contract.py) | Delivery validation is independent of the report parser, clause repository and reader. |
| [ExtractESG/extraction/tests/test_run_observation.py](../../ExtractESG/extraction/tests/test_run_observation.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_search_pages.py](../../ExtractESG/extraction/tests/test_search_pages.py) | 测试/样例 |
| [ExtractESG/extraction/tests/test_structure_contract.py](../../ExtractESG/extraction/tests/test_structure_contract.py) | 测试/样例 |

## ExtractESG/pipeline

| 文件 | 源码说明 |
|---|---|
| [ExtractESG/pipeline/src/esg_pipeline/__init__.py](../../ExtractESG/pipeline/src/esg_pipeline/__init__.py) | Durable local control plane for serial pipeline execution. |
| [ExtractESG/pipeline/src/esg_pipeline/context.py](../../ExtractESG/pipeline/src/esg_pipeline/context.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/pipeline/src/esg_pipeline/contracts.py](../../ExtractESG/pipeline/src/esg_pipeline/contracts.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/pipeline/src/esg_pipeline/execution_api.py](../../ExtractESG/pipeline/src/esg_pipeline/execution_api.py) | Queue history and resumable event transport; no worker is owned by a stream. |
| [ExtractESG/pipeline/src/esg_pipeline/extraction.py](../../ExtractESG/pipeline/src/esg_pipeline/extraction.py) | PDF preparation → independent clause run. Orchestration owns neither reader nor clauses. |
| [ExtractESG/pipeline/src/esg_pipeline/hooks.py](../../ExtractESG/pipeline/src/esg_pipeline/hooks.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/pipeline/src/esg_pipeline/native_dispatch.py](../../ExtractESG/pipeline/src/esg_pipeline/native_dispatch.py) | Close the native-state → queue crash gap without changing artifact IDs. |
| [ExtractESG/pipeline/src/esg_pipeline/outcomes.py](../../ExtractESG/pipeline/src/esg_pipeline/outcomes.py) | Shared execution/result axes derived from durable stage artifacts. |
| [ExtractESG/pipeline/src/esg_pipeline/plan_lifecycle.py](../../ExtractESG/pipeline/src/esg_pipeline/plan_lifecycle.py) | Durable parent lifecycle commands across the platform and extraction domain. |
| [ExtractESG/pipeline/src/esg_pipeline/progress.py](../../ExtractESG/pipeline/src/esg_pipeline/progress.py) | Compatibility imports for the single shared workbench progress contract. |
| [ExtractESG/pipeline/src/esg_pipeline/report_processing.py](../../ExtractESG/pipeline/src/esg_pipeline/report_processing.py) | Durable report identities and OCR → IR preparation above the single queue. |
| [ExtractESG/pipeline/src/esg_pipeline/scheduler.py](../../ExtractESG/pipeline/src/esg_pipeline/scheduler.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/pipeline/src/esg_pipeline/secrets.py](../../ExtractESG/pipeline/src/esg_pipeline/secrets.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/pipeline/src/esg_pipeline/shared_contracts.py](../../ExtractESG/pipeline/src/esg_pipeline/shared_contracts.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/pipeline/src/esg_pipeline/source_references.py](../../ExtractESG/pipeline/src/esg_pipeline/source_references.py) | Durable references owned by the current extraction coordinator. |
| [ExtractESG/pipeline/src/esg_pipeline/store.py](../../ExtractESG/pipeline/src/esg_pipeline/store.py) | 源码/配置；职责见模块设计 |
| [ExtractESG/pipeline/src/esg_pipeline/task_references.py](../../ExtractESG/pipeline/src/esg_pipeline/task_references.py) | One typed view of queue references retained by execution owners. |
| [ExtractESG/pipeline/src/esg_pipeline/worker.py](../../ExtractESG/pipeline/src/esg_pipeline/worker.py) | 源码/配置；职责见模块设计 |

## ExtractESG/pyproject.toml

| 文件 | 源码说明 |
|---|---|
| [ExtractESG/pyproject.toml](../../ExtractESG/pyproject.toml) | 源码/配置；职责见模块设计 |

## ExtractESG/scripts

| 文件 | 源码说明 |
|---|---|
| [ExtractESG/scripts/evaluate_scientific.py](../../ExtractESG/scripts/evaluate_scientific.py) | Evaluate a frozen result through its API against explicitly annotated evidence. |

## ExtractESG/shared_runtime

| 文件 | 源码说明 |
|---|---|
| [ExtractESG/shared_runtime/pyproject.toml](../../ExtractESG/shared_runtime/pyproject.toml) | 源码/配置；职责见模块设计 |
| [ExtractESG/shared_runtime/src/esg_runtime/__init__.py](../../ExtractESG/shared_runtime/src/esg_runtime/__init__.py) | Small, dependency-free contracts shared by extraction runtime services. |
| [ExtractESG/shared_runtime/src/esg_runtime/execution_slot.py](../../ExtractESG/shared_runtime/src/esg_runtime/execution_slot.py) | Shared inherited model-execution flock; no application or source-directory ownership. |
| [ExtractESG/shared_runtime/src/esg_runtime/layout.py](../../ExtractESG/shared_runtime/src/esg_runtime/layout.py) | Composition-root paths. Discover the checkout without loading clause data. |
| [ExtractESG/shared_runtime/src/esg_runtime/lifecycle.py](../../ExtractESG/shared_runtime/src/esg_runtime/lifecycle.py) | Reference checks shared by report, extraction and artifact lifecycles. |
| [ExtractESG/shared_runtime/src/esg_runtime/local_operator.py](../../ExtractESG/shared_runtime/src/esg_runtime/local_operator.py) | Local request identity; no credentials or dependency on the publication service. |
| [ExtractESG/shared_runtime/src/esg_runtime/model_calls.py](../../ExtractESG/shared_runtime/src/esg_runtime/model_calls.py) | Shared execution envelope; consumers retain ownership of output semantics. |
| [ExtractESG/shared_runtime/src/esg_runtime/power.py](../../ExtractESG/shared_runtime/src/esg_runtime/power.py) | Worker-owned idle sleep prevention, without blocking explicit system sleep. |
| [ExtractESG/shared_runtime/src/esg_runtime/process.py](../../ExtractESG/shared_runtime/src/esg_runtime/process.py) | Process identity checks never treat an observation failure as termination. |
| [ExtractESG/shared_runtime/src/esg_runtime/progress.py](../../ExtractESG/shared_runtime/src/esg_runtime/progress.py) | Typed business progress; logs and model budgets are never completion units. |
| [ExtractESG/shared_runtime/src/esg_runtime/release.py](../../ExtractESG/shared_runtime/src/esg_runtime/release.py) | Boot-frozen release identity and read-only compatibility checks. |
| [ExtractESG/shared_runtime/src/esg_runtime/trash.py](../../ExtractESG/shared_runtime/src/esg_runtime/trash.py) | Recoverable, journalled quarantine for owned artifacts. |
| [ExtractESG/shared_runtime/tests/test_lifecycle.py](../../ExtractESG/shared_runtime/tests/test_lifecycle.py) | 测试/样例 |
| [ExtractESG/shared_runtime/tests/test_model_calls.py](../../ExtractESG/shared_runtime/tests/test_model_calls.py) | 测试/样例 |

## PlatESG

| 文件 | 源码说明 |
|---|---|
| [PlatESG/backend/__init__.py](../../PlatESG/backend/__init__.py) | PlatESG read API and authenticated curation endpoints. |
| [PlatESG/backend/evidence_resolver.py](../../PlatESG/backend/evidence_resolver.py) | Evidence interface consumer. Never opens an extraction/IR directory. |
| [PlatESG/backend/http_prefix.py](../../PlatESG/backend/http_prefix.py) | Stable public /api prefix for development proxy and standalone service. |
| [PlatESG/backend/main.py](../../PlatESG/backend/main.py) | Current published-data platform. No legacy dictionary, SQL worker or frozen API. |
| [PlatESG/backend/products.py](../../PlatESG/backend/products.py) | Plat consumes published-only HTTP products; no capture database or producer directory. |
| [PlatESG/backend/runtime.py](../../PlatESG/backend/runtime.py) | Publication owns its release; no extraction source tree or sys.path mutation. |
| [PlatESG/frontend/index.html](../../PlatESG/frontend/index.html) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/package-lock.json](../../PlatESG/frontend/package-lock.json) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/package.json](../../PlatESG/frontend/package.json) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/App.test.tsx](../../PlatESG/frontend/src/App.test.tsx) | 测试/样例 |
| [PlatESG/frontend/src/App.tsx](../../PlatESG/frontend/src/App.tsx) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/api.test.ts](../../PlatESG/frontend/src/api.test.ts) | 测试/样例 |
| [PlatESG/frontend/src/api.ts](../../PlatESG/frontend/src/api.ts) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/components/AppShell.tsx](../../PlatESG/frontend/src/components/AppShell.tsx) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/components/ProductLink.tsx](../../PlatESG/frontend/src/components/ProductLink.tsx) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/components/PublishedData.tsx](../../PlatESG/frontend/src/components/PublishedData.tsx) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/components/Ui.tsx](../../PlatESG/frontend/src/components/Ui.tsx) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/hooks/useResource.ts](../../PlatESG/frontend/src/hooks/useResource.ts) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/main.tsx](../../PlatESG/frontend/src/main.tsx) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/navigation.ts](../../PlatESG/frontend/src/navigation.ts) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/styles.css](../../PlatESG/frontend/src/styles.css) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/src/types.ts](../../PlatESG/frontend/src/types.ts) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/tsconfig.json](../../PlatESG/frontend/tsconfig.json) | 源码/配置；职责见模块设计 |
| [PlatESG/frontend/vite.config.ts](../../PlatESG/frontend/vite.config.ts) | 源码/配置；职责见模块设计 |
| [PlatESG/scripts/run.sh](../../PlatESG/scripts/run.sh) | 源码/配置；职责见模块设计 |
| [PlatESG/tests/test_published_products.py](../../PlatESG/tests/test_published_products.py) | Current service boundary; no SQL files, old fixtures or running services. |

## database

| 文件 | 源码说明 |
|---|---|
| [database/contracts/package.json](../../database/contracts/package.json) | 源码/配置；职责见模块设计 |
| [database/migrations/0019_capture_ingestion.sql](../../database/migrations/0019_capture_ingestion.sql) | 源码/配置；职责见模块设计 |
| [database/migrations/capture/0020_products.sql](../../database/migrations/capture/0020_products.sql) | 源码/配置；职责见模块设计 |
| [database/migrations/capture/0021_source_lifecycle.sql](../../database/migrations/capture/0021_source_lifecycle.sql) | 源码/配置；职责见模块设计 |
| [database/migrations/capture/0022_product_previews.sql](../../database/migrations/capture/0022_product_previews.sql) | 源码/配置；职责见模块设计 |
| [database/migrations/capture/0023_projection_archives.sql](../../database/migrations/capture/0023_projection_archives.sql) | 源码/配置；职责见模块设计 |
| [database/package.json](../../database/package.json) | 源码/配置；职责见模块设计 |
| [database/pnpm-lock.yaml](../../database/pnpm-lock.yaml) | 源码/配置；职责见模块设计 |
| [database/pyproject.toml](../../database/pyproject.toml) | 源码/配置；职责见模块设计 |
| [database/queries/capture_products.mjs](../../database/queries/capture_products.mjs) | 源码/配置；职责见模块设计 |
| [database/queries/capture_publication.mjs](../../database/queries/capture_publication.mjs) | 源码/配置；职责见模块设计 |
| [database/queries/capture_sources.mjs](../../database/queries/capture_sources.mjs) | 源码/配置；职责见模块设计 |
| [database/queries/capture_workflow.mjs](../../database/queries/capture_workflow.mjs) | 源码/配置；职责见模块设计 |
| [database/queries/captures.mjs](../../database/queries/captures.mjs) | 源码/配置；职责见模块设计 |
| [database/queries/ingestion.mjs](../../database/queries/ingestion.mjs) | 源码/配置；职责见模块设计 |
| [database/runtime/canonical.mjs](../../database/runtime/canonical.mjs) | 源码/配置；职责见模块设计 |
| [database/runtime/capture_worker.mjs](../../database/runtime/capture_worker.mjs) | 源码/配置；职责见模块设计 |
| [database/runtime/connection.mjs](../../database/runtime/connection.mjs) | 源码/配置；职责见模块设计 |
| [database/runtime/manage.mjs](../../database/runtime/manage.mjs) | 源码/配置；职责见模块设计 |
| [database/runtime/rpc.mjs](../../database/runtime/rpc.mjs) | 源码/配置；职责见模块设计 |
| [database/runtime/schema.mjs](../../database/runtime/schema.mjs) | 源码/配置；职责见模块设计 |
| [database/scripts/export_contracts.py](../../database/scripts/export_contracts.py) | Publish database-owned value contracts; consumers install the generated types SDK. |
| [database/scripts/run-node.sh](../../database/scripts/run-node.sh) | 源码/配置；职责见模块设计 |
| [database/src/esg_database/__init__.py](../../database/src/esg_database/__init__.py) | 源码/配置；职责见模块设计 |
| [database/src/esg_database/api.py](../../database/src/esg_database/api.py) | Independent local ingestion service. Receipt precedes any review/publication. |
| [database/src/esg_database/client.py](../../database/src/esg_database/client.py) | One long-lived, serialized SQL adapter; bounded RPC, no per-request shell. |
| [database/src/esg_database/contracts.py](../../database/src/esg_database/contracts.py) | Database-owned query contracts. Producer facts are consumed, not redefined. |
| [database/src/esg_database/http_client.py](../../database/src/esg_database/http_client.py) | Credential-free service port for Console and Plat; no sibling filesystem access. |
| [database/src/esg_database/ingestion.py](../../database/src/esg_database/ingestion.py) | Receive a sealed archive through a value contract, never a producer directory. |
| [database/src/esg_database/operator.py](../../database/src/esg_database/operator.py) | Explicit local control, independent of Plat and stored credentials. |
| [database/src/esg_database/products_api.py](../../database/src/esg_database/products_api.py) | Internal control and published-only read ports over the same immutable products. |
| [database/src/esg_database/products_contracts.py](../../database/src/esg_database/products_contracts.py) | Database-owned observation and publication ports. All periods remain addressable. |
| [database/src/esg_database/projection.py](../../database/src/esg_database/projection.py) | Deterministic organization of admitted facts, never another extraction or model call. |
| [database/src/esg_database/projector.py](../../database/src/esg_database/projector.py) | Post-receipt projection can fail or retry independently of raw reception. |
| [database/src/esg_database/research.py](../../database/src/esg_database/research.py) | Typed long-table research exports. Display matrices are never a data authority. |
| [database/src/esg_database/selection.py](../../database/src/esg_database/selection.py) | Versioned, explicit source selection. Unknown identity always keeps its sources. |
| [database/src/esg_database/source_contracts.py](../../database/src/esg_database/source_contracts.py) | Reference IDs are provenance; only the PDF blob remains an external dependency. |
| [database/src/esg_database/sources_api.py](../../database/src/esg_database/sources_api.py) | Source lifecycle read/reservation port; no producer directories or credentials. |
| [database/tests/capture-ingestion.test.mjs](../../database/tests/capture-ingestion.test.mjs) | 测试/样例 |
| [database/tests/capture-pagination.test.mjs](../../database/tests/capture-pagination.test.mjs) | 测试/样例 |
| [database/tests/capture-schema.test.mjs](../../database/tests/capture-schema.test.mjs) | 测试/样例 |
| [database/tests/capture-sources.test.mjs](../../database/tests/capture-sources.test.mjs) | 测试/样例 |
| [database/tests/layout.test.mjs](../../database/tests/layout.test.mjs) | 测试/样例 |
| [database/tests/test_capture_products.py](../../database/tests/test_capture_products.py) | Real isolated SQL and HTTP boundaries; these fixtures are synthetic, not report accuracy evidence. |
| [database/tests/test_capture_worker.py](../../database/tests/test_capture_worker.py) | 测试/样例 |
| [database/tests/test_ingestion_api.py](../../database/tests/test_ingestion_api.py) | 测试/样例 |

## tools

| 文件 | 源码说明 |
|---|---|
| [tools/audit_structure.py](../../tools/audit_structure.py) | Inventory first-party files and enforce module boundaries without reading private data. |
| [tools/build_documentation.py](../../tools/build_documentation.py) | Generate source and HTTP references without importing services or reading runtime data. |
| [tools/check_contracts.py](../../tools/check_contracts.py) | Validate/reindex independent clauses and publish machine-readable module contracts. |
| [tools/contract_types.py](../../tools/contract_types.py) | Development-only JSON Schema to TypeScript renderer shared by module exporters. |
| [tools/export_console_types.py](../../tools/export_console_types.py) | Export Console's data types from authoritative Python contracts. |
| [tools/verify_standalone.py](../../tools/verify_standalone.py) | Install SDK wheels outside the checkout and start the current HTTP consumer. |
| [tools/verify_worker.py](../../tools/verify_worker.py) | Execute the installed worker outside the checkout against an explicit empty document port. |
