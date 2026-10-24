# PlatESG 产品网站

React、TypeScript、Vite 与按需加载的 Apache ECharts 构建的中文数据网站。首页、企业、指标、趋势、同行比较、来源与数据方法独立组成公开产品。工作台位于 ConsoleESG；本应用只读取正式发布版本，不访问提取目录、候选、审核人、任务日志或内部管理接口。

## 页面职责

| 页面 | 内容 |
| --- | --- |
| 介绍页（默认） | 产品定位、当前发布快照、三步链路、主题入口、已发布企业与 API 预览；点“进入平台”后才加载应用外壳 |
| 概览 | 发布规模、主题覆盖、复核状态、最近发布数据与快捷入口 |
| 企业目录 | 名称、行业和主体类型筛选，卡片与列表切换，每页 12 家 |
| 企业档案 | 指标趋势、数据表、期间覆盖、来源和下载 |
| 指标浏览 | 完整主要清单，保留 49 个 ESRS 原编号，按主题或关键词查阅，每页 24 项 |
| 指标详情 | 定义、标准单位、必要维度、独立趋势、企业筛选及数据下载 |
| 同行比较 | 企业选择、期间、边界、方法与维度；显式选择口径；纳入与排除原因 |
| 报告来源 | 固定发布版本的报告目录、复核标记、原文片段及页码，每页 9 份 |
| 数据复核 | 来源、定义、比较、缺失语义、机器核验与人工决定的分工、版本与下载 |
| API 文档 | 四组公开只读端点的参数、请求与响应示例、复制与公开边界 |

标准包建设状态、主要／核心关系、诊断及编译信息由工作台展示。产品目录不以这些工程状态代替数据覆盖。

## 科学表达与公开边界

- 十进制原值保持字符串，不通过浮点数重新格式化。比率转百分比使用字符串位移。图表几何单独使用浮点数。
- 年度曲线按企业、指标定义、单位、期间口径、边界、方法和必要维度分组。缺失年份填入 `null`，ECharts `connectNulls: false`；不插值、不填零。
- 未知方法、未知边界、同期间多条替代值，不形成单一连接曲线。
- 区间和上下限使用独立披露卡片和原文。即使带有 `value_numeric`，限定值也不被当作普通数值点。
- 比较图按所选企业顺序展示，保留比较条件及排除原因；排放等绝对量不作为 ESG 评级或优劣排名。
- 来源抽屉只展示科学口径、原值、公开换算、报告原文和 `public_revisions`。不读取或渲染旧 `review`、`history`、规则 hash、候选或审核人。
- 来源外部链接只接受无嵌入凭据的 HTTP／HTTPS URL。演示主体明确标注，首页告知合成数据语义。
- 覆盖来自冻结发布版本。缺少正式缺失声明时表达“此版本暂无数据”，不从当前候选或审核状态推断“未披露”。

## 路由与版本

查询字符串是可分享的页面状态。默认进入介绍页；`view=overview|entities|entity|metrics|metric|compare|sources|methods|api` 选择平台页面，旧 `view=home` 解析为介绍页。列表页的 `page` 写入 URL，筛选变化回到第 1 页。最新发布解析后，明确的 `dataset_version` 固定在 URL 中；旧 `dataset` 参数仍可读，随后转换为规范参数。

企业、指标、趋势、比较、原文和导出使用同一发布版本。不可用版本明确报错，不悄悄改用最新数据。页面内关键词、主题、企业、日期、维度、图表／数据表开关持久化至 URL。比较链接包括 `companies`、`dimensions` 及 `compared=1`，刷新后重建相同只读比较查询。跨页链接与点击行为一致；浏览器后退／前进同步恢复页面。

## 开发与验证

```bash
npm ci
npm run dev
npm test
npm run build
npm run format:check
```

开发地址 `http://127.0.0.1:5178`。Vite 将 `/api` 代理到 `http://127.0.0.1:8765`，移除该前缀；`PLATESG_API_URL` 可指定其他 API。`dist/` 由 PlatESG FastAPI 提供同源页面与 `/api/v1` 读取接口。数据库初始化、审核和发布由上层服务及 ConsoleESG 负责。

ECharts Canvas 仅在确有可绘制数据时加载。构建将 ECharts 与其基础渲染器分包，两者不存在反向依赖；初始页面无需加载图表库。键盘导航、抽屉焦点约束、Escape 关闭、移动端布局与减少动画设置均有实现。

单元／组件测试覆盖原编号、版本固定与隔离、公开字段不泄漏、来源、比较口径、URL 恢复、十进制、比例、缺失年份、上下限、实体／方法／单位／污染物分组。实际浏览器验收脚本：

```bash
NODE_PATH=/path/to/playwright/node_modules \
  node tests/live_product_readonly_test.cjs
```

使用 `PLATESG_TEST_URL` 指定地址，`PLATESG_TEST_OUTPUT` 指定截图与验收 JSON 的输出目录，`ESG_TEST_CHROME` 指定 Chromium。默认输出 `/tmp/esg-p5-product-qa`。脚本限制所有写请求，仅允许公开 `POST /v1/comparisons` 读取查询，验证六个入口、49 个原编号、同版本链接、实际来源、比较恢复、移动布局与页面错误。它要求存在已发布企业数据，不负责创建或修改验收数据。

## 设计依据

信息组织借鉴 [Our World in Data 气候主题页面](https://ourworldindata.org/co2-and-greenhouse-gas-emissions) 的主题入口、数据探索、来源说明与下载结构；采用 [其 Chart API 元数据组织](https://docs.owid.io/projects/etl/api/chart-api/) 中“图表与解释一同提供”的原则。具体企业指标与比较合同仍由本项目定义。

图表按 [Apache ECharts 官方按需导入指南](https://echarts.apache.org/handbook/en/basics/import/) 注册 Line、Bar、Grid、Tooltip、Aria 与 CanvasRenderer，不引入完整图表类型集合。
