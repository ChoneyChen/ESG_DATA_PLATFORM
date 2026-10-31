# 独立导出与数据库接收

当前源码交接为 `extraction-export-1.1` 与 `extraction-receipt-1.0`；实际部署状态见[本轮执行记录](../implementation/2026-10-31/README.md)。旧字典、旧快照读适配与旧SQL发布体系已清除。

结束的提取先保存原值，再自动封存。ZIP包含 manifest 与 facts、properties、evidence、relations、coverage、targets、document、diagnostics 八份JSON。清单绑定引擎、文档修订、完整PDF散列、冻结条款、文件摘要/计数；不用 producer目录或bundle_path。组织矩阵不是数据库身份。

数据库接收器校验一次传输清单与来源事实格式：可选维度缺失仍接收；格式坏行有独立处置，好行保留；封存包整体损坏不能假装成功。facts是唯一原值权威；属性/字段证据通过共享合同SDK重建，来件投影不一致时记录诊断并采用重建结果。原字串不变，数值用SQL numeric精确保存。回执接收数加拒绝数必须与清单事实数一致，目标、原文身份、资源身份分别存。

`capture_exports / captured_facts / capture_export_facts / capture_clause_targets / capture_clause_relations / captured_properties / captured_evidence` 归数据库模块。接收幂等，同export ID不同内容冲突；断网后的未知结果先查回执再重发，同一内容不会重复入库。永久4xx停止重试，连接故障自动重试；不丢原值，不自动发布。

接收器独立18280，本机默认原值库 `$ESG_DATA_ROOT/databases/capture`。新库显式管理初始化：

```sh
ESG_DATABASE_DATA_DIR=/absolute/new/capture database/scripts/run-node.sh database/runtime/manage.mjs init --profile capture
```

这条命令只接受显式目标和当前受管模式；不接管旧模式。应用worker启动只检查模式，不运行迁移和字典登记。桌面及工作台启动器不初始化或迁移数据库，缺少原值库时显示显式配置方法；旧数据清理和实际部署状态单独登记，启动不隐式搬迁。

新增原值接收和既有审核/发布是两层。原值接收不把新条款静默接成旧字典指标，不借显示矩阵聚合，不从来源缺失推断未披露。后续经批准的科学观测/产品适配应消费冻结条款和原值证据，决定有操作者、时间与血缘。当前 `PLATESG_AUTO_PUBLISH=0`。
