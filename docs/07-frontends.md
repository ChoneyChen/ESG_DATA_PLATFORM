# Console与Plat前后端设计

适用RQ-09、12、13。界面围绕对象及父子页组织，不恢复历史包堆叠；业务判断归责任后端，页面只表达已取得的状态和数据。

## Console信息架构

| 对象与页面 | 核心任务 | 数据来源 |
|---|---|---|
| #/reports → /folders/{folder} → /{pdf}；独立报告#/reports/{pdf} | 导入、筛选、资源修订、查看该报告任务 | 资源与文件夹API |
| #/extractions → /new | 选择单/多份报告及独立条款，预览再提交 | 会话预览/创建API |
| #/extractions/{session} | 本次准备/阅读、条款、事实、覆盖、运行记录 | 会话及结果概览 |
| /tables/{clause} | 该冻结条款的行表、分组或矩阵 | 分页organized结果 |
| /facts/{fact} | 原值、身份、目标关系及证据 | 分页事实/详情、物理来源 |
| /data | 接收、投影、原值下载、关联发布 | SessionProducts及数据库 |
| #/clauses → /{group} → /{group}/{clause} | 导航到独立合同；新增/编辑独立窗口 | 条款API |
| #/publication → /new → /{dataset} | 预览成员/理由、发布、撤下 | 数据库预览及产品API |
| #/runtime → /{task} | 服务状态、队列、实际任务 | 运行API及安全monitor投影 |

表中以/开头的提取子路径接在#/extractions/{session}后。路由定义在app/App.tsx与各feature入口，hash地址可直达，子页提供明确返回。新增/编辑是独立窗口，避免在列表下面展开一长串跨主题内容。

## 前后端职责

前端分app、features、shared。features按reports/extractions/clauses/publication/runtime内聚；共享合同在shared/contracts.ts生成，不手写另一份DTO。shared/api.ts负责超时、同源请求和错误；resource.ts负责资源键、取消和读取去重；ui.tsx负责基础窗口和控件。

后端application.py是组合根，sessions.py代理编排/结果，monitor.py组合安全运行状态，products.py连接接收/投影状态。原生run_id、session_id、processing_id分别保留，不能靠前端猜目录或拼存储URL。

同源代理：`/document`→18080，`/extraction`→18180，`/database`→18280。开发服务器覆盖变量为CONSOLE_DOCUMENT_URL/CONSOLE_EXTRACTION_URL/CONSOLE_DATABASE_URL；正式静态服务使用对应ESG_*_BACKEND_URL。浏览器不持有数据库连接或审核凭据。

## 运行浮窗与结果表

App级浮窗消费`/api/console/monitor`的console-monitor-1.1，与会话列表/详情来自同一后端会话观察。active_count、queued_count、running_count、waiting_count分别表示活动、排队、实际运行与等待准备；显示当前条款、对象、模型阶段、tokens和事实数。

执行状态、搜索范围、原值取得、属性完整度、交付、投影和发布分开显示。依赖不可用显示unavailable，不把旧缓存数或选中条款数当作当前完成。取消/恢复按钮来自后端available_actions。

结果表由本次冻结organization驱动；原值、实际身份、目标定义和资源信息分列。期间、单位、关系、文本筛选改变后重新分页；不全量拉取全部事实/组织结果再浏览器筛选。原文和字段证据可继续追到PDF页，原数值字符串不转JS浮点再显示。

读取去重及页面离开取消请求，后台暂停轮询。现有15秒读/45秒写等待边界只是HTTP等待，不等于业务终止；写入不盲目重放。批量提交失败仅重试未提交项，不能新建整批副本。

## 编辑与发布交互

条款编辑带expected_digest，资源编辑带修订信息；冲突呈现当前版本，让用户明确处理，不能后台覆盖。条款ready只表示声明可执行。新增条款输入类型、定义、属性与词典、阅读、检索、输出组织，不把大包当执行对象。

数据页即使投影失败仍可看已接收原值。发布页消费数据库预览的成员和理由，不在页面重算比较/选源。发布填写说明，携带preview_digest；摘要变化重新预览。撤下需要明确理由且不删除原值。

模态窗口管理焦点、Escape、Tab、背景交互和滚动；嵌套确认只响应顶层。按钮悬浮时保持文字与背景对比，不能全局button:hover覆盖文本按钮的局部设计。层级、加载、空态、失败与返回行为是页面改动的一部分。

## Plat

Plat前端按“数据集→数据表→观测详情”组织，以dataset_id和observation_id查询参数定位当前对象，navigation.ts保留浏览器前进/后退。观测提供全部期间/条款筛选、完整cursor分页、精确值、字段属性、证据和来源PDF。当前入口是App.tsx→components/PublishedData.tsx。

后端products.py只代理/v2发布产品，evidence_resolver.py校验发布来源并经散列读取PDF。上游离线明确不可用，不回退旧数据或读取本地提取目录。旧/v1、/internal/v1、/review仅返回410，不是备用业务接口。

Plat没有发布/审核写按钮或SQL连接；下载为数据库六份规范长表。缺比较轴的已发布观测仍可展示，比较资格另行表达，不隐去原值。
