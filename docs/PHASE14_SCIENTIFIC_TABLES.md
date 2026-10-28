# 阶段 14：条款分类与报告原印分类分开，科学表各自成表

日期：2026-10-28
范围：公开数据产品（PlatESG）、数据库读取投影、平台字典构建门禁、Console 导航与原文证据展示。

对应规划条目：§4.2（科学定义只有一份）、§4.4 / §5.3A（固定分类不得覆盖报告细分类）、
§5.3B（字段级证据公开）、§5.3D（公开 CSV 不是完整科学表）、§3.2（文字层为共享文档能力）、§6.3（Crawl 不占生产主线）。

## 1. 条款分类与报告原印分类分开

此前 `esg_api.v_disclosure_dimensions` 用 `COALESCE(canonical_value, raw_value)` 合成一个口径表，
报告印的是「风能」、条款固定的是 `energy_source=renewable`，公开数据只剩后者。

现在两层同时发布：

| 字段 | 含义 | 来源 |
|---|---|---|
| `dimensions` | 条款口径，比较契约依据 | 测量上下文（审核确认后） |
| `reported_dimensions` | 报告原印分类，按原词保留（每轴可多值） | `esg.disclosure_dimensions.raw_value` |
| `source_row_label` | 报告原始行名 | 来源事实 / 披露行名 |

读取侧在 `hydrateObservations` 一次性批量取回（一条查询，不按观测循环），派生观测从操作数快照回溯。
公开站观测详情显示「报告行名」与「报告原印分类」，只列出与条款口径不同的轴；
比较、筛选仍只用 `dimensions`，报告原词是证据而不是新契约。

旧接口视图 `esg_api.v_disclosure_dimensions` 保持 `COALESCE(canonical_value, raw_value)` 不变，
以免冻结行改变比较身份；迁移 0018 另加 `esg_api.v_reported_disclosure_dimensions`，
按披露给出未经改写的原印值，供旧接口消费者按需联接。

## 2. 导出改为科学表，不再把 JSON 塞进单元格

`/v1/exports` 新增 `table` 参数：

| table | 一行是什么 | 关键列 |
|---|---|---|
| `observations`（默认） | 一条观测 | 主体、指标、期间、边界、方法、精确数值、单位、原值、报告行名 |
| `dimensions` | 一个观测的一个轴的一个值 | `axis_class`（clause / reported）、`axis`、`ordinal`、`value` |
| `properties` | 一个补充属性字段 | `field_key`、`value`、`value_type`、`evidence_scope` |
| `evidence` | 一条字段级证据 | `field_key`、`evidence_role`、页码、原文摘录、坐标 |

- CSV 单元格只有标量；列表用 `|` 连接；精确小数（`value_numeric`、边界、页码、坐标）不做防注入前缀。
- `format=json` 返回 `items / dimensions / properties / evidence` 四张表（`schema_version=platesg-export-v2`）。
- 数据站方法页与接口文档同步给出「维度表」「证据表」下载和参数说明。

## 3. 平台字典的身份轴门禁

平台字典过去可以自行声明条款的身份轴，与标准包合同无强制关系。现在：

- `check_identity_axes` 要求每个 `required_dimensions` / `fixed_dimensions` 轴必须由标准包
  `measurement_contract.identity_axes ∪ comparison_axes` 声明；
- 尚未由包声明、但平台确需记录的 19 个轴登记在 `PLATFORM_IDENTITY_EXTENSIONS`，每条带原因
  （如 `denominator_scope`「占比必须声明分母范围」、`worker_population`「员工群体决定数量含义」）；
- 新条款若使用未声明的轴，字典构建直接失败并列出轴名。

本次按 55 项主要条款实测：既不是盲目改成从合同推导（37 项会与现有比较契约冲突），
也不是维持无约束双写，而是把“包声明 + 显式登记扩展”做成构建期可验证的唯一来源。
`build-dictionary.py` 的默认版本号同步为 **1.5.0**（此前仍是 1.4.5，重建会降低版本）。

## 4. 文字层是共享能力

`_read_text_layer` 的注释曾写“只用于 foundation16 页面定向运行”，与实际实现不符：
只要处理的是真实 PDF，全本运行同样按文字层填充、只把不可读页面送视觉识别。
已改正注释，避免后续误以为这是一条路线专属的捷径。

## 5. Crawl 报告源整条移除

按要求，Crawl 报告源在前端与后端一并删除，链路不再主动接入或读取外部报告源：

| 移除项 | 位置 |
|---|---|
| 报告源页面 `#/crawl` | `ConsoleESG/src/pages/CrawlSource.tsx` |
| 报告库连接卡片、外部索引选择器 | `CrawlConnection.tsx`、`CrawlIndexPicker.tsx` |
| 外部索引字段（系统、报告编号、LEI、证券代码、ISIN、包摘要、来源地址） | 报告身份编辑表单 |
| `/api/console/crawl-index`、`/crawl-index/status`、`/crawl-index/companies` | `esg_v2/api/console.py` |
| `CrawlIndexReader` 与对应测试 | `control_plane/crawl_index.py` |

Console 主线回到「报告 → 提取 → 审核 → 发布」四步，工具只剩批量生产、标准中心、运行中心。
报告只由本链路上传 PDF 进入，报告身份逐项手工登记。`CrawlESG/` 目录仍是独立保护区，未修改、未暂存。

## 6. 验证

| 套件 | 结果 |
|---|---|
| 标准（含新增身份轴门禁 5 条） | 177 通过 |
| PlatESG Python（含导出与 DTO 合同） | 79 通过 |
| PlatESG Node | 62 通过 / 10 跳过 |
| 数据库（含新增原印分类 1 条） | 62 通过 / 2 跳过 |
| Console（移除 2 条 Crawl 用例） | 240 通过 |
| 文档后端（移除 2 条 Crawl 用例） | 675 通过 |

真实数据核对：用 demo 库副本启动隔离实例，观测详情返回
`reported_dimensions` 与 `source_row_label`，`table=dimensions` 导出行含 clause 与 reported 两类轴；
核对后实例与副本已删除，未触碰正式库与 demo 库的原有内容。
