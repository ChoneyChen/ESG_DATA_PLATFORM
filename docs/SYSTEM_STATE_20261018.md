# 系统状态快照（2026-10-18）

本文记录 2026-10-18 第三轮结束时的系统实际状态：本轮改动的链路机制、实测质量、测试结果和已知问题。它是日期化快照；之后的当前事实仍以[根 README](../README.md)为准，变化记录见[更新日志](CHANGELOG.md)。

## 1. 本轮改动

本轮只动链路机制，不为任何单份报告写特例。出发点是 CKH 附表：一张宽表跨左右两页排版，左页是行标签与前两个实体，右页续 Infrastructure / Telecommunications / Total 三组列，且不重复行标签。旧逻辑要求跨页两表列数相同，因此这类表从未被连起来，模型只读到左页子实体列，把 Retail 的 Scope 1 当成集团值。

落地的通用规则：

| 机制 | 位置 | 判定 |
|---|---|---|
| 横向跨页续接 | `document/table_graph_builder.py` `_horizontal_continuation` | 左页首列为文字标签（≥50% 数据行）、右页首列为数值或空（≤15%）、两页前 3 行共享至少两个年份、垂直版带重合 ≥60%、行数差 ≤ max(3, 15%) |
| 分段行对齐 | `document/logical_table_builder.py` `_horizontal_row_shift` | 按两页首个数据行求 `row_offset`；数据带行数不同则记 `horizontal_row_alignment_unresolved` |
| 表头带推导 | `_leading_header_rows`（文档侧）、`_derived_header_rows`（提取侧） | 数值行之前的行都是表头；年份与 `(3)` 这类脚注标记不算数值 |
| 合并视图 | `tables.py` `composed_tables` / `_compose_horizontal` | 同组物理表按列拼接，保留原 `cell_id` 与页码 |
| 显式 Total 列 | `tables.py` `explicit_total_column` / `total_column_group` | 同期间唯一 Total 列组存在时取该组对应年份列；不依赖列求和 |
| 证据类别注册 | `document/validator.py` `CONFLICT_EVIDENCE_SCOPE` | 冲突类型 → 证据范围（page / page_text / object） |

## 2. 实测

CKH（`00001_2026_SUSTAINABILITY_2025`，25 页选中，4 组左右页附表全部识别）：

- 16 项全部有结论；右上四组横向续接写入 IR，其中 3 组行对齐成功、1 组行数 20 vs 18 保留未对齐标记。
- E1-6_07 = 6,734,455、E1-6_09 = 2,236,657、E1-6_10 = 1,106,778、E1-6_11 = 11,691,966，均为集团 Total 列，带 `column_switched_to_explicit_total`。
- 环比：上一轮同一份报告 11 项，且 Scope 1 取到 Retail 的 101,164。

9 份金标报告回批（tag `h2`）：

| 报告 | 正确 | 偏差 |
|---|---|---|
| longi | 24 | — |
| catl | 14 | E2-4_09 多报（真实 VOC 质量平衡陈述，金标标为未披露） |
| cmobile / tencent / pingan / report00004 | 各 16 | — |
| zijin | 29 | — |
| boc | 14 | E1-5_09（2024 办公楼碳中和数字） |
| alibaba | 12 | — |

合计 **157/159，0 错、0 漏**，与上一轮持平；两处偏差与本轮改动无关。

单份耗时（OCR + IR + 提取）：约 240–460 秒，CKH 最长（374 秒提取，6 次模型调用、18k 输入、882 输出）。

## 3. 测试

| 套件 | 结果 | 说明 |
|---|---|---|
| `document_ir_extract/backend` | 654 passed | `PYTHONPATH=src:../../shared_runtime/src:../../standards/src`，`.venv` 内运行 |
| `targeted_table_extract/backend` 全量 | 688 passed / 15 failed / 6 skipped | 15 项为既有失败：`report-fact-agent-v8` 常量与实现 `v9` 不一致（`shared_disclosure`、`text_span_reading` 等），与本轮表格改动无关 |
| `tests/test_foundation16.py` | 27 passed | 新增横向合并与 Total 列、脚注标记表头两项回归 |
| ConsoleESG | 未重跑 | 本轮未改前端；上一轮 210 passed |

新增依赖：`document_ir_extract/backend/.venv` 补装 `jsonschema>=4.23,<5`（测试声明为 dev 依赖，环境此前缺失）。

## 4. 已知问题

- **行数不一致的横向组不自动对齐。** p169/p170 两页数据行 20 vs 18，逻辑表保留 `review_required` 之外的未对齐标记，提取仍按拼接视图读取，可能错位一行；需要逐组修网格或人工确认。
- **`Total` 组的边界依赖标签位置。** 当前按“标签列 + 后续年份列直到下一个实体标签”展开；若表头跨列合并（`col_span > 1`）与标签分列混用，可能取错。
- **提取后端 15 项既有失败**未处理（版本常量与测试期望不一致）。
- `mhp` / `ckh` / `yuexiu` 仍无金标文件，只能人工核对。

## 5. 数据与提交

- 实验室产物在 `../.local/foundation-lab/`：IR `tl-ir/ir-20261018T121334Z-8c9a81162def`（CKH）、提取运行 `runs/*-h2/`。
- 本轮未提交、未推送；工作树改动保留待用户决定。
- `CrawlESG/` 保护区未改动。
