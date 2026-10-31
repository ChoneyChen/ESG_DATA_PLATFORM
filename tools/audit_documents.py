#!/usr/bin/env python3
"""Check explicitly reviewed first-party manuals and their current local links."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEWED = {
    "README.md": (
        "entry",
        "模块与五服务入口；全年份、独立条款和当前进度，未宣称全局科学验收。",
    ),
    "全链路检查报告与升级规划.md": (
        "requirement",
        "原始需求逐阶段对照；新入口指向10-31；旧路线与缓存建议由后续要求替代。",
    ),
    "更新要求.md": (
        "requirement",
        "补充需求保留为原依据；增加后续裁定与当前实现入口，不作为操作规范。",
    ),
    "ClauseESG/README.md": (
        "module",
        "一条一整体与独立SDK；结构条目非执行；命名集合仅选择。",
    ),
    "ExtractESG/README.md": (
        "module",
        "一条骨干、两类值、端口和当前安装入口；无大包分支。",
    ),
    "ConsoleESG/README.md": (
        "module",
        "对象父子路由、统一浮窗、接收/投影/发布操作；前端不重算科学规则。",
    ),
    "PlatESG/README.md": (
        "module",
        "仅当前已发布HTTP与来源端口；无SQL、审核、旧指标或冻结读适配。",
    ),
    "PlatESG/frontend/README.md": (
        "module",
        "数据集/观测子页、全期间过滤、精确原值、证据和科研导出。",
    ),
    "database/README.md": (
        "module",
        "原值、确定性投影、显式发布和来源保护；初始化由独立管理命令承担。",
    ),
    "database/contracts/README.md": (
        "module",
        "生成DTO/类型SDK，不把SQL连接实现交给消费者。",
    ),
    "ExtractESG/contracts/examples/report-batch/README.md": (
        "example",
        "输入批次格式与散列、可选资源信息；不限制事实年份。",
    ),
    "ExtractESG/document/docs/ARCHITECTURE.md": (
        "module",
        "重写当前包/证据/复核边界；移除旧读取、固定旧UI和云默认说明。",
    ),
    "ExtractESG/document/docs/reviews.md": (
        "module",
        "23种当前操作；当前计划/枚举；离线回放不等于业务科学验收。",
    ),
    "docs/README.md": ("entry", "规范、指南、基线与实施分开；删除重复旧手册入口。"),
    "docs/SYSTEM_STATE.md": (
        "entry",
        "唯一进度入口；源码、隔离、真实执行、部署分别说明。",
    ),
    "docs/CHANGELOG.md": (
        "history",
        "收口当前轮次；删除已替代的千行旧操作叙述，早期日志只留Git历史。",
    ),
    "docs/architecture/README.md": (
        "normative",
        "每模块所有权、当前接口/版本、依赖方向与宽容捕获。",
    ),
    "docs/architecture/organization.md": (
        "normative",
        "修正条款1.3；组织可重建、矩阵不聚合，原值和目标身份分列。",
    ),
    "docs/architecture/models.md": (
        "normative",
        "当前本地模型、执行信封、预算、条款搜索；不复用理解，不编造提速。",
    ),
    "docs/architecture/delivery.md": (
        "normative",
        "facts唯一权威、独立ZIP/回执及当前投影/发布桥，删除未来式旧说明。",
    ),
    "docs/architecture/products.md": (
        "normative",
        "当前观测、来源选择、发布/撤下、完整分页和精确科研类型与容量。",
    ),
    "docs/guides/clause-authoring.md": (
        "guide",
        "从模板制作完整单条；全期间、可选属性、词典、结构描述与真实样本验收。",
    ),
    "docs/guides/pdf-intake.md": (
        "guide",
        "完整PDF、批次文件/散列/元数据、接收与发布区别；不按年拆原件。",
    ),
    "docs/guides/local-operation.md": (
        "guide",
        "Console归属统一启动器、五服务、显式新库与数据根，禁止启动隐式迁移。",
    ),
    "docs/audits/2026-10-30/README.md": (
        "baseline",
        "F01–F15问题证据；保留对应日期语义，明确旧运行已清除。",
    ),
    "docs/audits/2026-10-31/README.md": (
        "baseline",
        "M01–M15主计划；后续清理裁定覆盖其旧冻结保留建议。",
    ),
    "docs/audits/2026-10-31/unresolved-clauses.md": (
        "baseline",
        "37项原阻塞及建议，标明当前已逐项归类，不能误报仍有37条阻塞。",
    ),
    "docs/implementation/2026-10-30/README.md": (
        "baseline",
        "F项历史实施与不足；标明不是当前部署/旧数据恢复说明。",
    ),
    "docs/implementation/2026-10-31/README.md": (
        "implementation",
        "按M项分别记录源码、真实报告及未完成验收；不把格式通过写成全部科学通过。",
    ),
    "docs/implementation/2026-10-31/clause-dispositions.md": (
        "implementation",
        "逐项含义、13结构/9定量/15定性和真实验收not_evaluated。",
    ),
}


def inspect():
    names = (
        subprocess.check_output(
            ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    actual = {
        p
        for p in names
        if p.endswith(".md")
        and not any(
            part in {"CrawlESG", ".claude", ".local", "node_modules", "build", "dist"}
            for part in Path(p).parts
        )
        and (ROOT / p).is_file()
    }
    errors = [
        {"path": p, "issue": "manual has no individual review decision"}
        for p in sorted(actual - REVIEWED.keys())
    ]
    records = []
    for name in sorted(actual):
        path = ROOT / name
        text = path.read_text()
        kind, decision = REVIEWED.get(name, ("unreviewed", ""))
        broken = []
        if kind not in {"baseline", "history", "requirement"}:
            for link in re.findall(r"\]\(([^)]+)\)", text):
                link = link.strip("<>").split("#")[0]
                if not link or re.match(r"[a-z]+:", link):
                    continue
                if not (path.parent / link).exists():
                    broken.append(link)
            errors.extend(
                {"path": name, "issue": "broken current local link", "target": p}
                for p in broken
            )
        records.append(
            {
                "path": name,
                "role": kind,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "lines": len(text.splitlines()),
                "decision": decision,
                "current_link_errors": broken,
            }
        )
    return {
        "schema_version": "document-review-1.0",
        "scope": "All tracked or nonignored first-party Markdown except protected Crawl and private local files.",
        "method": "Individual purpose/version/ownership decisions; current manual links verified. Baseline historical code paths refer to the recorded commit and are not current operational instructions.",
        "not_a_claim_of": "Line-by-line scientific certification of every clause, test fixture or dependency.",
        "documents": records,
        "errors": errors,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = inspect()
    if args.write:
        (ROOT / "docs/verification/document-review.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    print(
        json.dumps(
            {"documents": len(result["documents"]), "errors": result["errors"]},
            ensure_ascii=False,
        )
    )
    raise SystemExit(bool(result["errors"]))
