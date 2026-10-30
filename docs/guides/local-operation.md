# 本地运行与目录规则

## 启动

仓库根执行 `bash ExtractESG/scripts/install_local.sh` 安装六个可编辑模块；已有本地依赖下无网络下载。模型目录和MLX运行时显式配置。随后：

```sh
ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 ExtractESG/scripts/start.sh
```

文档/Console组合18080、提取18180、Console18081、原值数据库18280。发布服务8765独立维护；其正式/演示数据和审核凭据不交给提取启动器。

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
  logs/                            document/extraction/console/database
  databases/capture/               新原值库；与正式/演示库分离
```

源码不保存业务产物。`ExtractESG/.local/python`是依赖环境，不是运行数据。原始父目录pdf文件保留，不重复成为当前权威仓储。控制记录可迁移位置，冻结事实、IR和旧任务字节不改写。

`migrate_data_root.py`先dry-run再copy/verify；已迁移目录不重复覆盖。迁移清单记录源目标与摘要。旧物理副本核对一致且无打开句柄后，移入系统废纸篓；恢复路径记录在 `.local/data/control/cleanup-upgrade.json`。正式/演示库、原PDF和无法确认引用的历史备份不参与自动清理。

终态测试任务用会话archive从默认列表收起，事实仍可按ID读取；未结束任务不归档。不要为洁净界面删除科学证据。生产数据的永久删除仍需引用核查。

## 核验

```sh
ExtractESG/.local/python/bin/python ExtractESG/scripts/check_contracts.py
ExtractESG/.local/python/bin/python ExtractESG/scripts/audit_structure.py
npm --prefix ConsoleESG/frontend test
npm --prefix ConsoleESG/frontend run build
npm --prefix database test
```

合同修改由 `check_contracts.py --write`生成Schema、索引和TypeScript类型，再普通检查。读取健康应同时检查 `restart_required` 和业务就绪，不能只看HTTP200。先停止当前启动器拥有的服务，再以同配置重启；不因一次读取超时重开任务或模型。

源码并列目录按所有权命名；新规范在docs/architecture，操作在guides，一次实施与机器证据在implementation/日期，问题基线在audits/日期，原需求与历史记录单独标识。没有“final2/new2/latest”多份当前说明。
