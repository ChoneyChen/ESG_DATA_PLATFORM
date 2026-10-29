# ESG Data Platform

当前后端采用独立条款、两类原值事实和独立报告资源合同。实施与待核对项见[本轮逐条报告](模块化重构与逐条核验报告.md)，接口见[模块规范](docs/CLAUSE_BACKBONE_REBUILD.md)。原始[全链路检查报告与升级规划](全链路检查报告与升级规划.md)保留为需求依据。

## 当前范围

这轮重构覆盖 **PDF → 文档证据 → 按条款读取 → 原值事实与条款映射**。旧标准包编译、foundation/standard 两条生产路线、Core 结果转换和历史任务适配已退出。

数据库与已有公开产品保留，新格式的数据库接收器不在这一轮实现。新产物不会自动交付或发布，`PLATESG_AUTO_PUBLISH=0`。Console 本轮未重新设计，旧标准／创建表单所用接口已退役；当前通过两个后端的 `/docs` 使用新链路。

## 模块

| 目录 / 模块 | 职责 |
|---|---|
| `ExtractESG/standard/<组>/<条款>/clause.json` | 独立条款的唯一源：定义、类别、词典、口径、单位、阅读和证据要求 |
| `ExtractESG/contracts/src/esg_clauses` | 条款结构、规则解释、独立读写、选择和冻结 |
| `ExtractESG/contracts/src/esg_targeted/data_kinds` | 定量原值、定性原文两份数据合同 |
| `ExtractESG/contracts/src/esg_targeted/pipeline_contracts.py` | 来源事实、身份、证据、映射、覆盖与任务参数 |
| `ExtractESG/contracts/src/esg_reports` | PDF 编号、企业、发表年份、行业、目录导入及筛选 |
| `ExtractESG/document_processing` | 文字层优先、本地 OCR、IR、物理对象与证据端口 |
| `ExtractESG/extraction/src/esg_targeted/pipeline` | 单一提取骨干；文档、模型、产物通过端口连接 |
| `ExtractESG/control_plane` | PDF 准备、工作队列、当前运行、取消 |
| `ConsoleESG` | 上轮操作界面；新合同的页面接入仍待独立更新 |
| `PlatESG`、`database` | 已有审核、发布、公开数据和存储；本轮不迁移数据 |

组目录只有展示信息。`base16 / priority32 / quantitative / universal / platform_major` 是 `standard/selections.json` 中的 ID 列表，不对应不同提取算法。

## 启动

```sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 ExtractESG/scripts/start_local_workbenches.sh
```

| 入口 | 地址 |
|---|---|
| 文档与编排 API | http://127.0.0.1:18080/docs |
| 条款与提取 API | http://127.0.0.1:18180/docs |
| Console | http://127.0.0.1:18081/ |
| 既有公开产品 | http://127.0.0.1:8765/ |

模型仅使用本地适配器。条款数据修改不要求重启引擎；执行代码和接口类型变化要求重启。运行冻结条款、文档引用与引擎指纹，拒绝静默改用变更后的输入。

## 使用

1. `GET /api/report-resources` 按企业、行业、发表年份或覆盖年度筛选 PDF，取得 `asset_id`。
2. 文档服务 `POST /api/report-sessions/preview` 预览 `input + clause_keys`，或选一个 `selection`。
3. `POST /api/report-sessions` 创建任务；服务按这些独立条款的词典选择页面并进入统一队列。
4. 读取 `/{id}` 的状态及 `/{id}/facts` 的产物。原值、条款映射、范围确认分别保存。

原值非空且来源可绑定是核心准入要求。单位、期间、主体、解析结果可以待确认；未知属性归入扩展字段，不能把缺失当成零，也不能把条款预期口径复制成报告实际口径。

[目录批量导入规范与模板](ExtractESG/contracts/examples/report-batch/README.md)：`manifest.json + pdf/`。新批次明确填写企业、发表年份和行业，文件通过完整 SHA-256 校验；冲突不覆盖，重复提交幂等。

## 核对

```sh
ExtractESG/document_ir_extract/backend/.venv/bin/python ExtractESG/scripts/check_contracts.py
ExtractESG/document_ir_extract/backend/.venv/bin/python -m pytest ExtractESG/extraction/tests -q
```

当前条款结构共 99 个展示组、1,126 个原编号。其中 37 个原条目的值类别／结构含义或单位仍有未决问题，明确标记为 `blocked`，不会退回旧路线。可选状态不等于真实样本验收通过。

原始 PDF 与报告身份资料保留。旧任务、检查点和旧 OCR／IR 衍生产物已清理。`CrawlESG/` 仍是独立保护区，本链路不读取、不修改。
