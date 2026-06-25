#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 esef_crawler.py — 生产级 ESEF 采集器 (filings.xbrl.org)
================================================================================
为"云服务器长跑"设计：可随时 Ctrl-C / 断网 / 重启，重跑同一命令自动续传，
不重复下载、不留半文件、不污染台账。

子命令:
  index     采元数据 -> 双层去重 -> 索引CSV + 覆盖统计   (重试/断点/可复现report_id)
  plan      干跑：将下载多少、估算体积、磁盘是否够      (不下载, 决策用)
  download  断点续传下载报告包                          (流式/原子落盘/sha+zip双校验/
                                                         限速/单实例锁/优雅退出/durable台账)
  verify    对已下载文件重新做 sha256 + zip 完整性校验

万无一失的几条保证:
  1. 原子落盘: 先写 *.part, 通过 sha256+zip 校验后才 os.replace 到正式名;
     => 正式路径上出现的文件必然是完整且校验过的, 续传只需判断"正式文件是否存在"。
  2. 内容校验: 校验 sha256(若索引有) + zipfile.is_zipfile, 挡掉"200 错误页冒充包"。
  3. 单实例锁: fcntl 文件锁, 同一 packages 目录只允许一个下载进程(技术上落实"一人一机")。
  4. 优雅退出: 收到 SIGINT/SIGTERM, 完成在途、清掉 *.part、刷新台账后干净退出。
  5. 限速+重试: 全局最小请求间隔(对免费服务礼貌) + 指数退避(自动扛 429/5xx/断网)。
  6. 磁盘预检: 下载前比对可用空间, 不够直接拒绝, 避免写满。

用法:
  python esef_crawler.py index   --country GB --out-dir .
  python esef_crawler.py plan     --index index/gb_filings_primary.csv --years 2024
  python esef_crawler.py download --index index/gb_filings_primary.csv --years 2024 --limit 5
  python esef_crawler.py verify   --index index/gb_filings_primary.csv
