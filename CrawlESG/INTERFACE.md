# CrawlESG 接口约定（爬 → 提 → 搭）

> 给 ExtractESG / PlatESG 对接用。核心一句话：**`report_id` 是贯穿三个模块的主键骨架，
> 所有人对同一份 `schema.sql` 编程，CrawlESG 只负责把"报告身份 + 文件落盘"交出去。**

---

## 1. 模块边界（谁产出什么）

| 模块 | 负责 | 写入的表 | 交付给下游的东西 |
|---|---|---|---|
| **CrawlESG（爬）** | 采元数据、去重、下载、落盘 | `entity` / `report_raw` / `report` | 索引 + 本地包路径 |
| **ExtractESG（提）** | 读包、锚点/xBRL 抽指标 | `indicator_value` | 指标三元组 |
| **PlatESG（搭）** | 存储、检索、前端、对外 | 库/服务 | 查询接口 |

> 注意边界：`esg_extract_pilot.py`（锚点抽取雏形）**属于 ExtractESG，不在 CrawlESG**。
> CrawlESG 里下载脚本带的 `esg_flag` 只是"含不含 ESRS 声明"的布尔探测，不是抽取本身。

---

## 2. CrawlESG 对外输出（= ExtractESG 的输入契约）

ExtractESG 只需依赖以下两样，**字段名和含义保证稳定**：

### 2.1 索引表 `report`（也提供 `index/gb_filings_primary.csv`）

| 字段 | 含义 | 备注 |
|---|---|---|
| `report_id` | **主键骨架**，对外引用 | R000001…，确定性生成、可复现 |
| `lei` | 公司 LEI | 关联 `entity` |
| `entity_name` | 公司名 | |
| `period_end` | 财年截止日 YYYY-MM-DD | **用它当键**，`report_year` 由其前 4 位派生 |
| `country` / `report_type` | 国家 / 格式(ESEF) | |
| `sha256` | 文件身份/去重键 | 落盘文件名含其前 8 位 |
| `esg_flag` | 是否含 ESRS 声明(布尔) | 锚点探测结果，供提方优先排序 |
| `package_url` | 报告 ZIP 包地址 | 走文本抽取 |
| `json_url` | xBRL-JSON 地址 | **财务直接取结构化数值，不走文本** |
| `viewer_url` | 在线查看 | 人工核对 |
| `kodo_key` | Kodo 桶内对象路径 | 权威存储位置; Web据此签临时链接, 抽取据此拉包 |
| `n_versions` | 收敛了几份原始申报 | 审计用 |

**保证**：已按 `sha256 →(lei,period_end)` 双层去重，一行=一份权威报告；`sha256` 已校验。

### 2.2 本地包

- 路径：`packages/{lei}_{period_end}_{sha8}.zip`（共享服务器目录）。
- `download_ledger.csv` 给出 `report_id ↔ 本地文件 ↔ sha_ok ↔ esg_flag` 的对应，
  ExtractESG 据此知道"哪些 report_id 的文件已在本地、可直接开包"。

> 抽取时按 `report_id` 把结果写回 `indicator_value`，三方自然对齐。

---

## 2.5 存储架构（七牛 Kodo + ECS）

报告本体的权威存储是**七牛 Kodo（私有对象存储）**，ECS 本地盘只作下载/抽取的临时工作区。

**下载链路**（CrawlESG 负责）：
`filings.xbrl.org → ECS 临时盘(下载+sha256/zip校验) → 上传 Kodo → 回填 kodo_key → 删本地临时文件`

**索引新增列 `report.kodo_key`**：对象在桶内的路径，是"文件在哪"的唯一真相。
约定命名（内容寻址、可从索引行确定性推出、重传幂等）：
```
esef/{country}/{fiscal_year}/{lei}_{period_end}_{sha8}.zip
```

**临时下载链接的责任边界**：
- **PlatESG（Web 后端，持 AK/SK）** 负责按需签发短期链接：拿 `report_id` 查 `kodo_key`，
  调七牛 SDK `private_download_url(domain+'/'+kodo_key, expires=N)` 现签一个到期失效的 URL 返给浏览器。
- **CrawlESG 不签、不持密钥**，只保证每份报告有稳定、已记录的 `kodo_key`。
- **ExtractESG** 凭 `kodo_key` 从 Kodo 拉包再抽取（建议 ECS 留本地缓存，避免重抽反复拉全量）。
- AK/SK 放 Web 后端环境变量，**绝不进 git**。

> 待团队定：上传走"服务器过一道"(保校验, 推荐) 还是 Kodo 服务端 fetch(省带宽,无预校验)；
> 桶私有(用临时链接即私有)；抽取是否每次从 Kodo 拉或用本地缓存。

---

## 3. ExtractESG → PlatESG（下游契约，提方填，列在此对齐）

`indicator_value` 每行一个指标值：

`report_id`(FK) · `esrs_code`(FK→`esrs_datapoint`) · `value_num` · `unit` · `scope` ·
`fiscal_year` · `source`(xbrl-json/text-anchor) · `confidence`(high/medium) · `page_or_anchor`

指标必须用 **ESRS 受控词表编码**（E1-6 等），不用自由中文名；词表见 `schema.sql` 种子数据。

---

## 4. 共享契约 = 一份 `schema.sql`

三个模块对同一份 `schema/schema.sql` 编程：Crawl 写前三张表、Extract 追加 `indicator_value`、Plat 读全部并服务。`esef_index.db` 是 rebuildable 产物（`python src/load_index.py` 重建），**不进 git**，避免二进制冲突。

---

## 5. 协作约定

- 大文件不进 git：`packages/`、`*.zip`、`*.db`、`*.log` 一律 gitignore；包只在共享服务器。
- 台账回流 git：`download_ledger.csv` / `download_failed.csv` 定期提交，作为全组进度真相。
- 各自在自己文件夹内开发；跨模块改 `schema.sql` 先在 issue/PR 里同步，避免接口漂移。
