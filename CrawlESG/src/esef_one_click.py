# -*- coding: utf-8 -*-
"""
================================================================================
 ESEF 一键采集脚本 (filings.xbrl.org)
================================================================================
数据源 : https://filings.xbrl.org  (XBRL International 官方 ESEF/UKSEF 文件库)
API    : https://filings.xbrl.org/api/filings  (公开, 免费, JSON-API 标准)
内容   : 欧洲受监管市场上市公司的年度财务报告 (iXBRL 结构化格式),
         2024 财年起第一批 CSRD 公司的可持续发展声明(ESG)嵌在同一份报告中。

【环境】 Python 3.8+;  pip install requests pandas
【用法】
  第 1 步 (默认, 安全): 只拉元数据 + 生成覆盖统计, 不下载任何报告
      python esef_one_click.py
  第 2 步: 确认范围后下载报告 ZIP 包 (财务报告 + ESG 章节在同一个包里)
      python esef_one_click.py --download --country GB --years 2023 2024
  其他示例:
      python esef_one_click.py --country FI                # 只看芬兰的元数据
      python esef_one_click.py --download --years 2024     # 下载全欧 2024 财年
      python esef_one_click.py --download --yes            # 全量下载, 跳过确认
                                                            # (注意: 全量约 2.5 万份,
                                                            #  需数百 GB 磁盘, 慎用!)
【产出】 (均在 esef_output/ 目录下)
  esef_filings_index.csv   全部文件元数据清单 (公司名/LEI/国家/财年/下载链接/校验值)
  coverage_pivot.csv       国家 x 财年 覆盖数量透视表
  packages/                下载的报告 ZIP 包 (LEI_财年截止日.zip)
  download_failed.csv      下载失败清单 (可重跑脚本自动续传补齐)
  esef_run.log             运行日志
================================================================================
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

try:
    import requests
    import pandas as pd
except ImportError:
    sys.exit("缺少依赖, 请先执行:  pip install requests pandas")

BASE = "https://filings.xbrl.org"
API = BASE + "/api/filings"
OUT_DIR = "esef_output"
PKG_DIR = os.path.join(OUT_DIR, "packages")
PAGE_SIZE = 200          # 官方文档支持 page[size]
POLITE_DELAY = 0.4       # 翻页间隔(秒), 对免费公共服务保持礼貌
MAX_RETRIES = 5
TIMEOUT = 90

os.makedirs(OUT_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(OUT_DIR, "esef_run.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("esef")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "esef-research-pilot/1.0 (academic ESG project)"})


# ---------------------------------------------------------------- 网络层 ----
def get_with_retry(url, params=None, stream=False):
    """带指数退避重试的 GET; 429/5xx/网络错误自动重试。"""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = SESSION.get(url, params=params, timeout=TIMEOUT, stream=stream)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                wait = min(2 ** attempt, 60)
                log.warning("HTTP %s -> %s 秒后重试 (%s/%s)  %s",
                            r.status_code, wait, attempt, MAX_RETRIES, url)
                time.sleep(wait)
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            last_err = e
            wait = min(2 ** attempt, 60)
            log.warning("请求异常 %s -> %s 秒后重试 (%s/%s)", e, wait, attempt, MAX_RETRIES)
            time.sleep(wait)
    raise RuntimeError(f"重试 {MAX_RETRIES} 次后仍失败: {url}  ({last_err})")


# ------------------------------------------------------------ 元数据采集 ----
def absolutize(v):
    """把相对 URL 补全为绝对 URL。"""
    if isinstance(v, str) and v.startswith("/"):
        return urljoin(BASE, v)
    return v


def derive_year(attrs):
    """优先从 period_end 取财年; 取不到则从 package_url 文件名里找 20XX。"""
    pe = attrs.get("period_end") or attrs.get("last_end_date") or ""
    m = re.match(r"^(20\d{2})", str(pe))
    if m:
        return m.group(1)
    m = re.search(r"(20\d{2})", str(attrs.get("package_url") or ""))
    return m.group(1) if m else None


def fetch_all_metadata(country=None):
    """分页拉取全部 filing 元数据 (含 entity), 容错式: 所有属性字段动态保留。"""
    rows = []
    seen_urls = set()
    params = {"page[size]": PAGE_SIZE, "include": "entity", "sort": "-processed"}
    if country:
        params["filter[country]"] = country.upper()

    url, page = API, 0
    while url:
        if url in seen_urls:               # 防御: 避免 links.next 异常导致死循环
            log.warning("检测到重复分页链接, 提前结束: %s", url)
            break
        seen_urls.add(url)
        page += 1

        r = get_with_retry(url, params=params if url == API else None)
        try:
            data = r.json()
        except json.JSONDecodeError:
            raise RuntimeError(f"第 {page} 页返回的不是合法 JSON: {url}")

        # entity 映射: id -> {name, identifier(LEI)}
        ent_map = {}
        for inc in data.get("included") or []:
            if inc.get("type") == "entity":
                ia = inc.get("attributes") or {}
                ent_map[str(inc.get("id"))] = {
                    "entity_name": ia.get("name"),
                    "entity_identifier": ia.get("identifier"),
                }

        for f in data.get("data") or []:
            attrs = dict(f.get("attributes") or {})
            row = {"api_id": f.get("id")}
            # 动态保留全部属性; URL 字段自动补全为绝对地址
            for k, v in attrs.items():
                row[k] = absolutize(v) if "url" in k.lower() else v
            # 关联公司信息
            ent_id = str((((f.get("relationships") or {}).get("entity") or {})
                          .get("data") or {}).get("id"))
            row.update(ent_map.get(ent_id, {}))
            # 派生字段
            row["fiscal_year"] = derive_year(attrs)
            fxo = str(attrs.get("fxo_id") or "")
            row["lei"] = fxo.split("-")[0] if fxo else (row.get("entity_identifier") or "")
            rows.append(row)

        nxt = (data.get("links") or {}).get("next")
        url = absolutize(nxt) if nxt else None
        log.info("第 %s 页完成, 累计 %s 条", page, len(rows))

        # 每 10 页落盘一次断点, 中途失败不丢进度
        if page % 10 == 0:
            pd.DataFrame(rows).to_csv(
                os.path.join(OUT_DIR, "esef_filings_index.partial.csv"),
                index=False, encoding="utf-8-sig")
        time.sleep(POLITE_DELAY)

    df = pd.DataFrame(rows)
    return df


def save_index_and_stats(df):
    idx_path = os.path.join(OUT_DIR, "esef_filings_index.csv")
    df.to_csv(idx_path, index=False, encoding="utf-8-sig")
    log.info("元数据清单已保存: %s (共 %s 条)", idx_path, len(df))

    if df.empty:
        log.warning("没有取到任何数据, 请检查网络或过滤条件。")
        return

    # 国家 x 财年 覆盖透视表
    pv = (df.assign(fiscal_year=df["fiscal_year"].fillna("未知"))
            .pivot_table(index="country", columns="fiscal_year",
                         values="api_id", aggfunc="count", fill_value=0))
    pv["合计"] = pv.sum(axis=1)
    pv = pv.sort_values("合计", ascending=False)
    pv_path = os.path.join(OUT_DIR, "coverage_pivot.csv")
    pv.to_csv(pv_path, encoding="utf-8-sig")

    print("\n" + "=" * 60)
    print("覆盖统计 (国家 x 财年, 文件数):")
    print(pv.to_string())
    print(f"\n独立公司数(按LEI): {df['lei'].replace('', None).nunique()}")
    print(f"透视表已保存: {pv_path}")
    print("=" * 60 + "\n")


# -------------------------------------------------------------- 下载层 ----
def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))[:120]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_one(row):
    """下载单个报告 ZIP; 已存在且校验通过则跳过; 返回 (api_id, ok, msg)。"""
    url = row.get("package_url")
    if not url or not isinstance(url, str):
        return row.get("api_id"), False, "无 package_url"
    fname = f"{safe_name(row.get('lei') or row.get('api_id'))}_" \
            f"{safe_name(row.get('period_end') or row.get('fiscal_year') or 'NA')}.zip"
    path = os.path.join(PKG_DIR, fname)
    expected = str(row.get("sha256") or row.get("package_sha256") or "").lower()

    if os.path.exists(path) and os.path.getsize(path) > 0:
        if not expected or sha256_of(path) == expected:
            return row.get("api_id"), True, "已存在, 跳过"
        os.remove(path)  # 校验不符, 重新下载

    try:
        r = get_with_retry(url, stream=True)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 18):
                fh.write(chunk)
        if expected and sha256_of(tmp) != expected:
            os.remove(tmp)
            return row.get("api_id"), False, "sha256 校验失败"
        os.replace(tmp, path)
        return row.get("api_id"), True, fname
    except Exception as e:
        return row.get("api_id"), False, str(e)


def download_packages(df, workers, assume_yes):
    os.makedirs(PKG_DIR, exist_ok=True)
    todo = df[df.get("package_url").notna()] if "package_url" in df.columns else df.iloc[0:0]
    n = len(todo)
    if n == 0:
        log.warning("清单中没有可下载的 package_url。")
        return
    est_gb = n * 15 / 1024  # 按平均 ~15MB/份 估算
    print(f"\n即将下载 {n} 份报告 ZIP (粗略估计约 {est_gb:.0f} GB 磁盘空间)。")
    if not assume_yes:
        if input("确认开始下载? 输入 y 继续: ").strip().lower() != "y":
            print("已取消下载。可加 --country / --years 缩小范围后再试。")
            return

    ok_n, fails = 0, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(download_one, row): row
                   for _, row in todo.iterrows()}
        for i, fut in enumerate(as_completed(futures), 1):
            api_id, ok, msg = fut.result()
            if ok:
                ok_n += 1
            else:
                fails.append({"api_id": api_id, "reason": msg})
            if i % 25 == 0 or i == n:
                log.info("下载进度 %s/%s (成功 %s, 失败 %s)", i, n, ok_n, len(fails))

    if fails:
        fp = os.path.join(OUT_DIR, "download_failed.csv")
        pd.DataFrame(fails).to_csv(fp, index=False, encoding="utf-8-sig")
        log.warning("有 %s 份下载失败, 清单见 %s ; 直接重跑本命令即可自动续传补齐。",
                    len(fails), fp)
    log.info("下载完成: 成功 %s / 共 %s, 文件在 %s", ok_n, n, PKG_DIR)


# ----------------------------------------------------------------- 主流程 --
def main():
    ap = argparse.ArgumentParser(description="ESEF 一键采集 (filings.xbrl.org)")
    ap.add_argument("--country", help="国家代码过滤, 如 GB / FI / FR / NL")
    ap.add_argument("--years", nargs="*", help="财年过滤, 如 2023 2024")
    ap.add_argument("--download", action="store_true",
                    help="下载报告 ZIP 包 (默认只拉元数据和统计)")
    ap.add_argument("--workers", type=int, default=4, help="并行下载数 (默认 4)")
    ap.add_argument("--yes", action="store_true", help="跳过下载前的确认提示")
    args = ap.parse_args()

    log.info("开始拉取元数据 ... (国家过滤: %s)", args.country or "无, 全量")
    df = fetch_all_metadata(country=args.country)

    if args.years and "fiscal_year" in df.columns:
        df = df[df["fiscal_year"].isin([str(y) for y in args.years])]
        log.info("按财年 %s 过滤后剩 %s 条", args.years, len(df))

    save_index_and_stats(df)

    if args.download:
        download_packages(df, workers=args.workers, assume_yes=args.yes)
    else:
        print("提示: 本次未下载报告。确认覆盖范围后, 加 --download 参数再跑一次即可,")
        print("      例如:  python esef_one_click.py --download --country GB --years 2024\n")


if __name__ == "__main__":
    main()
