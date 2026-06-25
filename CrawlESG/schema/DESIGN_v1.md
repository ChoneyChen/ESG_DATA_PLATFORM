# ESEF / ESG 索引层总体设计方案（v1）

> 定位：把前面逐步敲定的所有结论收敛成一套**内部自洽、可落库、可回溯**的设计。
> "无问题"在这里的含义是：**所有设计矛盾已解决并写成可测试的不变量；剩下的只是显式标注的外部待定参数，不是隐藏的坑。**

---

## 0. 设计原则（五条，后面所有取舍都从这里推出）

1. **身份先行**：报告身份由结构标识定义（`sha256` / `fxo_id`），不由描述标签或网址定义。
2. **单一真相源**：每个事实只存一处。`report_year` 由 `period_end` 派生，不重复存；`company_id` 是 `lei` 的别名，不另立身份。
3. **可回溯、可复现**：每个数字都能回到某一行原始申报；同样的输入重跑得到同样的 `report_id`。
4. **薄而正确**：索引层只放"能自动推导且可校验"的字段；主观/高成本的内容标签（主题、指标）放抽取层，且锚到受控词表。
5. **公式与数据源解耦**：覆盖率/缺口逻辑封装在视图里；换交易所只换数据源，公式不动（对应 PPT"可扩展架构"）。

---

## 1. 总架构：四类元数据，**不要画成平级**

那张 6 层图的最大问题是把四种性质不同的东西并排了。正确的层次是：

| 类别 | 性质 | 在库里的形态 | 例 |
|---|---|---|---|
| **A. 身份层** | 唯一标识一份报告 | 主键 / 唯一约束 | `report_id`、`sha256`、`fxo_id`、`(lei, period_end)` |
| **B. 单值属性** | 一份报告恰好一个值 | 主表的**列** | `lei`、`period_end`、`country`、`report_type`、`language` |
| **C. 多值标签** | 一份报告可有多个 | **关联表(junction)** | 主题、标准、指标 |
| **D. 系统标签** | 运行/质量元数据 | 列或派生 | 质量计数、`esg_flag`、文件格式、URL |

并且分类是**两级**的：
- **报告级分类 = 身份维度**（A+B+D）→ 用于索引、去重、覆盖、缺口。
- **指标级分类 = ESRS 数据点编码**（C）→ 用于抽取，跨公司可比。

---

## 2. 数据模型（SQLite DDL，可直接执行）

> 完整可执行版见随附 `schema.sql`。下面是结构说明。

### 2.1 维度与受控词表

- `entity(lei PK, entity_name, company_alias, country, industry_code, industry_name)`
  公司身份表。`lei` 唯一；`company_alias` 收旧 company_id / 路透代码；`industry_*` 由 LEI/代码**派生**，不手工打标。
- `exchange(exchange_id PK, exchange_name, country)`
  交易所表，承载"可扩展"——换所只新增一行＋换数据源。
- `esrs_datapoint(esrs_code PK, topic_code, topic_name_en, standard, name_en, name_zh, default_unit)`
  **指标层受控词表，是指标级分类的核心**。`esrs_code` 如 `E1-6`；`topic_code`（E1/E3/S1/G1）由编码**派生**出主题，无需另设 Topic 标签；`name_zh` 只是显示别名，绝不当键。

### 2.2 原始申报（审计底稿，**不去重**）

- `report_raw(fxo_id PK, lei FK, period_end, country, report_type, seq, sha256, error_count, warning_count, inconsistency_count, processed, date_added, package_url, json_url, viewer_url, api_id)`
  每条原始申报一行（含 BP 那种同财年 3 份重报）。**全留**，去重发生在"晋升到主表"这一步，保证可回溯。

### 2.3 主报告（去重后，**索引层主表**）

- `report(report_id PK, fxo_id FK→raw, lei FK, exchange_id FK, period_end, report_year[派生], country, report_type, language, sha256, esg_flag, package_url, json_url, viewer_url, n_versions)`
  约束：`UNIQUE(lei, period_end)`、`UNIQUE(sha256)`、`UNIQUE(fxo_id)`。
  `report_year` 用 `GENERATED ALWAYS AS (CAST(substr(period_end,1,4) AS INTEGER)) STORED` 自动派生。

