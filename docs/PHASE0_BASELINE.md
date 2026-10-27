# 阶段 0：真实基线与七阶段实施入口

日期：2026-10-28  
范围：真实报告、正反例、提取字段、自动审核、人工修改、耗时与内存、同对象模型对照。

## 1. 固定输入与可重复命令

样本分为三层，不能混算：

1. **历史金标**：9 份真实报告，159 个原子期望（102 数值、4 存在性、53 负例）。
   金标主要证明值与报告级存在/不存在；不代表逐字段全量审计。
2. **独立全字段金标**：中国石油 PDF 第 2、90、91 页，30 个五年期单元格与正反例。
   原始来源为 `ExtractESG/tests/fixtures/business/cnpc-2025-esrs-major-gold.v1.json`。
3. **同表模型对照**：三张真实表，16 个正值、9 个明确反例。
   固定在 `ExtractESG/tests/fixtures/business/phase0-model-comparison-gold.v1.json`；
   覆盖多主体/多年期、两种 Scope 2 方法、Scope 3 分类、空气/水体污染物维度。

大型 IR、历史调用和模型产物保留在工作区 `.local/foundation-lab` 与仓库 `.local/phase0-baseline`，不提交到公开仓库。
基线工具通过显式路径读取，不写报告资产、产物目录或生产数据库。没有服务或云端调用。

```bash
PYTHONPATH="ExtractESG/targeted_table_extract/backend/src:ExtractESG/standard_packages/src:ExtractESG/shared_runtime/src:ExtractESG/extraction/src" \
  /Users/choney/Desktop/model/.runtime/venv/bin/python scripts/phase0_baseline.py replay \
  --lab "/Users/choney/Desktop/ESG  Database/.local/foundation-lab" \
  --output .local/phase0-baseline/final \
  --cnpc-ir .local/phase0-baseline/cnpc-evidence/ir/ir-20261027T181936Z-1584ad99ea49 \
  --cnpc-calls "/Users/choney/Desktop/ESG  Database/.local/foundation-lab/runs/cnpc-r3/calls.jsonl"
```

```bash
PYTHONPATH="ExtractESG/document_ir_extract/backend/src:ExtractESG/document_processing/src:ExtractESG/standards/src:ExtractESG/shared_runtime/src" \
  ExtractESG/document_ir_extract/backend/.venv/bin/python scripts/phase0_prepare_evidence.py \
  --pdf "/Users/choney/Desktop/ESG  Database/pdf/2025中国石油.pdf" \
  --pages 2 90 91 --output .local/phase0-baseline/cnpc-evidence
```

```bash
PYTHONPATH="ExtractESG/targeted_table_extract/backend/src:ExtractESG/standard_packages/src:ExtractESG/shared_runtime/src:ExtractESG/extraction/src" \
  /Users/choney/Desktop/model/.runtime/venv/bin/python scripts/phase0_model_comparison.py \
  --lab "/Users/choney/Desktop/ESG  Database/.local/foundation-lab" \
  --output .local/phase0-baseline/model-comparison --providers local_qwen local_nuextract
```

`--reuse` 只重放已保存的原始回答，不调用模型。每种 provider 单独运行后，再用两个 provider 加 `--reuse` 合并。
默认生产 provider 不变。真实三页证据重建只用原生文字层与布局，无视觉 OCR，实测 9.45 秒。

## 2. 指标定义

- 数值召回：金标正值中数值等价的比例；比较去千分位，不用倍率容差掩盖来源差异。
- 期间/主体/边界/单位：分别对照金标，不把“值对”当作其他字段也对。
- 维度保留率：实际报告成员（例如 COD、NOx、VOCs）是否各自保留，不用主值覆盖成员。
- 历史来源保留：当前期提取之外，已审计历史年份是否仍形成来源观察；不与当前期召回混算。
- 自动批准率：事实、映射、测量口径三者均由自动核验批准的映射数 / 去重映射数。
- 人工修改量：仅由显式人工 ledger 的 original/final 字段差异计算；没有 ledger 就标 `not_measured`。
- 未覆盖：没有提出候选，不等于正确的语义拒绝；正确拒绝、未提出、误接受分别计数。
- 未知：`unknown` 或缺失字段记不可测，不记正确。

