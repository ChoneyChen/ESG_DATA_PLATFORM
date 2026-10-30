# 独立条款模块

`clauses/<展示组>/<条款ID>/clause.json` 是每条的完整权威声明。组只有名字和成员；命名集合只是条款ID列表，不产生另一条执行路线。

可执行条款声明两类值之一、定义与词典、阅读规则、搜索计划输入、捕获/补充/目标/比较用途以及结果布局。缺失可选属性不阻止接收真实原值；范围完整度只决定比较等后续用途。`schemas/clause.schema.json` 是生成接口，`src/esg_clauses` 是独立SDK。

SDK只依赖轻量`esg-contracts`和Pydantic，可单独构建安装；不导入文档、提取、模型或数据库。数据仓储通过显式根目录读取，部署时将本模块`clauses/`作为版本化资产提供，并以`ESG_CLAUSE_ROOT`配置。当前编辑改变摘要，新运行冻结自己的副本；既有冻结快照不被重写。

```sh
python -m pip install -e ExtractESG/contracts -e ClauseESG
```

`compile_search_plan(clause)`返回完整、可追踪的页/对象检索计划。对物理页尚不能执行的对象规则明确延后到分段后的检索，而不静默丢弃字段。

结构标准条目使用独立 `clause-descriptor-1.0`，仍保留一条一目录，但没有data_kind或值合同。目录4.0通过entry_kind区分value/structure；提取只接收ClauseContract，结构成员关系不构成执行依赖。`selection_options`提供明确的可执行与排除原因，技术ready与真实样本验收分开。[37项逐条处置](../docs/implementation/2026-10-31/clause-dispositions.md)。