### 2.4 抽取指标值（指标级分类落点）

- `indicator_value(id PK, report_id FK, esrs_code FK, raw_label, value_num, unit, scope, fiscal_year, source, confidence, page_or_anchor)`
  约束：`UNIQUE(report_id, esrs_code, scope, unit)`。
  `source` ∈ {xbrl-json, text-anchor}；`confidence`：结构化=high、文本抽取=medium（对应 PPT 风险标注"财务不受影响、ESG 文本抽取有误差需抽样校验"）。

---

## 3. 主键与唯一约束的最终裁定

> 之前那张"是否唯一全打勾"的表是反的。定论如下，建库时照此加约束：

| 字段/组合 | 唯一性 | 角色 |
|---|---|---|
| `report_id` | ✅ 唯一 | 代理主键，**对外引用**（R000001…） |
| `sha256` | ✅ 唯一（去重后） | **身份/去重键**，跨机构交叉链接 |
| `fxo_id` | ✅ 唯一 | **申报级**唯一（含 seq），raw 表主键 |
| `(lei, period_end)` | ⚠️ **去重后才唯一** | 业务键；raw 层不唯一（重报） |
| `lei` / `period_end` / `country` / `report_type` / `language` | ❌ **必须不唯一** | 支撑 country×year 透视与缺口比对 |
| `source_url`（三个 URL） | 行级唯一但**不当键** | 定位，不定义身份 |

一句话：**只有代理 id 和 hash 唯一；其余靠重复支撑全部分析；唯一约束加在组合键上。**

---

## 4. 去重规则（定版，与已跑出的结果一致）

- **L1 — 按 `sha256`**：字节完全相同（多 OAM 报送同一文件）→ 判为同一份，只晋升一条。
- **L2 — 按 `(lei, period_end)`**：哈希不同的修正/重报 → 收敛为权威版，规则 **`error_count` 升序、再 `processed` 降序**取首条；`n_versions` 记录收敛了几份。
- raw 表保留全部，主表只放权威版。

> 实测：GB 2,838 → L1 后 2,838（无字节重复）→ L2 后 2,796 / 798 家；42 份重报被正确收敛（含 BP FY2024 的 3 版保留最终修正版）。

---

## 5. URL 与定位策略

`report_id` 定位、`sha256` 定身份、**三个 URL 各司其职、都要留**：
- `package_url` → 报告 ZIP 包，走 ESRS 锚点抽取；
- `json_url` → xBRL-JSON，财务科目**直接取结构化数值**（不走文本抽取）；
- `viewer_url` → 人工核对。

"唯一网址"不能替代去重——网址越唯一，重报越会被当成多份留下。

---

## 6. 指标级分类 = ESRS 受控词表（不要自由中文名）

每个抽出的数值挂一个 `esrs_code`：

| esrs_code | topic（派生） | 抽取来源 |
|---|---|---|
| E1-5 能源消耗与结构 | E1 气候 | 文本锚点 |
| E1-6 GHG 排放(Scope 1/2/3) | E1 气候 | 文本锚点 / xBRL |
| E1-7 GHG 移除 | E1 气候 | 文本锚点 |
| E1-8 内部碳价 | E1 气候 | 文本锚点 |
| E3-4 水资源 | E3 水 | 文本锚点 |
| S1 劳动力 | S1 社会 | 文本锚点 |
| G1 商业行为/腐败 | G1 治理 | 文本锚点 |

主题(Topic)从 `topic_code` 反推、不另设人工标签；行业(Industry)从 LEI 派生。**2026 财年 ESG 数字标记强制后，文本锚点逐步让位给结构化标记，受控词表不变、自动收敛。**

---

## 7. 覆盖率与缺口（用视图封装，公式不动）

- **分子 Y**：`report` 按 `country × report_year` 计数（视图 `v_coverage`）。
- **分母 X**：外部"在册公司-年"面板（PPT 分母面板，止于 2023，需更新）。
- **缺口**：t1/t2/t3 模型，双口径并行——口径 B（统一截止年 t3=2024）为主、口径 A（披露中断）为辅。
- **预测**：覆盖率 = R(发报率) × O(可获得率)；O 由实测命中率校准，先给区间再收敛。

