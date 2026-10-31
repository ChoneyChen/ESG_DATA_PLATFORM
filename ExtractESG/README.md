# PDF → 独立原值

主干只有一条，按条款的 `data_kind` 选择定量或定性读取协议。条款源和 SDK 在 `ClauseESG`；本模块不维护第二份指标目录。真实测量/陈述接收后，属性、目标关系、比较资格分别判断；缺失单位或可选维度不抹掉原值。

`contracts` 和 `shared_runtime` 是各自安装的轻量 SDK；`pyproject.toml` 安装 document、pipeline、extraction。Console 应用组合在 `ConsoleESG/backend`，数据库连接和接收器在 `database`。部署可以同进程组合，不以工作目录充当跨服务接口。

```sh
bash scripts/install_local.sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 scripts/start.sh
.local/python/bin/python ../tools/check_contracts.py
.local/python/bin/python ../tools/audit_structure.py
.local/python/bin/python -m pytest document/tests extraction/tests -q
```

安装脚本离线使用已有依赖，不下载模型。开发 Python 为 `.local/python`，本地 MLX Python 用 `ESG_MODEL_PYTHON`；单服务入口 `scripts/run_service.sh document|extraction|console|database`。默认文档服务是 Console 与文档能力的应用组合。

[架构与公共端口](../docs/architecture/README.md) · [模型合同](../docs/architecture/models.md) · [数据目录](../docs/guides/local-operation.md) · [导出接收](../docs/architecture/delivery.md)