## 3. 真实基线结果

### 3.1 历史回归

| 口径 | 结果 |
|---|---:|
| 原子期望 | 159 |
| 严格原值与存在/负例一致 | 154/159 |
| 数值正值 | 100/102 |
| 正值与存在性召回 | 104/106 |
| 负例一致 | 50/53 |

与旧记录 158/159 的差异不是本轮引入的模型退化：旧评分接受倍率差异、只看首选成员且不检查额外成员。
本轮固定回答后按原值重算，发现：

- 中国银行把 `Energy consumption per FTE (MWh) = 8.78` 当作总能耗；
- 中国银行把 `Water consumption per FTE (m³) = 40.39` 当作总耗水；
- 宁德时代 `E2-4_09` 已知误报；
- 紫金 `E1-5_05`、`E3-4_03` 仍是报告级待复核候选，不能直接判错或放行。

历史 `gold-check-tl4r.json` 只对数值文本和引用页有较强证明；主体、边界、单位、历史期间与负例仍需按上述全字段金标或人工 ledger 补齐。

### 3.2 中国石油全字段

| 指标 | 结果 |
|---|---:|
| 当前期独立污染物来源（COD、VOCs、NOx） | 3/3 |
| 历史期来源保留 | 0/12 |
| 污染物维度保留（15 个五年期单元格） | 3/15 |
| 当前期值/期间/单位/污染物 | 3/3 正确 |
| 当前期集团边界 | 0/2 可判定（`unknown`） |
| 炼化边界 | 0/1（来源未提升为结构化边界） |
| 正负例合计 | 1 正确拒绝、3 正值保留、12 历史未覆盖、14 负例未覆盖 |

当前 foundation 只选择一个当前期列，历史单元格不是当前期观察。后续须区分“保留所有报告期间的中立事实”与“选择当前期条款观测”，不能只加宽倍率容差。

`foundation.consolidate.brief()` 目前丢失 `period_year` 和成员级 `entity_basis`；基线评分从原始观察补入，未掩盖这项最终成果字段缺口。阶段 2 要把它与字段级证据一起保留。

### 3.3 自动审核与人工修改

阿里巴巴 16 个真实 Bundle 在**内存 PGlite** 中完整交付，13 条映射自动批准 11 条，2 条保留人工，自动批准率 **84.6%**。
两条事实、映射与测量口径各需处理，阻塞为映射不完全、原始行标签及维度不完整。未发生发布。

| 报告 | 实测结论 |
|---|---|
| 阿里巴巴 | 13 候选，11 自动批准，2 待人工；已重复验证隔离交付 |
| 中国石化 | 自动审核被 `aggregation_role` 的归一值重复进入科学维度阻塞；阶段 1/2 修正归一合同 |
| 宁德时代 | 同一物理单元格去重后，来源解释载荷差异被判碰撞；保留校验，阶段 2 分开物理去重与解释版本 |

人工修改量为 **未测量**：自动批准不是人工编辑，旧 `review_decisions` 也没有完整 original/final 差异。
后续审核采用显式 ledger，记录每条人工修改的字段、前后值、原因与时间，才能形成有意义的修改率。

```bash
database/scripts/run-node.sh scripts/phase0_review_baseline.mjs \
  --bundle-root ExtractESG/targeted_table_extract/targeted_extract_output \
  --document-id doc-sha256-4c11d4ccc30e8c1d7ccd352760c17938d73e397c241ee9de6d78418c70e58c0e \
  --output .local/phase0-baseline/review-alibaba
```

工具支持任意显式只读 Bundle 根和可选 `--ir-run-id`。混合 IR 不能用于同一报告的去重审核基线。

### 3.4 同对象 Qwen / NuExtract

实测硬件：Apple M5、32 GB。模型串行加载，未同时驻留；仅本地 MLX，无云调用。

