# 系统状态快照（2026-10-17）

本文记录 2026-10-17 第二轮结束、推送前的系统实际状态：代码、服务、模型、数据位置、16 项链路的实测质量与速度、测试结果和已知问题。它是日期化快照；之后的当前事实仍以[根 README](../README.md)为准，变化记录见[更新日志](CHANGELOG.md)。

## 1. 代码与提交

- 上一次提交为 `7ac8408`（2026-09-19）。本次推送收录此后全部工作树改动：P4–P7 工作台与公开产品、P6 结构迁移（唯一源码目录 + 相对符号链接兼容）、标准系统、数据库代码、foundation16 快速链路及本轮的文字层直读。`ConsoleESG/` 首次纳入版本控制（依赖与构建产物按其 `.gitignore` 排除）。
- 未纳入提交：`CrawlESG/` 保护区的工作树改动（`docs/MANUAL.docx` 的删除不是本项目改动，保持原样未暂存）；本机工具配置 `.claude/`。
- 根 `.gitignore` 新增 `node_modules/`：`database/node_modules`（PGlite 依赖，约 25 MB）按 `pnpm-lock.yaml` 重新安装，不入库。
- 凭据：工作台访问密钥在 `PlatESG/.local/review-token`，`.local/` 整体不入库；提交前扫描了待提交文件，只有测试用的假密钥字符串。

## 2. 服务与端口

| 服务 | 地址 | 说明 |
|---|---|---|
| Document API | `127.0.0.1:18080` | 报告资产、连续处理、平台会话、统一队列；foundation16 看板代理 `GET /api/report-sessions/{id}/foundation16` |
| Targeted API | `127.0.0.1:18180` | 固定 IR、原生会话、16 项结论 |
| Console 静态入口 | `127.0.0.1:18081` | 正式操作台；开发服务 `5180` |
| PaddleOCR-VL 推理服务 | `127.0.0.1:8111` | 本地 OCR 需要视觉识别时自动启动，之后常驻 |
| PlatESG | `127.0.0.1:8765` | 本轮未运行；由用户按 `PlatESG/scripts/run.sh` 启动 |

启动（非交互）：`ESG_PLATFORM_NO_BROWSER=1 ESG_SKIP_DEPENDENCY_INSTALL=1 ESG_PLATFORM_NONINTERACTIVE=1 ./ExtractESG/scripts/start_local_workbenches.sh`。服务启动时冻结源码指纹，源码变化后需重启，否则业务提交返回 `503 runtime_requires_restart`。

OCR、IR 与提取共用一个重任务执行槽，队列在 `../.local/platform-runtime/pipeline-queue.sqlite3`。

## 3. 本地模型与运行时

16 项链路全部在本机运行，不调用云服务。平台 OCR 默认 `local_paddleocr`、`allow_cloud_ocr=false`。

| 用途 | 模型 / 组件 | 位置 | 运行方式 |
|---|---|---|---|
| 版面区域与阅读顺序 | PP-DocLayoutV3 | `~/Desktop/model/.cache/paddlex` | CPU，Paddle 运行时 `~/Desktop/model/.runtime/paddleocr-vl` |
| 视觉识别（文字层不可用的页） | PaddleOCR-VL-1.6（0.9B） | `~/Desktop/model/PaddleOCR-VL-1.6` | mlx-vlm 推理服务 `:8111` |
| 表格理解（每表一次调用） | Qwen3.5-9B-4bit | `~/Desktop/model/Qwen3.5-9B-4bit` | MLX，llguidance JSON Schema 约束解码，贪心 |
| 扫描件选页、标题补读 | macOS Vision | 系统框架 | Swift 小工具，首次使用时编译到 `~/Library/Caches/ESG_DATA_PLATFORM/` |

开关（环境变量，默认开启）：`ESG_V2_OCR_TEXT_LAYER`（数字版页面文字层直读）、`ESG_V2_OCR_VISION_PRESCAN`（扫描件本机选页与标题补读）。设为 `0` 即回到全部页面走视觉识别。

## 4. 数据位置

| 内容 | 位置 |
|---|---|
| 原 PDF | `../pdf/`（仓库外） |
| OCR 成果 | `ExtractESG/document_ir_extract/ocr_output/` |
| Document IR | `ExtractESG/document_ir_extract/document_ir_output/` |
| 平台运行库（PGlite） | `PlatESG/.local/demo-data`；不重置、不迁移，`PLATESG_AUTO_PUBLISH=0` |

## 5. 16 项链路（当前）

