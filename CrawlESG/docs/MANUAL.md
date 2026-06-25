# CrawlESG 使用说明书

> 适用对象：项目组成员、对接同学、后续接手者。
> 本手册讲"怎么用这个工具"。配套文档：`README.md`（速查）、
> `INTERFACE_接口约定.md`（对接提/搭）、`docs/RUNBOOK_云服务器下载与协调.md`（服务器+多人协作）、
> `schema/设计方案_v1.md`（为什么这么设计）。

---

## 目录
1. 这个工具是做什么的
2. 环境与安装
3. 必须先懂的 5 个概念
4. 四个命令详解（index / plan / download / verify）
5. 标准作业流程（从零到一份完整数据集）
6. 输出文件说明（每个文件、每个关键字段）
7. 常见问题与排错
8. 参数速查表

---

## 1. 这个工具是做什么的

CrawlESG 是 ESG_DATA_PLATFORM 的"爬"模块。它从 **filings.xbrl.org**（XBRL International 官方
ESEF/UKSEF 文件库）做两件事：

1. **建索引**：把某国/某年所有上市公司年报的元数据采下来，去重，整理成一张"有哪些报告"的清单（CSV）。
2. **下载报告**：按这张清单把报告包（含财务 iXBRL ＋ 部分公司的 ESRS 可持续声明）下载到本地。

它**不做**抽取——把数值从报告里抠出来是 ExtractESG 的活。本模块只负责"把货齐整地搬到本地、并交一张清单"。

核心工具是 `src/esef_crawler.py`，专为云服务器长跑设计：**可随时 Ctrl-C、断网、重启，重跑同一条命令自动续传，不会重复下载、不会留半个文件。**

---

## 2. 环境与安装

要求：Linux / macOS，Python 3.8+。

```bash
# 1) 拿到代码（已在仓库 CrawlESG/ 下）
cd CrawlESG

# 2) 建虚拟环境（推荐，避免污染系统 Python）
python3 -m venv .venv
source .venv/bin/activate

# 3) 装依赖
pip install -r requirements.txt        # 仅需 requests、pandas
```

验证装好了：
```bash
python src/esef_crawler.py --help      # 看到 index/plan/download/verify 即正常
```

> 单实例锁用到 `fcntl`，仅 Linux/macOS 原生支持；Windows 请在 WSL 或服务器上跑。

---

## 3. 必须先懂的 5 个概念

**① 索引与下载是分开的两步。** 先 `index` 拿到"有哪些报告"的清单（很轻，只是元数据），
再按清单 `download`（很重，几十 GB）。永远先建索引、看清楚范围，再决定下什么。

**② `report_id` 是每份报告的身份证。** 形如 `R000001`，由 `index` 自动、确定性地生成
（同样输入重跑得到同样的号）。抽取、入库、对接全靠它串起来。

**③ `sha256` 才是文件的真身份。** 报告是否重复、下载是否完整，都看 sha256，不看网址。
下载的文件名里就嵌了它的前 8 位，例如 `<LEI>_<财年截止日>_<sha8>.zip`。

**④ 一份报告 = 一个 (公司 LEI, 财年截止日)。** 同一家公司同一财年可能有多份重报，
`index` 会自动按"错误最少、再最新"收敛成一份权威版（`n_versions` 记录收敛了几份）。

**⑤ 财务和 ESG 取法不同。** 财务数值直接用报告里的 `json_url`（xBRL-JSON，结构化）；
ESG 走报告包正文的文本抽取。下载时的 `esg_flag` 只是"这个包里疑似有没有 ESRS 声明"的布尔探测，
方便提方排优先级，不是抽取本身。

---

## 4. 四个命令详解

### 4.1 `index` —— 采元数据、去重、出清单