================================================================================
"""
import argparse, csv, fcntl, hashlib, logging, os, re, shutil, signal, sys, threading, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

try:
    import requests
    import pandas as pd
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    sys.exit("缺少依赖, 请先: pip install requests pandas")

BASE = "https://filings.xbrl.org"
API = BASE + "/api/filings"
UA = "esef-research-pilot/1.0 (academic ESG project; contact: your-email)"
TIMEOUT = 90
ESG_ANCHORS = re.compile("|".join([
    r"Gross Scope 1 GHG emissions", r"Total GHG emissions", r"Scope 1 .{0,3} 3",
    r"Energy consumption and mix", r"sustainability statement", r"ESRS",
    r"Water consumption", r"[Cc]onfirmed incidents of corruption",
]))

log = logging.getLogger("esef")
STOP = threading.Event()


# ------------------------------------------------------------------ 基础设施 --
def setup_logging(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(out_dir, "crawl.log"), encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])


def install_signal_handlers():
    def _h(signum, frame):
        if not STOP.is_set():
            log.warning("收到信号 %s, 完成在途任务后安全退出 ...", signum)
        STOP.set()
    signal.signal(signal.SIGINT, _h)
    signal.signal(signal.SIGTERM, _h)


def make_session(workers):
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    kw = dict(total=5, connect=5, read=5, status=5, backoff_factor=1.5,
              status_forcelist=(429, 500, 502, 503, 504),
              respect_retry_after_header=True, raise_on_status=False)
    try:
        retry = Retry(allowed_methods=frozenset(["GET"]), **kw)   # urllib3>=1.26
    except TypeError:
        retry = Retry(method_whitelist=frozenset(["GET"]), **kw)  # 老版本
    ad = HTTPAdapter(max_retries=retry, pool_connections=max(4, workers * 2),
                     pool_maxsize=max(4, workers * 2))
    s.mount("https://", ad)
    s.mount("http://", ad)
    return s


class RateLimiter:
    """全局最小请求间隔, 线程安全; 对免费公共服务保持礼貌。"""
    def __init__(self, min_interval):
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.next_t = 0.0
    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_t:
                time.sleep(self.next_t - now)
                now = time.monotonic()
            self.next_t = now + self.min_interval


@contextmanager
def single_instance_lock(lock_path):
    """fcntl 文件锁: 同一目录只允许一个下载进程。进程退出自动释放。"""
    f = open(lock_path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"已有下载进程在运行(锁 {lock_path})。一人一机跑即可, 其余人只看日志。")
    f.write(str(os.getpid())); f.flush()
    try:
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN); f.close(); os.remove(lock_path)
        except OSError:
            pass


def absolutize(v):
    return BASE + v if isinstance(v, str) and v.startswith("/") else v


def safe(s):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(s))[:120]


# --------------------------------------------------------------------- index --
def parse_fxo(fxo):
    m = re.match(r"^(.+?)-(\d{4}-\d{2}-\d{2})-([A-Za-z]+)-([A-Z]{2})-(\d+)$", str(fxo or ""))
    return (m.group(1), m.group(3), m.group(4), int(m.group(5))) if m else (None, None, None, None)


def cmd_index(args):
    s = make_session(1)
    rows, seen, page = [], set(), 0
    params = {"page[size]": 200, "include": "entity", "sort": "-processed"}
    if args.country:
        params["filter[country]"] = args.country.upper()
    url = API
    ckpt = os.path.join(args.out_dir, "index", "_index.partial.csv")
    os.makedirs(os.path.dirname(ckpt), exist_ok=True)
    while url and not STOP.is_set():
        if url in seen:
            log.warning("分页链接重复, 提前结束"); break
        seen.add(url); page += 1
        r = s.get(url, params=params if url == API else None, timeout=TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"第{page}页 HTTP {r.status_code}")
        d = r.json()
        ent = {str(i["id"]): {"entity_name": (i.get("attributes") or {}).get("name"),
                              "lei": (i.get("attributes") or {}).get("identifier")}
               for i in (d.get("included") or []) if i.get("type") == "entity"}
        for f in d.get("data") or []:
            a = dict(f.get("attributes") or {})
            lei_fx, rtype, _, seq = parse_fxo(a.get("fxo_id"))
            eid = str((((f.get("relationships") or {}).get("entity") or {}).get("data") or {}).get("id"))
            e = ent.get(eid, {})
            pe = str(a.get("period_end") or "")
            rows.append({"api_id": f.get("id"), "lei": e.get("lei") or lei_fx,
                         "entity_name": e.get("entity_name"), "period_end": pe,
                         "fiscal_year": pe[:4] if re.match(r"^\d{4}", pe) else None,
                         "country": a.get("country"), "report_type": rtype, "seq": seq,
                         "sha256": (a.get("sha256") or "").lower(),
                         "error_count": a.get("error_count"), "warning_count": a.get("warning_count"),
                         "inconsistency_count": a.get("inconsistency_count"),
                         "processed": a.get("processed"), "date_added": a.get("date_added"),
                         "package_url": absolutize(a.get("package_url")),
                         "json_url": absolutize(a.get("json_url")),
                         "viewer_url": absolutize(a.get("viewer_url")), "fxo_id": a.get("fxo_id")})
        nxt = (d.get("links") or {}).get("next")
        url = absolutize(nxt) if nxt else None
        log.info("index 第%s页, 累计 %s", page, len(rows))
        if page % 5 == 0:
            pd.DataFrame(rows).to_csv(ckpt, index=False, encoding="utf-8-sig")  # 断点
        if args.max_pages and page >= args.max_pages:
            break
        time.sleep(args.delay)

    raw = pd.DataFrame(rows)
    # 双层去重: L1 sha256(字节相同) ; L2 (lei,period_end) 取 error少→processed新
    raw = raw.sort_values("processed", ascending=False)
    l1 = raw.drop_duplicates(subset=["sha256"], keep="first").copy()
    l1["_err"] = pd.to_numeric(l1["error_count"], errors="coerce").fillna(9999)
    l1 = l1.sort_values(["_err", "processed"], ascending=[True, False])
    l1["n_versions"] = l1.groupby(["lei", "period_end"], dropna=False)["api_id"].transform("count")
    prim = l1.drop_duplicates(subset=["lei", "period_end"], keep="first").drop(columns=["_err"])
    # 可复现 report_id: 按(lei,period_end)确定性排序
    prim = prim.sort_values(["lei", "period_end"]).reset_index(drop=True)
    prim.insert(0, "report_id", ["R%06d" % (i + 1) for i in range(len(prim))])

    idx_dir = os.path.join(args.out_dir, "index"); os.makedirs(idx_dir, exist_ok=True)
    cc = (args.country or "all").lower()
    raw.to_csv(os.path.join(idx_dir, f"{cc}_filings_raw.csv"), index=False, encoding="utf-8-sig")
    prim.to_csv(os.path.join(idx_dir, f"{cc}_filings_primary.csv"), index=False, encoding="utf-8-sig")
    pv = prim.pivot_table(index="fiscal_year", columns="report_type",
                          values="report_id", aggfunc="count", fill_value=0)
    pv["合计"] = pv.sum(axis=1)
    pv.to_csv(os.path.join(idx_dir, f"{cc}_coverage_by_year_type.csv"), encoding="utf-8-sig")
    if os.path.exists(ckpt):
        os.remove(ckpt)
    log.info("index 完成: 原始 %s -> 主报告 %s / 公司 %s", len(raw), len(prim), prim["lei"].nunique())
    print(pv.to_string())


# -------------------------------------------------------- 下载 / plan / verify --
def load_index(args):
    df = pd.read_csv(args.index)
    if getattr(args, "years", None):
        df = df[df["fiscal_year"].astype(str).isin([str(y) for y in args.years])]
    df = df[df["package_url"].notna()].copy()
    if getattr(args, "limit", None):
        df = df.head(args.limit)
    return df


def cmd_plan(args):
    df = load_index(args)
    n = len(df)
    pkg_dir = args.pkg_dir
    have = sum(1 for _, r in df.iterrows()
               if os.path.exists(os.path.join(pkg_dir, _fname(r)))) if os.path.isdir(pkg_dir) else 0
    todo = n - have
    est_gb = todo * 15 / 1024
    free_gb = shutil.disk_usage(os.path.dirname(os.path.abspath(pkg_dir)) or ".").free / 1e9
    print(f"索引匹配 : {n}")
    print(f"已在本地 : {have}")
    print(f"待下载   : {todo}  (估算约 {est_gb:.1f} GB, 按 ~15MB/份)")
    print(f"磁盘可用 : {free_gb:.1f} GB  -> {'充足' if free_gb > est_gb * 1.2 else '⚠ 可能不足'}")


def _fname(row):
    sha8 = str(row.get("sha256") or "")[:8] or "nohash"
    return f"{safe(row.get('lei'))}_{safe(row.get('period_end'))}_{sha8}.zip"


def detect_esg(path):
    try:
        zf = zipfile.ZipFile(path)
        x = next((n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html"))), None)
        if not x:
            return 0
        return 1 if ESG_ANCHORS.search(zf.read(x).decode("utf-8", "ignore")) else 0
    except Exception:
        return 0


def download_one(row, session, limiter, pkg_dir, do_esg):
    if STOP.is_set():
        return ("skip", row, "stopped", None)
    url = row.get("package_url")
    path = os.path.join(pkg_dir, _fname(row))
    expected = str(row.get("sha256") or "").lower()
    # 续传: 正式路径存在即视为已完成(原子落盘保证它一定是校验过的完整文件)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return ("skip", row, path, detect_esg(path) if do_esg else None)
    limiter.wait()
    tmp = path + ".part"
    try:
        r = session.get(url, stream=True, timeout=TIMEOUT)
        if r.status_code != 200:
            return ("fail", row, f"HTTP {r.status_code}", None)
        h = hashlib.sha256()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 18):
                if STOP.is_set():
                    fh.close(); os.remove(tmp); return ("skip", row, "stopped", None)
                fh.write(chunk); h.update(chunk)
        if expected and h.hexdigest() != expected:
            os.remove(tmp); return ("fail", row, "sha256不符", None)
        if not zipfile.is_zipfile(tmp):                 # 挡掉"200错误页冒充zip"
            os.remove(tmp); return ("fail", row, "非ZIP(疑似错误页)", None)
        os.replace(tmp, path)                            # 原子落盘
        return ("ok", row, path, detect_esg(path) if do_esg else None)
    except Exception as e:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
        return ("fail", row, str(e), None)


def cmd_download(args):
    df = load_index(args)
    n = len(df)
    if n == 0:
        sys.exit("索引过滤后无可下载项。")
    pkg_dir = args.pkg_dir
    os.makedirs(pkg_dir, exist_ok=True)

    # 磁盘预检
    todo_est = n * 15 / 1024
    free_gb = shutil.disk_usage(pkg_dir).free / 1e9
    if free_gb < todo_est * 1.1:
        sys.exit(f"磁盘可能不足: 需~{todo_est:.1f}GB, 仅 {free_gb:.1f}GB 可用。换盘或缩小范围。")
    if not args.yes:
        if input(f"将处理 {n} 份(约 {todo_est:.1f}GB)。y 继续: ").strip().lower() != "y":
            sys.exit("已取消。")

    do_esg = not args.no_esg
    session = make_session(args.workers)
    limiter = RateLimiter(args.delay)
    ledger_path = os.path.join(args.out_dir, "download_ledger.csv")
    failed_path = os.path.join(args.out_dir, "download_failed.csv")
    os.makedirs(args.out_dir, exist_ok=True)

    fields = ["report_id", "lei", "entity_name", "period_end", "fiscal_year",
              "file", "size_mb", "status", "esg_flag", "msg"]
    new_ledger = not os.path.exists(ledger_path)
    lf = open(ledger_path, "a", newline="", encoding="utf-8-sig")
    lw = csv.DictWriter(lf, fieldnames=fields)
    if new_ledger:
        lw.writeheader(); lf.flush()

    ok = skip = 0
    fails = []
    with single_instance_lock(os.path.join(pkg_dir, ".crawler.lock")):
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(download_one, r, session, limiter, pkg_dir, do_esg): r
                    for _, r in df.iterrows()}
            done = 0
            for fut in as_completed(futs):
                status, row, info, esg = fut.result()
                done += 1
                rec = {"report_id": row.get("report_id"), "lei": row.get("lei"),
                       "entity_name": row.get("entity_name"), "period_end": row.get("period_end"),
                       "fiscal_year": row.get("fiscal_year"), "status": status, "esg_flag": esg,
                       "msg": "" if status != "fail" else info}
                if status in ("ok", "skip") and os.path.exists(str(info)):
                    rec["file"] = os.path.basename(info)
                    rec["size_mb"] = round(os.path.getsize(info) / 1e6, 2)
                if status == "ok":
                    ok += 1
                elif status == "skip":
                    skip += 1
                else:
                    fails.append({"report_id": row.get("report_id"), "lei": row.get("lei"),
                                  "period_end": row.get("period_end"), "reason": info})
                lw.writerow(rec); lf.flush()              # durable: 每行即刷盘
                if done % 25 == 0 or done == n:
                    log.info("进度 %s/%s (新下%s 跳过%s 失败%s)", done, n, ok, skip, len(fails))
                if STOP.is_set():
                    log.warning("已请求停止, 不再派发新任务, 收尾中 ...")
    lf.close()
    if fails:
        pd.DataFrame(fails).to_csv(failed_path, index=False, encoding="utf-8-sig")
        log.warning("失败 %s 份, 见 %s ; 直接重跑同一命令自动续传。", len(fails), failed_path)
    log.info("下载结束: 新下%s 跳过%s 失败%s / 共%s", ok, skip, len(fails), n)
    if do_esg:
        led = pd.read_csv(ledger_path)
        led = led[led["status"].isin(["ok", "skip"])]
        if "esg_flag" in led and len(led):
            log.info("ESG锚点命中: %s/%s", int(led["esg_flag"].fillna(0).sum()), len(led))


def cmd_verify(args):
    df = load_index(args)
    pkg_dir = args.pkg_dir
    checked = bad = miss = 0
    bad_rows = []
    for _, r in df.iterrows():
        path = os.path.join(pkg_dir, _fname(r))
        if not os.path.exists(path):
            miss += 1; continue
        checked += 1
        expected = str(r.get("sha256") or "").lower()
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for c in iter(lambda: fh.read(1 << 20), b""):
                h.update(c)
        sha_ok = (not expected) or (h.hexdigest() == expected)
        zip_ok = zipfile.is_zipfile(path)
        if not (sha_ok and zip_ok):
            bad += 1
            bad_rows.append({"file": os.path.basename(path), "sha_ok": sha_ok, "zip_ok": zip_ok})
    print(f"已校验 {checked} | 损坏 {bad} | 索引内但本地缺失 {miss}")
    if bad_rows:
        p = os.path.join(args.out_dir, "verify_bad.csv")
        pd.DataFrame(bad_rows).to_csv(p, index=False, encoding="utf-8-sig")
        print(f"损坏清单 -> {p} (删除后重跑 download 即重下)")


# --------------------------------------------------------------------- 入口 --
def main():
    ap = argparse.ArgumentParser(description="生产级 ESEF 采集器 (filings.xbrl.org)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="采元数据+去重+覆盖")
    pi.add_argument("--country"); pi.add_argument("--out-dir", default=".")
    pi.add_argument("--delay", type=float, default=0.3); pi.add_argument("--max-pages", type=int)

    common = dict()
    for name, helptext in [("plan", "干跑统计"), ("download", "下载报告包"), ("verify", "校验已下文件")]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--index", required=True)
        p.add_argument("--years", nargs="*")
        p.add_argument("--pkg-dir", default="packages")
        p.add_argument("--out-dir", default=".")
        if name == "download":
            p.add_argument("--limit", type=int)
            p.add_argument("--workers", type=int, default=4)
            p.add_argument("--delay", type=float, default=0.4)
            p.add_argument("--no-esg", action="store_true")
            p.add_argument("--yes", action="store_true")
        if name in ("plan", "verify"):
            p.add_argument("--limit", type=int)

    args = ap.parse_args()
    setup_logging(getattr(args, "out_dir", "."))
    install_signal_handlers()
    {"index": cmd_index, "plan": cmd_plan,
     "download": cmd_download, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    main()
