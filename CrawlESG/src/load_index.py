# -*- coding: utf-8 -*-
"""
用 schema.sql + 索引 CSV 重建 SQLite 索引库 (esef_index.db)。
DB 是 rebuildable 产物, 不进 git; 任何人 clone 后跑这个脚本即可得到同一个库。

用法:
  python src/load_index.py
产出:
  esef_index.db  (entity / report_raw / report / esrs_datapoint / v_coverage)
并自动跑设计方案第 11 节的不变量检查, 全 PASS 才算载入成功。
"""
import os, sqlite3, sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(ROOT, "schema", "schema.sql")
PRIMARY = os.path.join(ROOT, "index", "gb_filings_primary.csv")
RAW = os.path.join(ROOT, "index", "gb_filings_raw.csv")
DB = os.path.join(ROOT, "esef_index.db")

def main():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(open(SCHEMA, encoding="utf-8").read())

    prim = pd.read_csv(PRIMARY)
    # entity
    ent = prim[["lei", "entity_name"]].dropna().drop_duplicates("lei")
    con.executemany("INSERT OR IGNORE INTO entity(lei,entity_name) VALUES(?,?)",
                    ent.values.tolist())

    # report_raw (审计底稿, 若有 raw CSV)
    if os.path.exists(RAW):
        raw = pd.read_csv(RAW)
        cols = ["fxo_id","lei","period_end","country","report_type","seq","sha256",
                "error_count","warning_count","inconsistency_count","processed",
                "date_added","package_url","json_url","viewer_url","api_id"]
        con.executemany(
            f"INSERT OR IGNORE INTO report_raw({','.join(cols)}) "
            f"VALUES({','.join(['?']*len(cols))})",
            raw[cols].where(pd.notna(raw[cols]), None).values.tolist())

    # report (report_id 按 lei,period_end 确定性排序生成 -> 可复现)
    p = prim.sort_values(["lei", "period_end"]).reset_index(drop=True)
    if "report_id" not in p.columns:
        p["report_id"] = ["R%06d" % (i + 1) for i in range(len(p))]
    if "esg_flag" not in p.columns:
        p["esg_flag"] = 0
    cols = ["report_id","fxo_id","lei","period_end","country","report_type",
            "sha256","esg_flag","package_url","json_url","viewer_url","n_versions"]
    con.executemany(
        f"INSERT INTO report({','.join(cols)}) VALUES({','.join(['?']*len(cols))})",
        p[cols].where(pd.notna(p[cols]), None).values.tolist())
    con.commit()

    # 不变量检查
    q = lambda s: con.execute(s).fetchone()[0]
    checks = [
        ("sha256 全唯一", q("SELECT COUNT(*)-COUNT(DISTINCT sha256) FROM report") == 0),
        ("(lei,period_end) 唯一",
         q("SELECT COUNT(*)-COUNT(DISTINCT lei||period_end) FROM report") == 0),
        ("无孤儿外键 report.lei",
         q("SELECT COUNT(*) FROM report r LEFT JOIN entity e ON r.lei=e.lei "
           "WHERE e.lei IS NULL") == 0),
    ]
    if os.path.exists(RAW):
        checks.append(("去重份数对账",
                       q("SELECT SUM(n_versions) FROM report") ==
                       q("SELECT COUNT(*) FROM report_raw")))
    print(f"载入: report={q('SELECT COUNT(*) FROM report')} "
          f"entity={q('SELECT COUNT(DISTINCT lei) FROM report')}")
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    con.close()
    if not ok:
        sys.exit("不变量检查未通过, 数据有问题。")
    print(f"OK -> {DB}")

if __name__ == "__main__":
    main()
