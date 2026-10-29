# PDF → 条款事实

| 模块 | 所有权与接口 |
|---|---|
| `standard/<组>/<条款>/clause.json` | 每条定义、定性/定量、词典、口径、单位、阅读规则、证据要求和验收要求；组只用于展示 |
| `contracts/src/esg_clauses` | 条款结构、摘要校验、读写、选择和任务冻结，不调用模型 |
| `contracts/src/esg_targeted/data_kinds` | 定量原值和定性原文两份准入合同；元数据可缺失、可扩展 |
| `document_processing` | PDF 页面、OCR、IR、物理证据对象；只接受检索意图，不读取条款目录 |
| `extraction/src/esg_targeted/pipeline` | 单一提取骨干，依赖文档、模型、产物三个端口；按所选条款分别读取 |
| `control_plane` | PDF 准备和提取的调度、任务状态、取消；统一队列串行占用模型工作槽 |
| `extraction/.local` | 当前运行状态与原值事实产物。路径由本模块所有，不跨服务传递 |

校验：文档服务 Python 运行 `scripts/check_contracts.py`。修改结构后用 `--write` 生成 JSON Schema 与索引，再复查。

启动：`ESG_SKIP_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 scripts/start_local_workbenches.sh`。
详见仓库根目录当前手册和 `docs/CLAUSE_BACKBONE_REBUILD.md`。