```
PDF
 └─ 选页：PDF 文字层逐页打分（无文字层的扫描件：本机 Vision 读出页文字后打分）→ 约 25 页
     └─ 读页：版面模型找区域 → 文字层可用的页按区域取字、重建表格
     │        文字层不可用的页（扫描、图片表格、字体无法映射、倾斜文字）→ PaddleOCR-VL
     │        文字层为空的标题区域 → 本机 Vision 补读
     └─ 稀疏 Document IR（原页码，不复核）
         └─ 每张候选表一次 Qwen 受约束调用 → 确定性绑定 → 正文补查 → 跨来源归并 → 按标准包写成果包
```

## 6. 质量与速度（本轮实测）

最终批次：9 份报告各自从 PDF 冷启动（OCR → IR → 16 项提取 → 对金标准逐项核对），金标准共 159 项。

| 报告 | 正确 | 偏差 | OCR | IR | 提取 | 合计 |
|---|---|---|---|---|---|---|
| report00004（英文） | 16/16 | — | 58.2 s | 14.5 s | 50.8 s | 124.2 s |
| 隆基 | 24/24 | — | 31.8 s | 6.9 s | 119.0 s | 158.1 s |
| 中国移动 | 16/16 | — | 74.9 s | 11.0 s | 167.4 s | 254.0 s |
| 腾讯 | 16/16 | — | 57.2 s | 25.7 s | 114.0 s | 197.7 s |
| 紫金矿业 | 29/29 | — | 57.5 s | 9.8 s | 492.5 s | 560.4 s |
| 中国平安 | 16/16 | — | 52.2 s | 6.8 s | 96.9 s | 156.7 s |
| 阿里巴巴 | 12/12 | — | 58.9 s | 8.6 s | 108.2 s | 176.6 s |
| 宁德时代 | 14/15 | 1 项金标准待复核 | 42.4 s | 12.0 s | 174.5 s | 229.4 s |
| 中国银行 | 14/15 | 1 项待复核 | 155.8 s | 16.2 s | 162.2 s | 335.1 s |
| **合计** | **157/159** | **2** | 32–156 s | 7–26 s | 51–493 s | 124–560 s |

- 两处偏差都不是漏值：宁德时代的 `E2-4_09` 找到了真实的“VOCs 排放总量基于物料衡算法统计”，金标准当时记为“未披露”，应按金标准问题复核；中国银行 `E1-5_09` 是 2024 年“39 栋办公楼碳中和”口径，留在待复核、未当正确值。9 份报告 0 漏值、0 错值。
- 提取耗时包含同机并行跑测试与其它任务的抢占；单独跑时紫金约 330 s、其余 50–170 s。OCR 侧 20 页以上的版面检测稳定在 25–35 s，失败页的视觉识别是主要波动来源（银行 156 s，其倾斜文字页与图片表格页走了视觉识别）。
- 复用已有回答重新提取约 12 s（0 次模型调用），仅重跑提问集合变化的那一个文本任务约 5–15 s。

## 7. 测试

| 套件 | 结果 |
|---|---|
| 文档后端 `document_ir_extract/backend/tests` | 586 通过（`test_operation_payload_contracts.py` 因本机 venv 缺 `jsonschema` 无法收集，属既有环境问题） |
| 文字层直读 `tests/test_text_layer_route.py` | 10 通过 |
| 16 项提取 `tests/test_foundation16.py` | 25 通过 |
| Console `npx vitest run` | 207 通过（21 个文件） |
| Console `tsc -b` | 通过 |

## 8. 已知问题与风险

- **金标准问题**：宁德时代 `E2-4_09` 的实际披露（物料衡算法）与金标准“未披露”冲突；腾讯 `E1-6_09` 的同页方法说明同时给出市场法与位置法，金标准按“位置法数值”记，规则按“行未声明方法”留待复核，属口径分歧，不是漏值。
- **模型失败不重试**：腾讯本轮一次文本调用因输出触顶返回 `model_failed`，得出 15/16；修正提问（E2-4_09 规则只在请求该指标时出现）后重跑即 16/16。当前 runner 不自动重试失败调用，批量里单次失败就会记成缺项。
- **中国银行 `E1-5_09`**：2024 年办公楼碳中和比例被识别为可再生电力占比，留在待复核；这是上一轮已记录的口径问题。
- **速度波动**：视觉识别页和紫金这类大表报告仍是长尾；本机同时运行其它重任务时提取速度减半（PaddleOCR-VL 服务常驻会与 Qwen 争 GPU）。
- **扫描件**：真实扫描件的本机 Vision 读数是近似的，数字偶有误差；目前只用于选页与标题补读，数值仍走视觉识别页。
- **发布**：数据库、审核发布闭环不在本轮改动范围；`ConsoleESG/` 依赖、构建产物不入库，克隆后需重新安装。
