# Console

当前操作台围绕报告、提取、条款、发布、运行五个对象组织。所有操作消费当前服务接口，前端不解析 PDF、不决定事实、不计算发布资格。

| 路由 | 内容 |
|---|---|
| `#/reports` → `/{pdf_id}` | 报告身份、导入、筛选、修订与本报告提取记录 |
| `#/extractions` → `/new` | 单份/批量选择报告与独立条款，先预览再提交 |
| `#/extractions/{session}` | 实际准备/提取进度、条款结果、原始事实、覆盖与运行记录 |
| `#/extractions/{session}/tables/{clause}` | 本次冻结条款的独立行、分组或矩阵 |
| `#/extractions/{session}/facts/{fact}` | 原值、实际身份、条款映射与原文证据弹窗 |
| `#/clauses` → `/{group}` → `/{group}/{clause}` | 展示组、条款清单、独立合同；新增与编辑使用独立窗口 |
| `#/extractions/{session}/data` | 已接收原值、期间、投影、下载及发布记录；投影失败也可查看原值 |
| `#/publication` → `/new` → `/{dataset}` | 选择已投影数据、核对实际成员、显式发布、撤下 |
| `#/runtime` → `/{task}` | 当前服务就绪状态、执行队列与任务详情 |

父子页面可直接打开，子页面提供明确返回。报告修订和条款编辑使用乐观并发检查；新增条款默认阻塞，不将结构校验宣称为科学验收。批量仅组合独立选择，提交失败只重试未提交项；网络写操作不自动重放。

源码为 `frontend/src/app`（路由/外壳）、`frontend/src/features`（五类对象）、`frontend/src/shared`（生成类型、请求、资源读取和基础控件）。`frontend/src/shared/contracts.ts` 从 Python 公共合同生成。表格内容由冻结条款的组织定义驱动，原值、实际口径、报告身份与目标定义分别展示。

HTTP代理为 `/document`（18080）、`/extraction`（18180）、`/database`（18280）。开发覆盖变量为 `CONSOLE_DOCUMENT_URL`、`CONSOLE_EXTRACTION_URL`、`CONSOLE_DATABASE_URL`；静态服务使用对应 `ESG_*_BACKEND_URL`。不手拼服务器产物目录，不持有审核凭据。

```sh
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run dev
```

项目根运行 `ConsoleESG/scripts/start.sh` 在 18081 提供正式构建。请求按资源键去重，离开页面取消读取，后台暂停轮询；15 秒读取/45 秒写入等待边界不等于业务失败。弹窗管理焦点、Escape、Tab、背景交互和滚动；嵌套确认只响应顶层窗口。

App级运行浮窗与安全后端投影共用会话状态。原值自动封存并交给独立接收器，下载入口在提取详情；接收不自动审核或发布。见[交接合同](../docs/architecture/delivery.md)。发布使用数据库预览摘要，页面不重算选源或比较规则；新发布需填写说明，撤下不删除原值。旧版本中心和旧审核控件已清除。
