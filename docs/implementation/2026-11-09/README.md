# 勾选清理与文件夹批量提取

本轮完成任务记录、投影数据的勾选清理，并补齐“已删除”内的彻底删除接口和按钮。创建提取沿用报告库的文件夹与成员，不另建批次目录。

## 页面操作

- 提取：勾选已结束的任务，删除所选记录；进入“已删除”后可恢复或彻底删除。执行中的任务不能勾选。
- 发布 → 已投影数据：本页全选、删除所选；“已删除”中可恢复或彻底删除。永久操作有确认窗口。
- 创建提取：选择报告文件夹后点击“全选此文件夹”；切换文件夹保留已有选择，支持清空。不可用PDF不纳入全选。
- 报告文件夹页：“批量提取此文件夹”进入相同选择页，仍由用户确认所选报告与条款后提交。

## 接口

| 所属服务 | 接口 | 请求/用途 |
|---|---|---|
| Console后端18080 | GET `/api/report-sessions?archived=true` | 已删除任务列表；默认只读未删除记录 |
| Console后端18080 | POST `/api/report-sessions/archive`、`restore`、`purge` | `{"session_ids":["extract-plan-…"]}` |
| 提取18180 | POST `/api/runs/purge` | `{"run_ids":["extract-…"]}`，由编排层调用所属模块清理 |
| 数据库18280 | GET `/v1/projections?archived=true` | 分页读取已删除投影 |
| 数据库18280 | POST `/v1/projections/archive`、`restore`、`purge` | `{"projection_ids":["projection-…"]}` |
| 报告资源18080 | GET `/api/report-resources/folders`、`/api/report-resources?folder_id=…` | 报告页与创建提取共用 |

Console18081通过既有`/document`、`/extraction`、`/database`代理访问对应服务。

## 清理范围

任务彻底删除：提取服务清理运行记录、进度、信号和整份提取产物目录；编排层清理父任务、对应队列记录/事件、worker日志及这些任务持有的IR引用。共享文档准备、IR/OCR和原PDF保留。交付重试与清理串行，避免清理后重新写回产物。

投影彻底删除：数据库事务清理投影、成员、相关预览及不再被其他产品引用的观测和重述关系。接收回执、原值及条款快照独立保留。发布快照引用的投影（包括已撤下快照）不能单独彻底删除。删除状态不自动重试，也不把该报告的更早投影重新显示为待发布；显式重新投影仍可从原值重建。

## 文件落点

| 模块 | 文件 |
|---|---|
| Console后端 | `ConsoleESG/backend/src/esg_console/sessions.py`、`products.py` |
| Console前端 | `features/extractions/list.tsx`、`create.tsx`；`features/publication/pages.tsx`、`data.tsx`；`features/reports/pages.tsx`；`shared/ui.tsx`、`app/App.tsx`、`styles.css`（均位于`ConsoleESG/frontend/src`） |
| 编排 | `ExtractESG/pipeline/src/esg_pipeline/extraction.py` |
| 提取产物所有者 | `ExtractESG/extraction/src/esg_extraction/pipeline/{api,state,delivery}.py`、`adapters/artifacts.py` |
| 数据库 | `database/migrations/capture/0023_projection_archives.sql`；`queries/capture_products.mjs`；`runtime/{schema,capture_worker}.mjs`；`src/esg_database/{api,products_api,products_contracts}.py` |
| 生成SDK | `database/scripts/export_contracts.py` → `database/contracts`；`tools/export_console_types.py` → Console共享类型 |

0023已显式应用于工作台的`.local/data/databases/capture`；旧迁移未改写，服务启动不自行迁移。没有操作旧demo数据库。

## 真实操作结果

- 从已停止服务的数据复制隔离副本，使用真实任务记录及其原产物调用API：移入、恢复、再次移入、彻底删除通过。父任务、原生运行记录、产物目录、3条队列记录和对应日志/引用均被清理。
- 真实数据库副本中，删除一份69条观测的投影后：投影目录39→38，所选观测实体剩余0，69条接收原值保留；后台待投影列表不包含该删除项。额外删除一份确有多版投影的真实报告，目录38→37，旧版没有重新出现在列表。
- 正式环境经Console代理跑通移入与恢复，原有44条任务、39份可见投影保持原数。68份报告保留，PV文件夹50份均可用于全选。三个永久删除接口均已部署，未对原有数据执行永久删除。
- Console生产构建通过，服务已更新。当前Work设置禁用本地浏览器控制，因此本轮未执行视觉走查。

没有新增单元测试或运行无关测试套件；验证采用以上真实记录、真实数据库副本及实际HTTP操作。
