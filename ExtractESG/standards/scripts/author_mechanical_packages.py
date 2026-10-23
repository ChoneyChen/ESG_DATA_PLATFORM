"""Materialize the authored Chinese labels and positive definitions.

The 48 mechanically generated ESRS modules were compiled from the IG 3 catalog
with English labels. This script reads the reviewed TSV content, writes a new
immutable authoring version for each package, and publishes it. It never edits
an existing version or a compiled release.

    python scripts/author_mechanical_packages.py --check
    python scripts/author_mechanical_packages.py --apply --publish
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from esg_standard_packages.build import build_authoring, standard_root
from esg_standard_packages.compiler import StandardPackageCompiler
from esg_standard_packages.io import pretty_json_bytes

VERSION = "2.3.0"
CONTENT_DIR = Path(__file__).with_name("authoring_content")
MARKER = "pending_full_module_analysis"

SUFFIX = {
    "quantitative": "数值须来自报告原文，保留原值与单位；未披露与显式零分开。",
    "qualitative": "须说明适用主体、期间与报告边界；泛泛表述不能替代。",
    "structure": "表结构只承载成员与列的定义，不单独形成带值事实。",
    "mixed": "定量与定性信息分别保留，不以定性描述替代数值。",
}


def load_content() -> dict[str, tuple[str, str]]:
    content: dict[str, tuple[str, str]] = {}
    for path in sorted(CONTENT_DIR.glob("*.tsv")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) != 3 or not all(part.strip() for part in parts):
                raise SystemExit(f"{path.name}:{number} must hold id, label and positive definition")
            source_id, label, positive = (part.strip() for part in parts)
            if source_id in content:
                raise SystemExit(f"{path.name}:{number} repeats {source_id}")
            content[source_id] = (label, positive)
    return content


def registry_path(root: Path) -> Path:
    canonical = root / "registry/esrs/2023-set1/registry.json"
    return canonical if canonical.is_file() else root / "catalogs/esrs/2023-set1/registry.json"


def write_json(path: Path, payload) -> None:
    path.write_bytes(pretty_json_bytes(payload))


def patch_metrics(metrics: list[dict], content: dict[str, tuple[str, str]]) -> int:
    changed = 0
    for metric in metrics:
        source_id = metric.get("source_datapoint_id")
        if source_id not in content:
            raise SystemExit(f"no authored content for {source_id}")
        label, positive = content[source_id]
        metric.setdefault("labels", {})["zh"] = label
        suffix = SUFFIX.get(metric.get("data_class"), SUFFIX["qualitative"])
        metric["description"] = f"{positive}{suffix if positive.endswith('。') else '。' + suffix}"
        contract = metric.get("measurement_contract")
        if contract is not None:
            contract["positive_definition"] = positive
            contract["semantic_contract_version"] = "partitioned-semantics-v1"
        changed += 1
    return changed


def patch_concepts(concepts: list[dict], metrics: list[dict], content: dict[str, tuple[str, str]]) -> None:
    by_metric = {metric["metric_id"]: metric for metric in metrics}
    for concept in concepts:
        concept_id = concept.get("concept_id", "")
        for metric_id, metric in by_metric.items():
            if not metric_id.endswith("." + concept_id.rsplit(".concept.", 1)[-1]):
                continue
            source_id = metric["source_datapoint_id"]
            label, positive = content[source_id]
            concept.setdefault("labels", {})["zh"] = label
            concept["description"] = positive
            break


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the new authoring versions")
    parser.add_argument("--publish", action="store_true", help="publish immutable releases after writing")
    parser.add_argument("--check", action="store_true", help="verify the reviewed content covers every metric")
    parser.add_argument("--only", help="author one package (by disclosure requirement) for a trial run")
    args = parser.parse_args(argv)

    root = standard_root()
    content = load_content()
    registry = json.loads(registry_path(root).read_text(encoding="utf-8"))
    pending = [module for module in registry["modules"]
               if module.get("executable") and module.get("core_analysis_state") == MARKER]
    expected = {source_id for module in pending for source_id in module["source_datapoint_ids"]}
    known = {source_id for module in registry["modules"] for source_id in module["source_datapoint_ids"]}
    missing, unexpected = sorted(expected - set(content)), sorted(set(content) - known)
    if missing or unexpected:
        raise SystemExit(f"content differs from the catalog: missing={missing[:5]} unexpected={unexpected[:5]}")
    if args.check and not args.apply:
        print(f"{len(content)} authored metrics across {len(pending)} packages; content matches.")
        return 0
    if args.only:
        pending = [module for module in pending if module["disclosure_requirement"] == args.only]
        if not pending:
            raise SystemExit(f"unknown or already authored requirement: {args.only}")

    compiler = StandardPackageCompiler()
    for module in pending:
        old_dir = (root / module["source_directory"]).resolve()
        new_dir = old_dir.parent / VERSION
        if new_dir.exists():
            raise SystemExit(f"{new_dir} already exists; author a new version instead of overwriting")
        shutil.copytree(old_dir, new_dir)

        manifest_path = new_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["package_version"] = VERSION
        manifest["core_selection_reason"] = (
            "核心条款取该披露要求的锚点数据点（disclosure_role=anchor）；"
            "已逐条撰写中文标签与正向定义，仍待真实报告验收与业务批准。"
        )
        write_json(manifest_path, manifest)

        metrics_path = new_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        patch_metrics(metrics, content)
        write_json(metrics_path, metrics)

        concepts_path = new_dir / "concepts.json"
        if concepts_path.is_file():
            concepts = json.loads(concepts_path.read_text(encoding="utf-8"))
            patch_concepts(concepts, metrics, content)
            write_json(concepts_path, concepts)

        package = compiler.compile(
            core_path=root / "schemas/core" / manifest["core_schema_version"] / "core.json",
            module_dir=new_dir,
        )
        module.update(
            core_analysis_state="authored_draft",
            package_version=VERSION,
            source_directory=str(new_dir.relative_to(root)),
            source_digest=package.compilation.source_digest,
            runtime_path=f"releases/{module['package_id']}/{VERSION}/package.json",
            next_steps=["真实报告金标准验收", "业务批准并发布新版本"],
        )
        print(f"authored {module['package_id']} {VERSION} ({len(metrics)} metrics)")

    write_json(registry_path(root), registry)
    if args.publish:
        packages, _ = build_authoring(root, publish=True)
        print(f"published {len(packages)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
