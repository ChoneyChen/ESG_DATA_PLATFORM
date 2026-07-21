# ESG 条款候选检索工作包

本目录是当前可复现、可供组内审阅的最小工作包。输入为 Document IR，输出为“可能相关的官方条款候选”和 Evidence 复核队列。它不会判断企业已经满足某项标准。

## 当前进度（2026-07-20）

- 测试输入：越秀地产 2024 年可持续发展报告印刷页 62–67 的团队 Document IR，已缩减为不含个人电脑路径的文本夹具。
- 条款库：49 条带官方来源定位的样本记录，包括 GRI、HKEX Appendix C2 和 IFRS S2。该库仍是样本库，不是完整标准全集。
- 词典：27 条官方标准/规则用语，加 10 条公司官方报告原词。公司报告用语与标准用语分开存放。
- 关键词/同义词召回：17 条候选。
- TF-IDF 稀疏向量召回：30 条候选。它是真实向量计算，但不是神经网络 embedding。
- 合并去重：33 条候选，其中 14 条被两种方法同时召回，19 条仅被一种方法召回。
- 原文片段定位：18 条找到直接文本行，15 条需要回到表格、图片或更细粒度结构继续核验。
- 当前演示性首轮复核：22 条保留候选、3 条待确认、8 条排除。这里的“保留”仍不等于条款满足。
- 云 embedding：已提供适配器，但尚未用获批准的服务商、模型和接口做真实验证，因此没有上传云端分数。

## 目录

```text
clause_retrieval/
├─ data/
│  ├─ clauses/          # 条款库样本：ID、标题、主题、版本、官方 URL、官方定位
│  ├─ dictionaries/     # 概念表、官方同义词、公司报告原词
│  └─ fixtures/         # 已清理个人路径的最小 Document IR 测试输入
├─ scripts/             # 关键词、TF-IDF、合并、Evidence 队列和网页生成
├─ examples/            # 仅针对当前 6 页测试夹具的演示性人工决定
├─ outputs/             # 本次可审阅输出和空白/载入版工作台
├─ docs/                # 来源政策、云 embedding 和人工复核说明
└─ tests/               # 数据结构与安全边界检查
```

## 快速复现

建议 Python 3.9 或更高版本。

```powershell
cd ExtractESG\infor_match\clause_retrieval
python -m pip install -r requirements.txt
python scripts\retrieve_from_document_ir.py
python scripts\run_vector_retrieval.py
python scripts\compare_retrieval_methods.py
python scripts\build_evidence_review_queue.py
python examples\apply_demo_review_decisions.py
python scripts\build_workbench.py
python scripts\build_workbench.py --blank
python -m unittest discover -s tests -v
```

运行顺序不能随意调换：后一阶段读取前一阶段的 CSV。`apply_demo_review_decisions.py` 只为了重现本次 6 页样本的首轮判断，不能当成通用自动审核器。

## 输入契约

默认输入是 `data/fixtures/yuexiu_2024_pages_62_67/document_ir.json`。检索当前只依赖：

- `metadata.run_id`：本次 Document IR 运行标识；
- `pages[].page_id`：结构化页 ID；
- `pages[].page_index`：从 0 开始的页序号；
- `pages[].printed_page_label`：报告印刷页码；
- `pages[].text`：该页规范化文本。

Evidence 片段定位还会读取同目录下的 `canonical_markdown/page_XXXX.md`。未来对接完整 Document IR 时，应由上游适配层保证这些字段稳定，而不是修改条款数据。

## 主要输出怎么读

- `document_ir_retrieval_candidates.csv`：可解释关键词/同义词候选。`matched_terms` 是实际命中的表达，`score` 是启发式分数。
- `document_ir_vector_candidates.csv`：TF-IDF 字符 n-gram 余弦相似度候选。`score` 只用于同一方法内部排序，不能直接解释为满足概率。
- `retrieval_method_comparison_review_queue.csv`：按“页码 + 条款 ID”合并两种方法。`agreement=both_methods` 表示两种方法都找到了它，不代表条款一定对应。
- `evidence_review_queue.csv`：增加 `ir_source_file`、`ir_source_line`、`evidence_snippet` 和 `evidence_status`，供人工回到原文定位。
- `evidence_reviewed_candidates.csv`：加入本次演示性首轮 `manual_decision` 和理由。
- `esg_retrieval_workbench_blank.html`：组会现场从空白状态载入 Document IR。
- `esg_retrieval_workbench.html`：预载本次测试材料和结果，便于直接审阅。

所有输出中的 `suggested_relation=candidate_only` 都表示“只是一条候选关系”。形成正式 Evidence 仍需核对 PDF/表格/图片原件、精确定位和审核人决定。

## 已知边界

1. 当前只有 6 页测试输入，不能用来评价系统在完整报告或跨行业报告上的召回率。
2. 条款库只有 49 条，尚未覆盖全部主流 ESG 标准及完整版本演进。
3. TF-IDF 是可复现基线；云 embedding 只有安全适配器，尚无经验证结果。
4. 浏览器工作台里的现场召回是关键词演示；离线 TF-IDF 结果来自 Python 脚本，不应混称为同一个模型。
5. 人工复核的长期目标是建立高质量标注集、阈值和自动规则，但现阶段不能跳过人工证据核验。

来源和防虚构边界见 [`docs/OFFICIAL_SOURCE_POLICY.md`](docs/OFFICIAL_SOURCE_POLICY.md)。

