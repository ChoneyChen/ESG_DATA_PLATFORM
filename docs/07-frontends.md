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

“运行”默认显示当前工作，近期执行记录在另一页签。排队行显示实时等待顺位。已读部分报告的任务显示“进度已保存 · 等待续读”和实际阅读计数。运行任务仍可返回所属提取。

执行状态、搜索范围、原值取得、属性完整度、交付、投影和发布分开显示。依赖不可用显示unavailable，不把旧缓存数或选中条款数当作当前完成。取消/恢复按钮来自后端available_actions。

结果表由本次冻结 organization 驱动。原值、实际身份、目标定义和资源信息分列。期间、单位、关系或文本改变后回到第一页并请求服务器，保留同条款的列和明细/矩阵选择，读取期间保留同条款已有筛选选项。提取详情可按条款名称或编号检索并查看目标数据状态。表格“核对原页”和事实详情使用同一原页预览，可同时查看印刷量值、单位、期间、实际属性及分类记录值，放大原页或跳转到 PDF 对应页。原数值字符串不转 JS 浮点再显示。

候选诊断显示实际处置原因。单个值有物理分类时显示该分类，行、文本片段和对象层面的诊断按自身范围显示。

读取去重及页面离开取消请求，后台暂停轮询。现有15秒读/45秒写等待边界只是HTTP等待，不等于业务终止；写入不盲目重放。批量提交失败仅重试未提交项，不能新建整批副本。

## 编辑与发布交互

条款编辑带expected_digest，资源编辑带修订信息；冲突呈现当前版本，让用户明确处理，不能后台覆盖。条款ready只表示声明可执行。新增条款输入类型、定义、属性与词典、阅读、检索、输出组织，不把大包当执行对象。

数据页即使投影失败仍可看已接收原值。发布页消费数据库预览的成员和理由，不在页面重算比较/选源。发布填写说明，携带preview_digest；摘要变化重新预览。撤下需要明确理由且不删除原值。

创建提取时独立选择“文档定位”和“提取模型”。文档定位默认原生文字，可选本地 Qwen 或 DeepSeek Flash 辅助文字不足页。主提取默认本地 Qwen 读 PDF 原页，可选 DeepSeek Flash 或只接收文字的 DeepSeek V4 Pro。任一阶段选择 DeepSeek 时显示密钥状态、掩码写入及所需页图或文字的传输说明。预览与详情显示冻结的两阶段选择，任务数据只带模型选项。

模态窗口管理焦点、Escape、Tab、背景交互和滚动；嵌套确认只响应顶层。按钮悬浮时保持文字与背景对比，不能全局button:hover覆盖文本按钮的局部设计。层级、加载、空态、失败与返回行为是页面改动的一部分。

## Plat

Plat前端按“数据集→数据表→观测详情”组织，以dataset_id和observation_id查询参数定位当前对象，navigation.ts保留浏览器前进/后退。观测提供全部期间/条款筛选、完整cursor分页、精确值、字段属性、证据和来源PDF。当前入口是App.tsx→components/PublishedData.tsx。

后端products.py只代理/v2发布产品，evidence_resolver.py校验发布来源并经散列读取PDF。上游离线明确不可用，不回退旧数据或读取本地提取目录。旧/v1、/internal/v1、/review仅返回410，不是备用业务接口。

Plat从已发布数据集的下载入口提供CSV长表ZIP，包含六张规范CSV及清单和条款定义。成功读取当前已发布数据集后，页头显示“下载”，导出该数据集的完整成员。表格内的CSV长表链接沿用当前期间及条款筛选。缺比较轴的已发布观测仍可展示，比较资格另行表达。
