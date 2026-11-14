# 数据、存储与发布设计

适用RQ-03—05、10、11、13。各模块拥有自己的存储；跨模块交接见[合同](04-contracts.md)。本章描述结构，不声称某个活动库当前有多少记录。

## 目录与权威

默认数据根为`$ESG_DATA_ROOT`，未指定时是仓库`.local/data`。源码内JSON条款/Schema/迁移是部署资产；PDF、运行、日志、数据库不放进源码或docs。

```text
$ESG_DATA_ROOT/
  reports/
    blobs/                     原PDF字节
    registry.sqlite3           报告资源与文件夹登记
  documents/
    uploads/                   文档接入
    ocr/<ocr_run_id>/           文档模块拥有的OCR包
    ir/<ir_run_id>/             文档模块拥有的IR包/修订
  extractions/<run_id>/
    input/                     请求、冻结条款、搜索计划、阅读策略
    evidence/                  本任务实际使用的物理帧
    facts/                     单事实记录
    relations/                 单条款关系
    results/                   状态、检查点、索引及最终汇总
    diagnostics/               调用及候选诊断
    exports/                   自包含交接包
  control/                     队列、任务、作业状态、workbench进程登记
  logs/                        各服务与launcher日志
  databases/capture/           默认独立PGlite数据位置
```

精确子路径由[layout.py](../ExtractESG/shared_runtime/src/esg_runtime/layout.py)、[document config](../ExtractESG/document/src/esg_document/config.py)、[结果仓储](../ExtractESG/extraction/src/esg_extraction/pipeline/result_store.py)维护。提取运行SQLite控制库是`control/extractions.sqlite3`；队列是`control/queue.sqlite3`，不是业务原值库。索引可重建，已保存事实ID及冻结输入必须保留其含义。

开发临时结果位于`.local/development/`或显式隔离`ESG_DATA_ROOT`，与生产数据根分开。运行目录是内部实现，其他服务不可自行扫描。

## 物理包

OCR和IR以manifest.json为唯一包入口，entrypoints解析相对成员，integrity/files.json保存成员大小/摘要。IR保存canonical物理对象、逻辑表/续表、观察、图像、质量、复核及当前完整快照。物理包与控制状态分开。

OCR输入变换和页号映射有记录，不改变原PDF散列。修复产出新修订。源事实引用准确IR身份、页、对象及摘录；文档来源解析器负责回读内部成员，消费者只用端口。

## 独立数据库结构

SQL权威为`database/migrations/`，连接/RPC归`database/runtime/`，查询和事务归`database/queries/`。Python接收及投影只调用本模块桥，不把SQL分散给Console或Plat。

| 层 | 主要表 | 用途 |
|---|---|---|
| 接收 | capture_exports、captured_facts、capture_export_facts | 包、原值及包成员；重复交接幂等 |
| 合同/关系 | capture_clause_targets、capture_clause_relations | 冻结条款和事实目标关系 |
| 字段证据 | captured_properties、captured_evidence | 原值身份、开放维度、对应来源 |
| 投影 | capture_projection_attempts、capture_projections、capture_product_definitions、capture_observations、capture_projection_members | 确定性观测、定义及成员、失败状态 |
| 选源/产品 | capture_restatements、capture_dataset_previews、capture_dataset_versions、capture_dataset_projections、capture_dataset_members、capture_dataset_events | 重述链接、预览决定、发布快照和事件 |
| 生命周期 | capture_source_locks、capture_source_reservations、capture_projection_archives | 来源清理互斥、预留和已删除导航 |

迁移0019是原值，capture/0020产品、0021来源、0022预览、0023投影归档。迁移内容不可原地改写；新增模式由新迁移表达。启动只检查模式，不自动初始化、迁移或接管旧表。

## 接收与投影

接收器解读ZIP清单、成员和两类值，保存原文及字段证据，返回accepted/partial/rejected及逐项处置。缺公司、期间、单位或边界不能当作拒绝真实原值的理由。当前解包上限由ingestion.py的MAX_ARCHIVE_BYTES定义（128MiB），不能返回悄悄截断的成功包。

后台ProjectionWorker读取已接收的不可变输入，由projection.py生成观测及冻结产品定义。一个事实对多个条款的关系分别形成观测；物理occurrence身份辅助说明重复来源，不借此静默删掉跨报告原值。投影失败可重试，原值仍可查询。

比较资格单独表达eligible/reasons；缺身份、未知期间、未解析数值或关系非match等可能不能比较，但观测仍保留。目标固定轴和报告实际分类分别保存，历史实际值不自动变成baseline。

## 预览、选源、发布

| 政策 | 行为 |
|---|---|
| all_sources | 按选择的关系保留所有来源 |
| latest_report | 对比较身份明确的同口径组按发表年选源；身份或发表年未知继续保留，并列最新保留并列 |
| prefer_explicit_restated | 只沿明确重述链接采用较新观测，旧值仍留数据库 |

选择政策默认仅match关系，可明确选择其他关系。重述需指向同冻结定义、同已知期间，已知身份不能冲突；不能用“年份更新”覆盖不同范围。

预览持久保存projection摘要、选源决定及preview_digest，成员分页直接消费该决定。发布请求带同一摘要、标题、projection_ids、政策和reason；内容变化需重新预览。发布建立不可变成员快照，不反向重算旧产品。

发布状态published/withdrawn与资源、接收、投影状态独立。撤下记录理由和修订，不删除原值；恢复公开应按产品流程明确处理，不能由页面显示切换替代数据库决定。`PLATESG_AUTO_PUBLISH=0`，接收与投影不自动发布。

## 公开读取与科研导出

Plat及数据库公开/v2接口只读有效发布。PDF先确认属于当前发布来源，再经文档散列端口读取字节；撤下后不能继续通过Plat公开取得成员或PDF。

科研导出合同为capture-research-1.1，六份长表：facts、properties、evidence、coordinates、relations、sources，并附定义/类型清单。开放属性按叶子路径展平，不塞JSON单元格；坐标独立为子表。所有期间和未知期间保留。

CSV保留精确十进制字符串；Parquet按列选择decimal128/256，超出精度或容量明确失败，不四舍五入、转浮点或输出半包。展示矩阵仅是浏览视图，不作为入库或科研权威。

## 删除、保留与备份

资源归档/恢复/清除、提取移入已删除/恢复/彻底删除、投影归档及产品撤下是不同操作。通过所属模块接口执行：运行中任务不能清除；被发布快照引用的投影不能单独彻底删除；有已接收事实引用的PDF受来源保护。IR/OCR是否可清理由引用与可重建状态决定，不按目录名批量删除。

来源清理预留与接收在数据库协调，清除后迟到的交付应明确拒绝。备份需同时覆盖原PDF/登记、冻结输入与结果、数据库及必要控制记录；PGlite停写关闭后复制完整目录，外部PostgreSQL采用其一致性备份工具。本仓库没有统一自动备份/跨机恢复命令，属于[待办](10-quality-and-work.md)，不能把导出CSV当完整备份。