逻辑全部封装在视图里：换交易所 → 改 `exchange_id` 与数据源，视图公式不变。

---

## 8. 工作流（国家先行，已对齐 PPT 后续计划）

| 阶段 | 内容 | 状态 |
|---|---|---|
| **Phase 0** 索引 | 拉全元数据→双层去重→覆盖统计，不下包 | ✅ GB 已完成 |
| **Phase 1** 校准 | 扩锚点词表，在更大 GB 样本测 ESG 命中率，定 O 实测值 | ⏳ 下一步 |
| **Phase 2** 放量 | GB 全量下载（2,796 包，~40GB），生成台账 | 脚本就绪 |
| **Phase 3** 扩欧盟 | EU FY2024（ESRS 真 ESG）按同模型扩 | 规划 |

国家先行的定位：在**最干净、单语言、100% 可下**的 GB 集上把"下载→校验→检测→入库"管线跑硬；标准化 ESRS ESG 的增量论证留给 Phase 3。

---

## 9. 与 6 层图的映射（哪层自动填、哪层要抽、哪层丢弃）

| 6 层 | 处理 | 来源 |
|---|---|---|
| ① Source | 自动＝ESEF（单源近常量） | 固定值 |
| ② Entity | 自动＝LEI/公司名 | `entity` |
| ③ Topic | **派生**自 ESRS 编码，不单独打标 | `topic_code` |
| ④ Industry | **派生**自 LEI，不单独打标 | `entity.industry_*` |
| ⑤ Standard | 自动＝ESEF，可探测 ESRS | `report` |
| ⑥ Indicator | **保留，用 ESRS 受控词表** | `indicator_value` |
| 系统标签 | 适配 ESEF：格式=iXBRL、质量=校验计数、语言可判 | `report` |

真正需要靠抽取去填的只有 ⑥（和由它派生的 ③）。

---

## 10. 显式待定参数（不是问题，是输入；各带建议默认值）

1. **去重保留规则**：默认"error 少、再 processed 新"。若导师倾向保留首报，改一行。
2. **分子口径（PPT 口径一）**：含 ESRS 章节的 ESEF 年报是否计入"ESG 报告"？实测结论：**UK 的 ESEF ≠ ESRS**，建议 UK 仅在 `esg_flag=1` 且锚点命中真 ESRS 时计入；EU FY2024 才是标准 ESRS。
3. **下载范围（PPT 口径二）**：**已实测简化**——GB 在库中无 UKSEF 桶，全部 ESEF/GB，无需"要不要连 UKSEF"的纠结。
4. **锚点召回**：现锚点漏掉 L&G 类大报告，Phase 1 需扩 TCFD/SECR/气候措辞后重测。
5. **路透代码 ↔ LEI 映射（PPT Step 2）**：把覆盖率区间收敛为单值的前提，需建映射表。
6. **分母面板更新**：止于 2023，2024–2025 需更新在册名单。
7. **存储**：GB ~40GB；全财年/扩欧盟需预留更大空间。

---

## 11. 不变量与质检（"无问题"的可测试保证）

建库后跑以下检查，任何一条不通过即说明数据有问题：

1. `report` 中每个 `report_id` 对应**恰好一个** `sha256` 且全表 `sha256` 唯一。
2. `SUM(report.n_versions)` ＝ `COUNT(report_raw)`（去重前后份数对账）。
3. 无孤儿外键：`report.lei` 全部存在于 `entity`；`indicator_value.report_id` 全部存在于 `report`。
4. 覆盖率 `Y ≤ X`（已收集不超过在册）逐 (country, year) 成立。
5. 下载包 `sha256` ＝ 索引记录值（落盘即校验）。
6. `indicator_value.esrs_code` 全部在 `esrs_datapoint` 受控词表内（无自由标签漏入）。
7. `report` 对 `(lei, period_end)` 与 `fxo_id` 唯一约束零违规。
8. `report_id` 在固定排序（按 `lei, period_end`）下重跑可复现。

> 这 8 条通过，索引层就是自洽且可复现的——这才是"without any problem"能达到的、诚实的标准。
