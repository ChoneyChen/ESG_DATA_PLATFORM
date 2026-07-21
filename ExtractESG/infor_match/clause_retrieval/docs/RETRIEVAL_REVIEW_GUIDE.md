# 检索合并表阅读说明

`outputs/retrieval_method_comparison_review_queue.csv` 每行表示“某个报告印刷页可能对应某条官方条款”。

- `printed_page_label`：报告印刷页码；
- `candidate_clause_id` / `candidate_title`：条款库中的候选 ID 和标题；
- `lexical_score` / `lexical_rank`：关键词/同义词方法的分数与页内名次；
- `vector_score` / `vector_rank`：TF-IDF 余弦相似度与页内名次；
- `retrieved_by`：候选来自哪种方法；
- `agreement=both_methods`：两种方法都召回，适合优先复核；
- `review_state=review_required`：尚未形成审核结论；
- `evidence_locator`、`review_decision`、`review_notes`：留给后续复核填写。

两类分数的量纲不同，不能相加，也不能解释成合规概率。两种方法同时召回只提高复核优先级，不证明映射成立。

