"""Materialize the first-pass human review decisions in a structured CSV."""
from pathlib import Path
import csv

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "outputs" / "evidence_review_queue.csv"
dst = ROOT / "outputs" / "evidence_reviewed_candidates.csv"

def decision(page: str, clause: str):
    if clause in {"gri_305_1", "gri_305_2", "gri_305_7"}:
        return "reject_candidate", "本页未见对应 Scope 1/Scope 2/NOx 排放披露。"
    if page == "62" and clause == "gri_201_2":
        return "uncertain", "有风险/影响表，但尚未完成财务影响单元格级核对。"
    if page in {"66", "67"} and clause == "hkex_c2_27":
        return "uncertain", "续页表格列出风险影响，但未单独证明风险管理流程。"
    if page in {"66", "67"} and clause == "gri_3_3":
        return "reject_candidate", "该页主要是风险影响表，缺少管理政策、行动、目标或有效性内容。"
    return "keep_candidate", "主题与官方条款相关；需回到原文/表格完成最终 Evidence 审核。"

with src.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))
for row in rows:
    row["manual_decision"], row["manual_reason"] = decision(row["printed_page_label"], row["candidate_clause_id"])
with dst.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
print(f"reviewed_candidates={len(rows)}")
print(dst)
