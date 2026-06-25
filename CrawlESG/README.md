# CrawlESG（爬）— ESEF/ESG 报告采集与索引

ESG_DATA_PLATFORM 的"爬"模块：从 filings.xbrl.org 采集 ESEF 报告元数据、双层去重、
下载报告包，产出供 ExtractESG/PlatESG 对接的索引。核心工具 `src/esef_crawler.py`
为**云服务器长跑**设计：可随时 Ctrl-C / 断网 / 重启，重跑同一命令自动续传。

## 目录
```
CrawlESG/
├── INTERFACE.md       # ★ 对接 提/搭 的接口契约, 先看这个
├── src/
│   ├── esef_crawler.py        # ★ 生产级采集器: index/plan/download/verify 四子命令
│   ├── load_index.py          # 用 schema+CSV 重建 SQLite 索引库(含不变量检查)
│   └── esef_one_click.py      # 通用元数据脚本(全欧/其他国家), 参考
├── index/
│   ├── gb_filings_primary.csv # ★ 去重后主索引(交付物), 2796份/798家, report_id内建
│   ├── gb_filings_raw.csv     # 去重前审计底稿
│   └── gb_coverage_by_year_type.csv
├── schema/
│   ├── schema.sql             # ★ 三模块共享的库结构(接口契约)
│   └── DESIGN_v1.md          # 索引层总体设计(11节, 含8条不变量)
└── docs/
    ├── MANUAL.docx          # ★ 完整操作手册(Word版, 给队友/导师看)
    ├── MANUAL.md            #   同上 Markdown 版(便于 git 改动追踪)
    └── RUNBOOK.md  # 服务器+多人协作
```

## 四个子命令
```bash
pip install -r requirements.txt

# index : 采元数据 -> 双层去重 -> 索引CSV+覆盖(重试/断点/可复现report_id)
python src/esef_crawler.py index --country GB --out-dir .

# plan  : 干跑, 给出待下载数/估算体积/磁盘检查, 不下载
python src/esef_crawler.py plan --index index/gb_filings_primary.csv --years 2024

# download : 断点续传下载(流式/原子落盘/sha+zip双校验/限速/单实例锁/优雅退出)
python src/esef_crawler.py download --index index/gb_filings_primary.csv --years 2024 --yes

# verify : 对已下文件重做 sha256+zip 完整性校验
python src/esef_crawler.py verify --index index/gb_filings_primary.csv

# 重建本地索引库(秒级, 自带不变量检查)
python src/load_index.py
```

## 万无一失的保证(均已小规模实测)
1. **原子落盘**: 先写 `*.part`, 通过 sha256+zip 校验后才 `os.replace` 正式名 -> 正式路径上的文件必然完整且校验过。
2. **内容校验**: 校验 sha256 + `zipfile.is_zipfile`, 挡掉"200 错误页冒充包"。
3. **单实例锁**: fcntl 文件锁, 同一 packages 目录只允许一个下载进程(技术落实"一人一机")。
4. **优雅退出**: 收 SIGINT/SIGTERM 后完成在途、清 `*.part`、刷台账再退, 可续传。
5. **限速+重试**: 全局最小请求间隔 + 指数退避(自动扛 429/5xx/断网)。
6. **磁盘预检**: 下载前比对可用空间, 不够直接拒绝。
7. **durable 台账**: 每完成一份立即写 `download_ledger.csv` 并刷盘, 崩溃不丢进度。

## 现状(GB, 国家先行)
- 在库 2,838 条原始申报 → 去重 2,796 份主报告 / 798 家公司; 财年 2020–2026; 全部 ESEF/GB。
- 实测: UK 的 ESEF 包含 ESRS 不稳定, 详见 schema/DESIGN_v1.md 第 10 节。
