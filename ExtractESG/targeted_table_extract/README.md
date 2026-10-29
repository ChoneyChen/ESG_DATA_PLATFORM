此目录只保留本地模型服务的启动环境。提取源码位于 `../extraction/src/esg_targeted/pipeline/`，条款源位于 `../standard/`，合同位于 `../contracts/`。

启动：`scripts/run_backend.sh`。当前 API：`http://127.0.0.1:18180/docs`。

旧的 foundation、standard、聚合包执行、Core 结果转换、共享阅读缓存、历史任务适配均已删除。新任务使用独立条款 ID 或 `standard/selections.json` 中的 ID 集合。