```bash
python src/esef_crawler.py index --country GB --out-dir .
```
作用：分页拉取该国全部申报元数据 → 双层去重 → 写三个文件到 `index/`。
带重试和断点（每 5 页存一次 `index/_index.partial.csv`），断了重跑即可。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--country` | 无（全量） | 国家代码，如 `GB` / `FR` / `FI`。不填则采全欧（很大，慎用） |
| `--out-dir` | `.` | 输出根目录，结果写到其下 `index/` |
| `--delay` | `0.3` | 翻页最小间隔（秒），对免费服务礼貌 |
| `--max-pages` | 无 | 只采前 N 页，**测试用** |

产出：`index/gb_filings_primary.csv`（去重主清单）、`gb_filings_raw.csv`（去重前底稿）、
`gb_coverage_by_year_type.csv`（财年×类型覆盖）。

### 4.2 `plan` —— 干跑，只看不下

```bash
python src/esef_crawler.py plan --index index/gb_filings_primary.csv --years 2024
```
作用：**下载前必看**。告诉你这次会下多少份、估算多少 GB、磁盘够不够，但**不下载任何东西**。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--index` | 必填 | 用哪张清单 |
| `--years` | 无 | 只看某些财年，如 `--years 2023 2024` |
| `--pkg-dir` | `packages` | 包存哪里（用于算"已在本地多少"） |
| `--limit` | 无 | 只看前 N 份 |

输出示例：
```
索引匹配 : 579
已在本地 : 0
待下载   : 579  (估算约 8.5 GB, 按 ~15MB/份)
磁盘可用 : 100.3 GB  -> 充足
```

### 4.3 `download` —— 断点续传下载

