# 部署与运维

本章记录当前入口，不执行部署、不证明服务此刻就绪。命令从仓库根运行。Python应用要求3.11+；本机系统python3可能仍为3.9，文档工具也用下面的项目Python。

## 运行前提与安装

- 控制Python默认`ExtractESG/.local/python/bin/python`；已具备pyproject声明的基础依赖和构建工具。
- 本地模型Python由ESG_MODEL_PYTHON指定，默认用户Desktop/model/.runtime/venv/bin/python；Paddle专用Python单独配置。已有本地权重和第三方模型依赖由对应模型环境维护。
- Node.js 20+，database使用其packageManager声明的pnpm@11.9.0；Console/Plat前端及database的Node依赖按各自lockfile安装。安装依赖需要网络时与离线运行分开，不由业务任务临时下载。
- 已显式初始化的当前capture数据库；原报告和运行目录可以通过ESG_DATA_ROOT隔离。

```sh
bash ConsoleESG/scripts/install_local.sh
npm --prefix ConsoleESG/frontend ci
npm --prefix PlatESG/frontend ci
pnpm --dir database install --frozen-lockfile
```

install_local.sh离线、无第三方依赖下载地安装本仓库SDK/应用，并调用install_worker.py更新所配置MLX及Paddle环境中的本仓库包。它不安装模型权重或替换第三方模型依赖。worker实际运行安装包，不拼接兄弟源码路径。

## 配置与五服务

| 配置 | 含义/默认 |
|---|---|
| ESG_PROJECT_ROOT | 本仓库组合根 |
| ESG_DATA_ROOT | 默认仓库.local/data；不同工作区/实验必须区分 |
| ESG_CLAUSE_ROOT | 独立条款资产根；默认ClauseESG/clauses |
| ESG_DOCUMENT_PYTHON | 控制Python |
| ESG_MODEL_PYTHON | MLX提取API/worker Python |
| QWEN_VLM_MODEL_PATH | 本地提取权重目录，默认用户Desktop/model/Qwen3.5-9B-4bit |
| LOCAL_PADDLEOCR_RUNTIME_PYTHON | 本地OCR环境 |
| ESG_DOCUMENT_BACKEND_URL | 文档API，默认http://127.0.0.1:18080 |
| ESG_EXTRACTION_BACKEND_URL | 提取API，默认18180 |
| ESG_DATABASE_INGEST_URL | 自动交付目标，统一启动器设置为18280 |
| ESG_DATABASE_PRODUCTS_URL | Plat的产品目标，默认18280 |
| ESG_DATABASE_DATA_DIR | 本地PGlite位置，工作台默认数据根/databases/capture |
| ESG_DATABASE_URL | 独立数据库外部PostgreSQL连接配置；值可能敏感，不输出到文档/日志 |
| ESG_DATABASE_MODULE_ROOT | SDK部署时Node/queries/migrations资产位置 |
| PLATESG_AUTO_PUBLISH | 保持0；发布始终显式 |

完整文档配置定义在esg_document/config.py，其余配置在所属启动/客户端文件。服务地址只含协议、主机和端口/允许前缀，不夹凭据、查询参数或隐含提供者切换。

| 端口 | 入口 | 健康/兼容 |
|---|---|---|
| 18080 | esg_console.application:app（组合文档） | /health/compatibility |
| 18180 | esg_extraction.pipeline.api:app | /api/compatibility、/api/engine |
| 18280 | esg_database.api:app | /health |
| 18081 | serve_workbench_frontend.py | /api/compatibility |
| 8765 | PlatESG.backend.main:app | /health/compatibility |

## 统一启动和停止

```sh
ConsoleESG/scripts/start.sh start --no-browser
ConsoleESG/scripts/start.sh status --json
ConsoleESG/scripts/start.sh stop
ConsoleESG/scripts/start.sh restart --no-browser
```

workbench.py后台托管五服务，记录进程身份，只停止自己拥有的进程。关闭终端不停止服务。已有不兼容服务占用端口时明确返回，不按端口名称粗暴kill。start会离线安装本仓库包、构建两前端、检查现有数据目录；超时不等于后台进程消失，不重复提交任务。

有活动任务时stop/restart会拒绝；先在Console按实际需要结束/取消任务，确认阅读和准备都已停止，再维护。文档整理不隐含启动、停止或恢复原批次。

单服务入口是`ConsoleESG/scripts/run_service.sh document|extraction|console|database|plat`。其document仍为Console组合应用；真正独立的文档程序入口还可使用esg-document CLI。桌面启动项由install_desktop_launcher.py安装，控制器与命令行共用。

## 新库与模式管理

仅对明确选定的新目录执行，不能把示例路径替换为正在使用的demo或生产库：

```sh
ESG_DATABASE_DATA_DIR=/absolute/new/capture database/scripts/run-node.sh database/runtime/manage.mjs init --profile capture
ESG_DATABASE_DATA_DIR=/absolute/new/capture database/scripts/run-node.sh database/runtime/manage.mjs status --profile capture
```

应用启动不迁移。已有迁移摘要不可变；遇到不完整/不兼容模式先分析目标库，不删除数据目录“重试”。独立服务支持PGlite或PostgreSQL；统一本机启动器目前仍以本地capture目录作为准备条件，外部数据库独立部署需显式安排服务/地址。

## 有序排障

| 观察到的现象 | 先读取 | 责任与处理 |
|---|---|---|
| 页面未开/服务未就绪 | launcher状态与logs/launcher.log | 看具体服务、依赖、端口和指纹，不重建任务 |
| 长期无进度 | 会话、monitor、准备阶段、原生run和当前调用 | 区分排队/等待IR/模型生成/中断，追到对应子任务 |
| 条款完成但数值少 | 冻结条款、搜索计划、候选排名、物理表、关系 | 找首个偏离点；不能先放宽所有预算 |
| 目标值变未决 | 原值/单位来源、规则、实际字段和flags | 区分含义不明与属性缺失，后者不应阻碍原值 |
| 定性错误 | 所选引句、直接上下文、条款定义 | 区分话题与方法、检查是否借未选原文补足含义 |
| 文档断连 | 原生error与检查点、文档日志 | 恢复依赖后明确续跑，不把未执行项当完成 |
| 交付不明 | export_id、outbox状态、数据库回执 | 先按幂等ID查回执，不新造包ID或手工复制值 |
| 投影失败 | 已接收事实、projection状态/error | 修复数据库转换/模式，保留接收原值后重试 |
| 发布冲突 | preview_digest、成员摘要、修订 | 重新预览后显式发布，不跳过摘要 |

默认日志在数据根/logs；调用与诊断在所属运行目录。观察不修改事实、不手工代填结果、不恢复用户暂停任务。指标含义错误回到条款，运行错误回到模块。

## 更新、回滚与来源保护

维护前记录数据根、代码提交、条款摘要及有关运行ID；在保存边界结束当前执行，安装更新，再按明确计划恢复。代码回滚用Git恢复相应版本，不自动降低SQL模式或改旧事实。已封存旧任务继续按自身合同解释。

PGlite备份前停止写入并关闭所属服务，复制完整目录及相关资源/控制记录；不要热复制部分文件。来源、已发布投影及活动产物按所属生命周期端口清理，不能直接rm业务目录。自动备份/灾难恢复尚没有统一产品化命令，见工作清单。

运行仅绑定本机，写操作使用既有本机operator机制；不要求开发者复制旧review-token。不读取凭据文件来“调通”请求，不把密钥放进URL、任务JSON、前端或文档。云OCR保持关闭。