| 指标 | Qwen3.5-9B-4bit | NuExtract3-mlx-4bits |
|---|---:|---:|
| 正值召回 | 16/16 | 16/16 |
| 数值/期间/单位/污染物维度 | 16/16 | 16/16 |
| 集团边界已证明 | 11/16 | 11/16 |
| 边界不可测 | 5/16 | 5/16 |
| 明确负例：正确拒绝 | 1/9 | 3/9 |
| 明确负例：未提出 | 8/9（确定性行补全后） | 6/9（诊断） |
| 明确负例：误接受 | 0/9（确定性行补全后） | 0/9（诊断） |
| 三张表实际调用耗时 | 41.72 秒 | 40.89 秒 |
| 最大 MLX peak memory | 7.77 GB | 4.89 GB |
| 模型加载 | 3.02 秒 | 1.21 秒 |
| 输入/输出 token | 10,424 / 531 | 10,036 / 1,359 |

Qwen 表中为共享协议修订前实际回答经确定性行补全后的最终诊断；原始回答保留在 `.local/phase0-baseline/model-comparison`。
后续对照应分别报告“模型自身映射”与“最终链路结果”，不把程序行恢复冒充模型语义能力。

NuExtract 原始回答在末尾带 `<|im_end|>`，foundation 原先直接 `json.loads` 导致三次 `invalid_json`，生产合同 0/16。
本轮复用 standard 已有的严格首个 JSON 解析，并在绑定层拒绝 `null` 目标。此后 NuExtract 16/16 是**协议兼容后的诊断结果**，
不是已切换生产 provider 的证明。三张表不足以决定模型替换；NuExtract 对 Scope 3 分类与强度反例仍比 Qwen 更宽松。

## 4. 本轮实际代码变化

- `scripts/phase0_baseline.py`：历史与全字段基线，逐例 JSON/CSV。
- `scripts/phase0_prepare_evidence.py`：隔离选页证据准备；显式选页也使用文字层 + 布局。
- `scripts/phase0_model_comparison.py`：同一 IR、同一请求、同一绑定的模型对照与资源记录。
- `scripts/phase0_review_baseline.mjs`：真实 Bundle 的内存交付与自动审核；发布恒为零。
- foundation 模型记录增加 provider、模型加载与峰值内存；输出合同共享标准 JSON 协议。
- 新增对应 Python/Node 回归测试。

## 5. 解耦与效果边界

报告资源、产物、链路、条款包和数据库服务必须职责清晰、通过版本化合同连接；
本来就合理关联的组件可以保留连接，不机械追求物理拆分。最终以真实提取准确、可泛化、可用为首要目标。

当前基线只注入只读输入、写隔离目录。审核工具不依赖生产库，文档工具不依赖生产 OCR/IR 根，模型实验不改变生产路由。

## 6. 七阶段执行清单

后续每阶段必须复跑受影响基线、真实隔离链路和模块回归，不以“测试通过”替代语义正确。
`PlatESG/frontend` 不在本轮范围；Console、Extract 全链路、Plat 后端与数据库全部纳入。

| 阶段 | 交付与验收 | 已固定的真实问题 |
|---|---|---|
| 1 统一条款科学合同 | 标准包生成平台字典、foundation catalog/specs 与审核归一；一次合同修改传播检索/提取/审核/比较；冻结旧任务 | `aggregation_role` 重复归一；字典与标准包双写 |
| 2 中立事实与路线 | 两路线同一 SourceFact；固定分类与报告细分类并存；字段证据贯穿审核/Observation | 历史期 0/12；边界未提升；去重与解释版本冲突 |
| 3 文档与增量搜索 | 共享文字层/布局、合同驱动选页、报告证据索引与增量映射 | 显式选页文字层已补；避免新条款全本 OCR |
| 4 科学数据产品 | 观测维度、证据、来源、覆盖状态与规范 CSV/Parquet；显式适配不改旧快照 | 维度与字段证据不可完整公开 |
| 5 条款中心 | 草稿/编译/试提取/验收/发布/退役，原条款与延伸统一生命周期 | 未通过真实验收不得标可发布 |
| 6 Console 重构 | 统一报告状态/产物投影与单批量请求，收编诊断 | 报告六轴状态，审核与发布不再各拼一套 |

每个阶段的实际完成状态、基线对比、未完成项将追加在 `docs/CHANGELOG.md` 与 `docs/SYSTEM_STATE.md`，不把本清单当作已交付声明。