```bash
python src/esef_crawler.py download --index index/gb_filings_primary.csv --years 2024 --yes
```
作用：按清单下载报告包。流式落盘、sha256＋zip 双校验、原子改名、限速、单实例锁、durable 台账。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--index` | 必填 | 用哪张清单 |
| `--years` | 无 | 只下某些财年 |
| `--limit` | 无 | 只下前 N 份（试跑用） |
| `--pkg-dir` | `packages` | 包存哪里 |
| `--out-dir` | `.` | 台账/日志存哪里 |
| `--workers` | `4` | 并行数。**对免费服务别调太高，4 足够** |
| `--delay` | `0.4` | 全局最小请求间隔（秒） |
| `--no-esg` | 关 | 加上则跳过 ESG 锚点探测（更快） |
| `--yes` | 关 | 跳过下载前确认（脚本化/无人值守时加） |

产出：`packages/*.zip`、`download_ledger.csv`（逐包台账）、`download_failed.csv`（失败清单，重跑自动补）。

### 4.4 `verify` —— 复检已下文件

```bash
python src/esef_crawler.py verify --index index/gb_filings_primary.csv
```
作用：把本地已下的包逐个重算 sha256、重验 zip 完整性，报告有多少损坏、多少缺失。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--index` / `--years` / `--pkg-dir` / `--limit` | 同上 | |

输出示例：`已校验 579 | 损坏 0 | 索引内但本地缺失 0`。
若有损坏，写 `verify_bad.csv`；删掉这些文件再跑一次 `download` 即重下。

---

## 5. 标准作业流程（从零到一份完整数据集）

```bash
# 0) 进目录、激活环境
cd CrawlESG && source .venv/bin/activate

# 1) 建索引（若仓库已带 index/*.csv 可跳过，或重采更新）
python src/esef_crawler.py index --country GB --out-dir .

# 2) 看范围、估体积、查磁盘（必做）
python src/esef_crawler.py plan --index index/gb_filings_primary.csv

# 3) 先小下 5 个试通
python src/esef_crawler.py download --index index/gb_filings_primary.csv --limit 5 --yes

# 4) 全量下载（服务器上请放 tmux 里跑，见 RUNBOOK）
python src/esef_crawler.py download --index index/gb_filings_primary.csv --yes

# 5) 完成后复检
python src/esef_crawler.py verify --index index/gb_filings_primary.csv

# 6) 重建本地 SQLite 索引库（供查询/对接，自带不变量检查）
python src/load_index.py
```

> 多人协作、服务器后台长跑、权限共享 → 看 `docs/RUNBOOK_云服务器下载与协调.md`。

---

## 6. 输出文件说明

### `index/gb_filings_primary.csv`（核心交付物）
去重后的主清单，一行一份权威报告。关键列：

| 列 | 含义 |
|---|---|
| `report_id` | 报告身份证 R000001…（对外引用主键） |
| `lei` / `entity_name` | 公司 LEI / 名称 |
| `period_end` / `fiscal_year` | 财年截止日 / 财年（后者由前者派生） |
| `country` / `report_type` | 国家 / 格式（ESEF） |
| `sha256` | 文件身份/去重键 |
| `package_url` | 报告 ZIP 地址（走文本抽取） |
| `json_url` | xBRL-JSON 地址（**财务直接取数**） |
| `viewer_url` | 在线查看（人工核对） |
| `n_versions` | 收敛了几份原始申报 |

### `download_ledger.csv`（下载台账）
逐包记录，每下完一份立即追加并刷盘。列：`report_id, lei, entity_name, period_end,
fiscal_year, file, size_mb, status(ok/skip/fail), esg_flag, msg`。
**这是全组"下到哪了"的进度真相，建议定期 commit 回 git。**

### `download_failed.csv`
失败清单：`report_id, lei, period_end, reason`。**直接重跑同一条 download 命令即自动补齐。**

### `crawl.log`
结构化运行日志（同时打到屏幕和文件），排错看它。

---

## 7. 常见问题与排错

| 现象 / 报错 | 原因 | 处理 |
|---|---|---|
| `已有下载进程在运行(锁 ...)` | 同目录已有下载进程（一人一机的锁生效） | 正常协作约束。若确认无进程在跑（崩溃残留），`ps aux\|grep esef` 确认后删 `packages/.crawler.lock` 再跑 |
| `磁盘可能不足: 需~X GB` | 可用空间 < 估算需求 | 换更大数据盘、改 `--pkg-dir` 指到大盘、或 `--years` 缩小范围 |
| 下载中途断网/报错 | 网络抖动（国内访问国际线路常见） | **直接重跑同一命令**，自动续传；已下的会 skip，只补没下的 |
| 某些份 `非ZIP(疑似错误页)` | 源站偶发返回错误页而非包 | 已自动删除并记 fail；稍后重跑 download 补 |
| 某些份 `sha256不符` | 下载过程损坏 | 已自动删除；重跑即重下 |
| `verify` 报"损坏 N" | 个别文件 sha/zip 不过 | 删掉 `verify_bad.csv` 里列的文件，重跑 download |
| `index` 某页 HTTP 报错 | 源站临时故障 | 已内置重试；仍失败则重跑，断点在 `index/_index.partial.csv` |
| 国内服务器下载很慢 | 国际带宽限制 | 属正常；重试/续传能扛。条件允许换国际带宽好的节点，或 `--workers` 适度调（别超 8） |
| Windows 上 `fcntl` 报错 | 单实例锁仅类 Unix 支持 | 在 WSL 或 Linux 服务器上跑 |

**黄金法则**：几乎所有下载问题的解法都是同一句——**重跑同一条 `download` 命令**。
它是幂等的：已完成的跳过，失败的重试，不会重复、不会损坏。

---

## 8. 参数速查表

```
index    --country  --out-dir  --delay  --max-pages
plan     --index    --years    --pkg-dir  --limit
download --index    --years    --limit  --pkg-dir  --out-dir  --workers  --delay  --no-esg  --yes
verify   --index    --years    --limit  --pkg-dir  --out-dir
```

常用三连（服务器标准动作）：
```bash
python src/esef_crawler.py plan     --index index/gb_filings_primary.csv          # 看
python src/esef_crawler.py download --index index/gb_filings_primary.csv --yes    # 下
python src/esef_crawler.py verify   --index index/gb_filings_primary.csv          # 检
```
