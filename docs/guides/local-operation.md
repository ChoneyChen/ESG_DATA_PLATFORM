# 本地运行与目录规则

## 启动

仓库根执行 `bash ConsoleESG/scripts/install_local.sh` 安装六个可编辑模块；已有本地依赖下无网络下载。模型目录和MLX运行时显式配置。随后：

```sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 ConsoleESG/scripts/start.sh
```

五个本机服务统一管理：文档/Console组合18080、提取18180、Console18081、独立数据库18280、Plat8765。启动不读取凭据文件，自动发布关闭。

## 数据位置

```text
$ESG_DATA_ROOT/                     默认仓库 .local/data
  reports/blobs/<full-sha256>.pdf
  reports/registry.sqlite3
  documents/{ocr,ir,uploads}/
  extractions/<run_id>/
    input/                         冻结目标、来源、预算、搜索和代码身份
    facts/                         每条独立事实
    results/                       事实集与组织投影
    diagnostics/                   候选处置、调用回执
    exports/<export_id>/           不可变交接清单和数据成员
  control/                         队列、会话、运行SQL、准备任务与清理记录
  logs/                            document/extraction/console/database/plat
  databases/capture/               当前原值、观测与发布库
```

源码不保存业务产物。`ExtractESG/.local/python`是依赖环境，不是运行数据。当前PDF是独立输入；报告元数据主年不限制事实年份。物理OCR/IR可按实际散列、计划和版本复用，每条每次重新理解。

2026-10-31已按用户明确要求停机清理旧库、备份、控制记录、处理缓存、提取产物和日志；不保留旧快照读取适配。清理清单在[运行数据记录](../implementation/2026-10-31/runtime-cleanup.json)，只记录路径/字节数，不复制数据或凭据。当前完整PDF运行重新生成处理证据。

新库须显式指定目标后初始化，应用不接管旧模式：

```sh
ESG_DATABASE_DATA_DIR=/absolute/new/capture database/scripts/run-node.sh database/runtime/manage.mjs init --profile capture
```

当前任务结束后可归档，不改变其已交付事实；删除当前来源仍由所属模块核对引用及活动任务，不能绕过引用检查。

## 核验

```sh
ExtractESG/.local/python/bin/python tools/check_contracts.py
ExtractESG/.local/python/bin/python tools/audit_structure.py
npm --prefix ConsoleESG/frontend test
npm --prefix ConsoleESG/frontend run build
npm --prefix database test
```

合同修改由 `check_contracts.py --write`生成Schema、索引和TypeScript类型，再普通检查。读取健康应同时检查 `restart_required` 和业务就绪，不能只看HTTP200。当前控制器以 `start.sh status` 查看，`start.sh stop` 停止，`start.sh restart` 重启；只停止本入口托管进程，有活动提取时先在Console结束/取消。不要因读取超时重开任务或模型。

源码并列目录按所有权命名；新规范在docs/architecture，操作在guides，一次实施与机器证据在implementation/日期，问题基线在audits/日期，原需求与历史记录单独标识。没有“final2/new2/latest”多份当前说明。

## 桌面入口

运行 `ExtractESG/.local/python/bin/python ConsoleESG/scripts/install_desktop_launcher.py` 更新桌面“启动ESG统一工作台.command”。入口原子替换，不保留旧启动器备份。仓库移动后可设置ESG_PROJECT_ROOT或重新安装入口。双击默认后台启动并打开页面，不需要终端一直开着。

```sh
ConsoleESG/scripts/start.sh status --json
ConsoleESG/scripts/start.sh stop
ConsoleESG/scripts/start.sh restart --no-browser
```

入口、终端和自动化共用workbench.py。已有当前服务直接复用；更换控制器或服务版本时，先确认无活动任务再替换其托管进程。PID与启动时刻防止PID复用误停；不按端口或模糊名称杀进程，外部占用明确报错。命令锁与supervisor锁避免两套后台实例。状态保存在control/workbench，日志在logs/launcher.log及各服务日志；关闭终端不停止，显式stop保留数据。

Node从现有PATH或nvm查找；Python/MLX缺失显示修复位置，不下载模型。轻量SDK使用本机源码离线安装，不下载依赖。原值库缺失时提示显式初始化新库，启动本身不初始化或迁移数据库；不读取审核凭据；Plat作为当前只读服务统一启停。
