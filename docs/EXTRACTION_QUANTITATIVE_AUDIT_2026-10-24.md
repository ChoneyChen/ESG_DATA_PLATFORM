# 定量条款提取审核与增强（2026-10-24）

对已有 29 份 foundation16 结果中的**全部定量条款结论**做一次只读审计，归纳未完全通过的原因，
并把可泛化的类别转成“子表 / 开放区间 / 表外属性”的正式存储，而不是继续堆在“待审核”或“排除”里。

## 1. 状态分布

| 状态 | 条数 |
|---|---:|
| found | 328 |
| not_found_in_selected_sources | 257 |
| only_rejected_or_other_period | 132 |
| review | 69 |
| conflict | 13 |

“未找到”里既有报告确实没有披露的，也有**披露了但被当成子项/表外属性而拒收**的。

## 2. 未完全通过的原因分类

同一行可能带多个标记，因此分类计数不相加。

| 类别 | 代表标记 | 次数 | 典型例子 | 处理 |
|---|---|---:|---|---|
| 分项 / 子属性 | `share_row_beside_mapped_quantity`、`label_not_renewable`、`label_is_fossil_carrier`、`label_not_fossil_energy` | 24 / 51 / 42 / 20 | 风能比例 28.28%、太阳能比例 13.24%、煤炭 453,408 吨 | **转成子表**，不再排除 |
| 相关度量 | `label_not_top_management_gender`、`label_not_training_hours`、`label_not_board_gender`、`label_not_work_related_accident`、`label_not_materials_used`、`label_not_waste`、`label_not_turnover_rate`、`label_not_corruption_conviction` | 31 / 24 / 17 / 14 / 11 / 9 / 8 / 5 | 女性董事 1 名、供应商培训 100%、损工事件率 0.102 | **转成子表**（相关度量），保留原标签与口径 |
| 数量 / 比例口径 | `not_a_ratio`、`unit_is_count` | 5 / 12 | 清洁能源使用量 567.42 万千瓦时、供应商培训 4 次 | **转成子表**，不冒充主值 |
| 表外 / 叙述 | `from_narrative_text`、`sentence_limited_to_part_of_group`、`boundary_model_subset_vs_header_group` | 88 / 24 / 10 | 正文“占比提升至 54.43%”、基地口径节约量 | 叙述值保留为子项；适用范围差异保留为独立视图 |
| 期间 / 列 | `current_column_period_not_labelled` | 12 | 当前列未标年份 | 保持待复核；不猜年份 |
| 单位 / 维度 | `unit_missing`、`unit_dimension_*` | 6 + 8 | 缺少单位、比例单位配到体积指标 | 保持待复核；不静默通过 |
| Scope 2 方法 | `scope2_method_unstated`、`label_not_this_scope` | 6 / 6 | 未写地点法/市场法 | 保持待复核，方法必须由原文支持 |
| 真正不同概念 | 索引行、监管上限、其他指标 | 其余 | GRI 索引 `303-5耗水`、许可配额 | 继续拒收 |

## 3. 本轮增强

### 3.1 自动子表

- 拒绝候选若属于同一概念族的分项或相关度量（例如能源载体、人群、计量方式），
  不再丢弃，写入结论的 `children`：`relation`（`component` / `related_measure`）、原标签、
  原值、单位、期间、页码与来源。
- 主值未披露但有分项时，结论状态为 `review` + `child_only`，操作台显示“已找到分项”，
  并展示子表；主值仍不伪造。
- 概念族词表在 `catalog.GROUP_COMPONENT_TERMS`，是数据：新增同族条款不需要新分支，
  也不引用任何报告名。

### 3.2 开放区间

- 值解析增加 `parse_measure`：`≥/不少于`、`≤/不超过`、`约`、`10-20` 分别得到
  `greater_than`、`less_than`、`about`、`range`，并保留 lower/upper。
- 提取观测、归并结论与成果包都携带 comparator 与上下界；平台已有对应字段，
  区间值不会退化成被截断的“精确值”。

### 3.3 表外属性与平台存储

- 有主值的结论：子项以 `component_<子项名>` 写入事实的来源属性，随父事实进入平台；
  审核口径表单默认勾选这些 `component_` 属性，不需要逐条手工添加。
- 没有主值的结论：子表保留在提取证据与操作台，平台不会凭空生成观测；需要主值确认后才发布。
- 叙述型子项保留原文与页码，和表格子项同一形态。

### 3.4 前端

- 报告文件改为 `inline` 返回：点击“打开原文”在浏览器中显示，不再强制下载；
  工作台优先使用页图查看器。
- 子表显示“关系 / 原值 / 页码”，主值缺失时标注“已找到分项”。

## 4. 解耦检查

- 子表识别只依赖概念族词表与行标记，不依赖报告名、页码或某份 PDF。
- 每条条款仍独立建任务、独立绑定、独立出结论；子表挂在各自结论下，不合并主值。
- 平台身份未变：子项是主披露的属性，不创建新的观测身份，也不改变发布门槛。

## 5. 仍保留的人工判断

单位缺失、期间未标注、Scope 2 方法未写明、真实不同概念与冲突值仍不会自动通过。
这些是证据本身不足，自动“予以通过”会制造错误数据；链路保留原值、页码与原因。
