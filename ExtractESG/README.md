# ExtractESG · 提取引擎

`ExtractESG` 是平台的数据生产引擎：把报告从 PDF 变成**可追溯、可复核、可发布**的结构化事实。
它不提供人工界面——操作界面是 [ConsoleESG](../ConsoleESG/README.md)，公开数据产品是 [PlatESG](../PlatESG/README.md)。

## 两个服务和一组库

| 目录 | 职责 | 端口 |
|---|---|---|
| `document_ir_extract/` | Document API 宿主：资产、连续处理（OCR→DocumentIR）、报告会话、统一队列 | 18080 |
| `targeted_table_extract/` | Targeted API 宿主：固定证据、条款投影、事实与成果包 | 18180 |
| `document_processing/` | PDF/OCR/DocumentIR、模型适配、资产生命周期 | 库 |
| `control_plane/` | 唯一重任务队列、处理计划、事件与恢复 | 库 |
| `extraction/` | 检索、来源事实、映射、`foundation16/32` 条款引擎 | 库 |
| `standards/` | 标准目录、作者源、编译与发布、profiles | 库 |
| `contracts/`、`shared_runtime/` | 共享类型、进度与路径合同 | 库 |

Python 包名仍是 `esg_v2`、`esg_targeted`；旧导入路径是指向同一份源码的符号链接。

## 数据流

```text
PDF（SHA-256 资产身份）
  → 页面读取：文字层优先，图片页才走本地视觉识别          ocr_output/
  → DocumentIR：正文、表格单元格、逻辑表、跨页、坐标        document_ir_output/
  → 条款提取：每张候选表一次本地 Qwen 受约束调用
             ＋确定性绑定、守卫判定、跨来源归并            targeted_extract_output/
  → 成果包：来源事实、标准映射、证据位置
  → 交付到数据平台候选 → 审核 → 发布
```

同一份报告重新提取会复用兼容的读取版本与已保存的回答；证据版本、标准版本与结果版本都不可变。

## 指标集

- `base16`：E1–E3 环境核心 16 条。
- `priority32`：基础 16 + 16 条（化石能源、排放总量与强度、废弃物、材料、员工、治理）。

指标集是数据（`extraction/src/esg_targeted/foundation/catalog.py` 的 `PROFILES`），引擎按请求里的
`metric_profile` 运行；条款包、链路与报告三者互不绑定。

规则从可推广的类别来：单位与标签判定、许可上限与实际排放、跨页表格续接、显式合计列、父/子条款、
覆盖率与比率区分、索引行与分类行不算总量。**不为任何一份报告写特例。**

## 本地启动

```bash
ExtractESG/scripts/start_local_workbenches.sh     # 两个后端 + 操作台静态入口
```

也可以只跑后端（`document_ir_extract/scripts/run_backend.sh`、`targeted_table_extract/scripts/run_backend.sh`）；
操作台关闭不影响 API 与 CLI。

## 测试

```bash
pytest ExtractESG/document_ir_extract/backend/tests      # 657
pytest ExtractESG/targeted_table_extract/backend/tests   # 697（16 项既有失败）
pytest ExtractESG/standards/tests                        # 116
```

金标回归在 `.local/foundation-lab/`（`reg_only.sh` 只重跑提取，`score.py` 打分）。

## 与操作台的分工

- 操作台决定**做什么**：选哪份报告、哪套指标、审核与发布。
- 引擎决定**怎么读**：选页、版面、直读还是视觉识别、如何绑定与判定。
- 引擎不写操作台的状态、不决定发布；操作台不解析 PDF、不调用模型。
