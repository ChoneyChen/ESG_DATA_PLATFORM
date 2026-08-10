from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from esg_v2.evidence.contracts import EvidenceAtom, EvidenceValidationReport
from esg_v2.storage.package_layout import (
    read_jsonl,
    resolve_package_path,
    write_file_index,
    write_json,
    write_jsonl,
)
from esg_v2.storage.package_validator import PackageValidationResult, validate_package


class EvidencePackageWriter:
    PACKAGE_TYPE = "evidence-inventory"
    PACKAGE_SCHEMA = "evidence-inventory-package-v1"

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def write(
        self,
        *,
        run_id: str,
        source_manifest: dict[str, Any],
        atoms: list[EvidenceAtom],
    ) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=False)
        atoms_path = write_jsonl(
            self.output_dir / "inventory" / "atoms.jsonl",
            [atom.model_dump(mode="json") for atom in atoms],
        )
        counts = Counter(atom.atom_type for atom in atoms)
        index_path = write_json(
            self.output_dir / "inventory" / "index.json",
            {
                "schema_version": "evidence-inventory-index-v1",
                "atom_count": len(atoms),
                "counts_by_type": dict(sorted(counts.items())),
                "page_count": len({page for atom in atoms for page in atom.location.page_indices}),
                "source_node_count": len({node for atom in atoms for node in atom.source_node_ids}),
            },
        )
        validation = self._validation(atoms)
        validation_path = write_json(
            self.output_dir / "quality" / "validation-report.json",
            validation.model_dump(mode="json"),
        )
        manifest_path = write_json(
            self.output_dir / "manifest.json",
            {
                "package_type": self.PACKAGE_TYPE,
                "package_schema_version": self.PACKAGE_SCHEMA,
                "run_id": run_id,
                "source_ir_run_id": source_manifest["run_id"],
                "source_ir_revision": int(source_manifest.get("ir_revision") or 1),
                "source_ir_schema_version": source_manifest.get("schema_version"),
                "source_ir_pipeline_version": source_manifest.get("pipeline_version"),
                "source_ir_readiness": source_manifest.get("readiness"),
                "atom_count": len(atoms),
                "counts_by_type": dict(sorted(counts.items())),
                "can_run_targeted_recall": validation.can_run_targeted_recall,
                "entrypoints": {
                    "atoms": "inventory/atoms.jsonl",
                    "inventory_index": "inventory/index.json",
                    "validation_report": "quality/validation-report.json",
                    "integrity": "integrity/files.json",
                },
            },
        )
        integrity_path = write_file_index(
            self.output_dir,
            self.output_dir / "integrity" / "files.json",
        )
        validate_package(
            self.output_dir,
            expected_type=self.PACKAGE_TYPE,
            expected_schema=self.PACKAGE_SCHEMA,
            required_entrypoints={"atoms", "inventory_index", "validation_report", "integrity"},
        ).require_valid()
        return {
            "manifest": manifest_path,
            "atoms": atoms_path,
            "index": index_path,
            "validation": validation_path,
            "integrity": integrity_path,
        }

    @staticmethod
    def _validation(atoms: list[EvidenceAtom]) -> EvidenceValidationReport:
        unique_ids = len({atom.atom_id for atom in atoms}) == len(atoms)
        all_have_sources = all(atom.source_node_ids for atom in atoms)
        all_have_locations = all(atom.location.page_indices for atom in atoms)
        all_have_hashes = all(len(atom.content_sha256) == 64 for atom in atoms)
        checks = {
            "atoms_present": bool(atoms),
            "atom_ids_unique": unique_ids,
            "source_nodes_present": all_have_sources,
            "page_locations_present": all_have_locations,
            "content_hashes_present": all_have_hashes,
        }
        issues = [
            {"code": key, "severity": "error", "message": f"Evidence check failed: {key}"}
            for key, passed in checks.items()
            if not passed
        ]
        return EvidenceValidationReport(
            valid=all(checks.values()),
            can_run_targeted_recall=all(checks.values()),
            checks=checks,
            counts={"atoms": len(atoms)},
            issues=issues,
        )


class EvidencePackageReader:
    def __init__(self, root: Path):
        self.root = root
        self.manifest_path = root / "manifest.json"
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Evidence manifest not found: {self.manifest_path}")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def validate_integrity(self) -> PackageValidationResult:
        return validate_package(
            self.root,
            expected_type=EvidencePackageWriter.PACKAGE_TYPE,
            expected_schema=EvidencePackageWriter.PACKAGE_SCHEMA,
            required_entrypoints={"atoms", "inventory_index", "validation_report", "integrity"},
        )

    def entrypoint(self, key: str) -> Path:
        relative = (self.manifest.get("entrypoints") or {}).get(key)
        if not relative:
            raise KeyError(f"Evidence entrypoint not declared: {key}")
        return resolve_package_path(self.root, str(relative))

    def atoms(self) -> list[EvidenceAtom]:
        return [EvidenceAtom.model_validate(row) for row in read_jsonl(self.entrypoint("atoms"))]

    def validation_report(self) -> dict[str, Any]:
        return json.loads(self.entrypoint("validation_report").read_text(encoding="utf-8"))
