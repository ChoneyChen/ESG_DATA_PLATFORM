# RUNBOOK：在云服务器上下载 GB ESEF 报告并与同组协调

> 目标：把 GB 报告包（约 2,796 份 / ~42GB）下载到一台共享云服务器，
> 全组可随时查看进度、安全续传，且对 filings.xbrl.org 免费服务保持礼貌。
>
> 三条铁律：**① 只一台机器、一个人起下载；② 断线必须能续跑；③ 包落共享目录、台账是唯一真相。**

---

## 0. 前置条件

- 一台 Linux 云服务器（阿里云 ECS / 腾讯云 CVM / 实验室服务器均可），能 SSH。
- Python 3.8+。
- **数据盘 ≥ 100GB**：GB 全量约 42GB；给后续欧盟扩展、xBRL-JSON、抽取产出预留余量（与 PPT 的 100–150GB 一致）。
- 两个文件：`esef_download_gb.py`、`gb_filings_primary.csv`。
- ⚠️ 国内云服务器访问 filings.xbrl.org 走国际线路，可能慢/偶尔抖动。脚本有重试+续传可兜底，但请预期整体耗时更长；条件允许优先选国际带宽好的节点。

---

## 1. 用 git 同步代码与索引（协调的基础）

让全组用同一份代码和同一份索引，避免"各自一份对不上"。

```bash
# 本地：把这两个文件加入仓库并推送
git add esef_download_gb.py gb_filings_primary.csv
git commit -m "add GB downloader + frozen index"
git push

# 服务器：
git clone <你们的仓库地址> /data/esef
cd /data/esef
```

> 若暂时没有仓库，可用 scp 临时替代：
> `scp esef_download_gb.py gb_filings_primary.csv user@server:/data/esef/`

---

## 2. 装环境

```bash
cd /data/esef
python3 -m venv .venv
source .venv/bin/activate
pip install requests pandas
```

---

## 3. 共享目录与权限（让同学都能读）

```bash
sudo groupadd esef 2>/dev/null
sudo usermod -aG esef <同学A账号>
sudo usermod -aG esef <同学B账号>
sudo chgrp -R esef /data/esef
sudo chmod -R 2775 /data/esef     # 开头的 2 是 setgid：新下载的文件自动继承 esef 组
```

被加组的同学需要重新登录一次才生效。

---

## 4. 先小跑试通（务必先做）

正式全量前，先下 5 个确认网络/权限/脚本都正常：

```bash
python src/esef_crawler.py download --index index/gb_filings_primary.csv --years 2024 --limit 5 --yes
```

成功标志：`packages/` 出现 5 个 zip，`download_ledger.csv` 有 5 行且 `sha_ok=True`。

---

## 5. 全量下载（断线续跑 + 写日志）

在 tmux 里跑，输出同时写进日志文件，方便全组围观：

```bash
tmux new -s esef
source .venv/bin/activate
python src/esef_crawler.py download --index index/gb_filings_primary.csv --yes 2>&1 | tee -a download.log
```

离开但保持运行：按 `Ctrl-b`，松开，再按 `d`。
重新回到会话：`tmux attach -t esef`。

> 脚本特性：已存在且 sha256 校验通过的包会自动跳过 → **任何时候中断，重跑同一条命令即自动续传**。

---

## 6. 全组查看进度（只读，不另起进程）

任何同学 SSH 上来都可以：

```bash
tail -f /data/esef/download.log          # 实时滚动日志
ls /data/esef/packages | wc -l           # 已下载包数 / 目标 2796
du -sh /data/esef/packages               # 已占磁盘
column -s, -t /data/esef/download_ledger.csv | less   # 看台账(含 esg_flag)
```

---

## 7. 协调分工（避免重复 & 避免一起砸 API）

- **默认方案（推荐）**：指定 1 人在这台服务器跑全量，其余人只 `tail` 看。脚本默认 4 并发，对免费服务已足够礼貌。
- **切勿**多人各自 `python esef_download_gb.py ...` —— 5 人 × 4 并发 = 20 并发，既不礼貌也会让台账/文件写入打架。
- **若确需分工**：按财年切片，分人分时段，互不重叠：
  ```bash
  # 同学A
  python esef_download_gb.py --index gb_filings_primary.csv --years 2024 2025 --yes
  # 同学B（另一时段）
  python esef_download_gb.py --index gb_filings_primary.csv --years 2022 2023 --yes
  ```
  因为文件名带 sha8 且有 sha 校验，切片之间不会互相覆盖，合并后台账各自独立。

---

## 8. 收尾校验

```bash
# 失败清单：有内容就原样重跑下载命令，自动补齐
cat /data/esef/download_failed.csv

# 抽查 ESG 命中分布（含 ESRS 声明的包数）
python - <<'PY'
import pandas as pd
ld = pd.read_csv('/data/esef/download_ledger.csv')
print('成功包数:', len(ld))
if 'esg_flag' in ld or 'esg_hit' in ld:
    col = 'esg_hit' if 'esg_hit' in ld else 'esg_flag'
    print('ESG锚点命中:', int(ld[col].sum()), '/', len(ld))
PY
```

完成标准：`packages/` 数量 ≈ 索引主报告数，`download_failed.csv` 为空，台账齐全。

---

## 9. （可选）开机自启 / 长跑守护

若希望服务器重启后自动续传，可用 systemd（一次性下载通常不必，tmux 足够）：

```ini
# /etc/systemd/system/esef-download.service
[Unit]
Description=ESEF GB downloader
After=network-online.target
[Service]
WorkingDirectory=/data/esef
ExecStart=/data/esef/.venv/bin/python src/esef_crawler.py download --index index/gb_filings_primary.csv --yes
Restart=on-failure
User=<运行账号>
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now esef-download
journalctl -u esef-download -f      # 看日志
```
