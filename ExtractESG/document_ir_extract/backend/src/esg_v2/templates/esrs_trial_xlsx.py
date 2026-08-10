from __future__ import annotations

import hashlib
from pathlib import Path

from esg_v2.standards.compiler import RequirementProfileCompiler
from esg_v2.standards.contracts import CompiledTaskSet, StandardTaskSpec
from esg_v2.templates.contracts import TemplateAnswer, TemplateExportResult
from esg_v2.templates.xlsx_xml import XlsxTemplateDocument


class EsrsTrialXlsxAdapter:
    adapter_id = "esrs-direct-fill-trial-v1"
    sheet_name = "报告试填表"
    headers = {
        "A": "序号",
        "B": "ESG维度",
        "C": "主题标准",
        "D": "议题英文标题",
        "E": "Disclosure Requirement",
        "F": "Data Point ID",
        "G": "Paragraph",
        "H": "Related AR",
        "I": "官方Data Point原文",
        "J": "Data Type",
        "K": "暂定为直接填充的原因",
    }
    output_columns = {"L": "status", "M": "location", "N": "quote", "O": "value", "P": "notes"}

    def __init__(self, compiler: RequirementProfileCompiler | None = None):
        self.compiler = compiler or RequirementProfileCompiler()

    def supports(self, path: Path) -> bool:
        try:
            rows = XlsxTemplateDocument(path).rows(self.sheet_name, min_column="A", max_column="K")
        except (FileNotFoundError, KeyError, ValueError):
            return False
        if not rows:
            return False
        return all(rows[0].get(column) == value for column, value in self.headers.items())

    def compile(self, path: Path) -> CompiledTaskSet:
        document = XlsxTemplateDocument(path)
        rows = document.rows(self.sheet_name, min_column="A", max_column="P")
        if not rows or not all(rows[0].get(column) == value for column, value in self.headers.items()):
            raise ValueError("Workbook does not match the ESRS direct-fill trial template")
        tasks: list[StandardTaskSpec] = []
        warnings: list[str] = []
        for row in rows[1:]:
            if not row.get("F") or not row.get("I"):
                continue
            source_row = int(row["__row__"])
            sequence = self._int(row.get("A"), default=source_row - 1)
            data_point_id = row["F"].strip()
            task = StandardTaskSpec(
                task_id=f"esrs:{data_point_id}",
                sequence=sequence,
                framework="ESRS",
                topic_standard=row.get("C") or None,
                esg_dimension=row.get("B") or None,
                topic=row.get("D") or row.get("C") or "",
                disclosure_requirement=row.get("E") or None,
                data_point_id=data_point_id,
                paragraph=row.get("G") or None,
                related_ar=row.get("H") or None,
                official_text=row.get("I") or "",
                data_type=row.get("J") or None,
                rationale=row.get("K") or None,
                source_sheet=self.sheet_name,
                source_row=source_row,
                source_fields={self.headers[column]: row.get(column, "") for column in self.headers},
            )
            tasks.append(task)
            stale = [column for column in self.output_columns if row.get(column) == "38"]
            if stale:
                warnings.append(f"row {source_row}: placeholder value 38 found in {','.join(stale)}; export will replace it")
        profiles = self.compiler.compile_all(tasks)
        for profile in profiles:
            if profile.execution_strategy == "generic_local_rule":
                warnings.append(
                    f"row {profile.source_row}: {profile.data_point_id} uses the generic local execution rule; review its compiled slots before production use"
                )
        return CompiledTaskSet(
            template_name=path.name,
            template_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            source_sheet=self.sheet_name,
            requirements=profiles,
            warnings=warnings,
        )

    def export(
        self,
        source_path: Path,
        output_path: Path,
        answers: list[TemplateAnswer],
    ) -> TemplateExportResult:
        document = XlsxTemplateDocument(source_path)
        source_rows = document.rows(self.sheet_name, min_column="A", max_column="X")
        source_cell_count = sum(len([key for key, value in row.items() if key != "__row__" and value != ""]) for row in source_rows)
        updates: dict[str, str] = {}
        for answer in answers:
            status = {
                "found": "是",
                "not_found": "否",
                "not_applicable": "否（条件不适用）",
                "uncertain": "不确定",
                "system_failed": "系统失败",
            }[answer.status]
            values = {
                "L": status,
                "M": answer.location,
                "N": answer.quote,
                "O": answer.value,
                "P": answer.notes,
            }
            for column, value in values.items():
                updates[f"{column}{answer.source_row}"] = value
        updated = document.write_cells(output_path, self.sheet_name, updates)
        return TemplateExportResult(
            output_path=output_path,
            updated_cells=updated,
            preserved_cell_count=max(0, source_cell_count - len(updated)),
        )

    @staticmethod
    def _int(value: str | None, default: int) -> int:
        try:
            return int(float(value or ""))
        except ValueError:
            return default
