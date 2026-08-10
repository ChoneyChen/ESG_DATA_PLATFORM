from __future__ import annotations

import json
import importlib
import hashlib
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from esg_v2.document.contracts import (
    BlockIR,
    CellIR,
    DocumentIR,
    DocumentIRMetadata,
    PageIR,
    SectionIR,
    SourceTrace,
    TableIR,
    ValidationReport,
)
from esg_v2.document.writer import DocumentIrWriter
from esg_v2.evidence.builder import EvidenceAdmissionError, EvidenceInventoryBuilder
from esg_v2.evidence.contracts import EvidenceAtom, EvidenceBuildRequest, EvidenceLocation
from esg_v2.evidence.local_index import LocalEvidenceIndex
from esg_v2.evidence.package import EvidencePackageReader
from esg_v2.standards.compiler import RequirementProfileCompiler
from esg_v2.standards.contracts import StandardTaskSpec
from esg_v2.targeted.assessment import TargetedAssessor
from esg_v2.targeted.catalog import DisclosureCatalog
from esg_v2.targeted.contracts import TargetedRunRequest
from esg_v2.targeted.features import DisclosureFeatureExtractor
from esg_v2.targeted.package import TargetedPackageReader
from esg_v2.targeted.retrieval import HybridLocalRetriever
from esg_v2.targeted.workflow import TargetedRecallWorkflow
from esg_v2.templates.esrs_trial_xlsx import EsrsTrialXlsxAdapter
from esg_v2.templates.xlsx_xml import XlsxTemplateDocument


TRACE = SourceTrace(parser="fixture", parser_version="1")


def _write_ready_ir(root: Path, *, admitted: bool = True) -> str:
    run_id = "ir-20260809T010101Z-000000000001" if admitted else "ir-20260809T010101Z-000000000002"
    page = PageIR(
        page_id="page-0001",
        page_index=0,
        page_number=1,
        text="Climate policy and workforce metrics",
        text_length=36,
        block_ids=["block-p0001-0001", "block-p0001-0002", "block-p0001-0003"],
        table_ids=["table-p0001-0001"],
        source_trace=TRACE,
    )
    blocks = [
        BlockIR(
            block_id="block-p0001-0001",
            page_index=0,
            order=0,
            block_type="heading",
            text="Climate change",
            section_id="section-0001",
            source_trace=TRACE,
        ),
        BlockIR(
            block_id="block-p0001-0002",
            page_index=0,
            order=1,
            block_type="paragraph",
            text="The company adopted a climate change policy and implemented related actions.",
            section_id="section-0001",
            source_trace=TRACE,
        ),
        BlockIR(
            block_id="block-p0001-0003",
            page_index=0,
            order=2,
            block_type="table_markdown",
            text="Employees by gender",
            section_id="section-0002",
            table_id="table-p0001-0001",
            source_trace=TRACE,
        ),
    ]
    cells = [
        CellIR(
            cell_id="cell-p0001-t0001-r0001-c0001",
            table_id="table-p0001-0001",
            page_index=0,
            row_index=0,
            col_index=0,
            text="Gender",
            is_header=True,
            source_trace=TRACE,
        ),
        CellIR(
            cell_id="cell-p0001-t0001-r0001-c0002",
            table_id="table-p0001-0001",
            page_index=0,
            row_index=0,
            col_index=1,
            text="Number of employees",
            is_header=True,
            source_trace=TRACE,
        ),
        CellIR(
            cell_id="cell-p0001-t0001-r0002-c0001",
            table_id="table-p0001-0001",
            page_index=0,
            row_index=1,
            col_index=0,
            text="Female",
            column_header_path=["Gender"],
            source_trace=TRACE,
        ),
        CellIR(
            cell_id="cell-p0001-t0001-r0002-c0002",
            table_id="table-p0001-0001",
            page_index=0,
            row_index=1,
            col_index=1,
            text="420",
            column_header_path=["Number of employees"],
            row_header_path=["Female"],
            source_trace=TRACE,
        ),
        CellIR(
            cell_id="cell-p0001-t0001-r0003-c0001",
            table_id="table-p0001-0001",
            page_index=0,
            row_index=2,
            col_index=0,
            text="Male",
            column_header_path=["Gender"],
            source_trace=TRACE,
        ),
        CellIR(
            cell_id="cell-p0001-t0001-r0003-c0002",
            table_id="table-p0001-0001",
            page_index=0,
            row_index=2,
            col_index=1,
            text="580",
            column_header_path=["Number of employees"],
            row_header_path=["Male"],
            source_trace=TRACE,
        ),
    ]
    table = TableIR(
        table_id="table-p0001-0001",
        page_index=0,
        page_indices=[0],
        order=2,
        block_id="block-p0001-0003",
        caption="Characteristics of employees by gender",
        markdown="| Gender | 2024 number of employees (persons) |\n| --- | --- |\n| Female | 420 |\n| Male | 580 |",
        row_count=3,
        column_count=2,
        cells=cells,
        header_row_indices=[0],
        source_trace=TRACE,
    )
    document = DocumentIR(
        metadata=DocumentIRMetadata(run_id=run_id, ocr_run_id="ocr-20260809T010101Z-000000000001"),
        readiness="ready" if admitted else "review_required",
        pages=[page],
        sections=[
            SectionIR(
                section_id="section-0001",
                title="Climate change",
                level=1,
                start_page_index=0,
                end_page_index=0,
                block_ids=["block-p0001-0001", "block-p0001-0002"],
            ),
            SectionIR(
                section_id="section-0002",
                title="Own workforce",
                level=1,
                start_page_index=0,
                end_page_index=0,
                block_ids=["block-p0001-0003"],
            ),
        ],
        blocks=blocks,
        tables=[table],
        validation_report=ValidationReport(
            readiness="ready" if admitted else "review_required",
            checks={"can_build_evidence": admitted},
        ),
    )
    DocumentIrWriter(root / run_id).write(document)
    return run_id


HEADERS = [
    "序号", "ESG维度", "主题标准", "议题英文标题", "Disclosure Requirement", "Data Point ID",
    "Paragraph", "Related AR", "官方Data Point原文", "Data Type", "暂定为直接填充的原因",
    "是否在报告中找到", "报告页码/章节", "报告原文摘录", "填充值/内容", "同学备注",
]


def _write_template(path: Path, task_rows: list[list[object]] | None = None) -> None:
    default_rows = [
        [1, "E", "ESRS E1", "Climate change", "mdr_no_p", "E1.MDR-P_07-08", "62", "mdr_no_p", "Disclosures to be reported in case the undertaking has not adopted policies", "38", "reason", "38", "38", "38", "38", "38"],
        [2, "S", "ESRS S1", "Own workforce", "S1-6", "S1-6_01", "50 a", "", "Characteristics of undertaking's employees - number of employees by gender [table]", "Table 1", "reason", "38", "38", "38", "38", "38"],
    ]
    rows = [HEADERS, *(task_rows or default_rows)]
    sheet_rows = []
    for row_index, values in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(values, start=1):
            reference = f"{_column(column_index)}{row_index}"
            cells.append(f'<c r="{reference}" t="inlineStr"><is><t>{_escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="报告试填表" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def _column(number: int) -> str:
    output = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _evidence_atom(
    atom_id: str,
    text: str,
    *,
    section: str,
    table_id: str | None = None,
    atom_type: str = "table_region_atom",
    row_index: int | None = None,
) -> EvidenceAtom:
    return EvidenceAtom(
        atom_id=atom_id,
        atom_type=atom_type,
        source_text=text,
        search_text=f"{section} | {text}",
        location=EvidenceLocation(page_indices=[0], page_numbers=[1], section_path=[section]),
        source_node_ids=[table_id or atom_id],
        source_table_ids=[table_id] if table_id else [],
        row_index=row_index,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _profile(data_point_id: str, official_text: str, topic: str) -> object:
    control = next(
        (f"mdr_no_{letter.casefold()}" for letter in ("P", "A", "T") if f".MDR-{letter}" in data_point_id),
        None,
    )
    return RequirementProfileCompiler().compile(
        StandardTaskSpec(
            task_id=f"esrs:{data_point_id}",
            sequence=1,
            framework="ESRS",
            topic=topic,
            disclosure_requirement=control,
            data_point_id=data_point_id,
            official_text=official_text,
            data_type=None if control else "Table 1",
            source_sheet="test",
            source_row=2,
        )
    )


def _assess(tmp_path: Path, profile: object, atoms: list[EvidenceAtom]):
    catalog = DisclosureCatalog.build(atoms)
    index = LocalEvidenceIndex(tmp_path / "index.sqlite3")
    index.build(atoms)
    retrieval = HybridLocalRetriever(index, atoms, catalog=catalog).retrieve(
        [profile], mode_requested="local_strict", top_k=20
    )
    return TargetedAssessor(atoms, catalog).assess(profile, retrieval.candidates[profile.requirement_id])


def test_evidence_inventory_requires_admitted_document_ir(tmp_path: Path) -> None:
    ir_root = tmp_path / "document_ir_output"
    run_id = _write_ready_ir(ir_root, admitted=False)
    with pytest.raises(EvidenceAdmissionError, match="can_build_evidence=false"):
        EvidenceInventoryBuilder(ir_root, tmp_path / "evidence_output").build(
            EvidenceBuildRequest(ir_run_id=run_id)
        )


def test_evidence_inventory_is_grounded_and_package_valid(tmp_path: Path) -> None:
    ir_root = tmp_path / "document_ir_output"
    run_id = _write_ready_ir(ir_root)
    result = EvidenceInventoryBuilder(ir_root, tmp_path / "evidence_output").build(
        EvidenceBuildRequest(ir_run_id=run_id, run_id="evd-20260809T010101Z-000000000001")
    )
    reader = EvidencePackageReader(result.output_dir)
    assert reader.validate_integrity().valid is True
    atoms = reader.atoms()
    assert {"paragraph_atom", "table_cell_atom", "table_row_atom", "table_region_atom"}.issubset(
        {atom.atom_type for atom in atoms}
    )
    assert all(atom.source_node_ids and atom.location.page_numbers == [1] for atom in atoms)
    assert all(len(atom.content_sha256) == 64 for atom in atoms)


def test_targeted_workflow_is_zero_cloud_and_exports_only_answer_columns(tmp_path: Path) -> None:
    ir_root = tmp_path / "document_ir_output"
    run_id = _write_ready_ir(ir_root)
    template = tmp_path / "task.xlsx"
    _write_template(template)
    before = XlsxTemplateDocument(template).rows("报告试填表", min_column="A", max_column="P")
    workflow = TargetedRecallWorkflow(
        document_ir_root=ir_root,
        evidence_output_root=tmp_path / "evidence_output",
        targeted_output_root=tmp_path / "targeted_fill_output",
    )
    result = workflow.run(
        TargetedRunRequest(
            ir_run_id=run_id,
            template_path=template,
            run_id="trg-20260809T010101Z-000000000001",
            mode="local_strict",
        )
    )
    reader = TargetedPackageReader(result.output_dir)
    assert reader.validate_integrity().valid is True
    assert result.cloud_call_count == 0
    assert json.loads(reader.entrypoint("cloud_calls").read_text(encoding="utf-8") or "[]") == []
    answers = {row["data_point_id"]: row for row in reader.answers()}
    assert answers["E1.MDR-P_07-08"]["status"] == "not_applicable"
    assert answers["S1-6_01"]["status"] == "found"
    assert answers["S1-6_01"]["selected_source_node_ids"]
    assert reader.disclosure_catalog()
    assert reader.verifications()
    assert all(row["all_retrieved_candidates_verified"] for row in reader.search_coverage())
    after = XlsxTemplateDocument(result.export_path).rows("报告试填表", min_column="A", max_column="P")
    assert [{key: row.get(key) for key in "ABCDEFGHIJK"} for row in before] == [
        {key: row.get(key) for key in "ABCDEFGHIJK"} for row in after
    ]
    assert after[1]["L"] == "否（条件不适用）"
    assert after[2]["L"] == "是"
    assert all(after[index][column] != "38" for index in (1, 2) for column in "LMNOP")


def test_targeted_api_keeps_frontend_optional_and_exposes_evidence_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir_root = tmp_path / "document_ir_output"
    ir_run_id = _write_ready_ir(ir_root)
    template = tmp_path / "task.xlsx"
    _write_template(template)
    monkeypatch.setenv("ESG_V2_OCR_OUTPUT_DIR", str(tmp_path / "ocr_output"))
    monkeypatch.setenv("ESG_V2_DOCUMENT_IR_OUTPUT_DIR", str(ir_root))
    monkeypatch.setenv("ESG_V2_EVIDENCE_OUTPUT_DIR", str(tmp_path / "evidence_output"))
    monkeypatch.setenv("ESG_V2_TARGETED_OUTPUT_DIR", str(tmp_path / "targeted_fill_output"))
    monkeypatch.setenv("ESG_V2_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("ESG_V2_OCR_JOB_STATE_DIR", str(tmp_path / "jobs" / "ocr"))
    monkeypatch.setenv("ESG_V2_DOCUMENT_IR_JOB_STATE_DIR", str(tmp_path / "jobs" / "document-ir"))
    monkeypatch.setenv("ESG_V2_TARGETED_JOB_STATE_DIR", str(tmp_path / "jobs" / "targeted"))

    import esg_v2.api.main as api_main

    api_main = importlib.reload(api_main)
    client = TestClient(api_main.app)
    with template.open("rb") as handle:
        plan = client.post(
            "/api/targeted-fill/plan",
            data={"ir_run_id": ir_run_id},
            files={"file": (template.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert plan.status_code == 200
    assert plan.json()["requirement_count"] == 2
    assert plan.json()["ir_admission"]["can_build_evidence"] is True

    with template.open("rb") as handle:
        created = client.post(
            "/api/targeted-fill/jobs",
            data={"ir_run_id": ir_run_id, "mode": "local_strict", "top_k": "20"},
            files={"file": (template.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    state = created.json()
    for _ in range(100):
        state = client.get(f"/api/targeted-fill/jobs/{run_id}").json()
        if state["status"] in {"done", "failed"}:
            break
        time.sleep(0.01)
    assert state["status"] == "done", state
    assert state["summary"]["cloud_call_count"] == 0
    detail = client.get(f"/api/targeted-fill/jobs/{run_id}/requirements/esrs%3AS1-6_01")
    assert detail.status_code == 200
    assert detail.json()["selected_evidence"]["atoms"]
    assert detail.json()["disclosure_groups"]
    assert detail.json()["verifications"]
    assert detail.json()["search_coverage"]["all_retrieved_candidates_verified"] is True
    assert client.get(f"/api/targeted-fill/jobs/{run_id}/export").status_code == 200


def test_template_adapter_accepts_variable_requirement_counts(tmp_path: Path) -> None:
    template = tmp_path / "variable-task-count.xlsx"
    task_rows = [
        [1, "S", "ESRS S1", "Own workforce", "S1-6", "S1-6_01", "50 a", "", "Characteristics of undertaking's employees - number of employees by gender [table]", "Table 1", "reason", "", "", "", "", ""],
        [2, "S", "ESRS S1", "Own workforce", "S1-17", "S1-17_01", "103 a", "", "Number of incidents of discrimination [table]", "Table 1", "reason", "", "", "", "", ""],
        [3, "G", "ESRS G1", "Business conduct", "G1-4", "G1-4_03", "21 c", "", "Prevention and detection of corruption or bribery - anti-corruption and bribery training table", "Table 1", "reason", "", "", "", "", ""],
        [4, "E", "ESRS E1", "Climate change", "E1-6", "E1-6_01", "44", "", "GHG emissions per scope [table]", "Table 1", "reason", "", "", "", "", ""],
    ]
    _write_template(template, task_rows)
    compiled = EsrsTrialXlsxAdapter().compile(template)
    assert len(compiled.requirements) == 4
    assert [item.source_row for item in compiled.requirements] == [2, 3, 4, 5]
    assert {item.profile_schema_version for item in compiled.requirements} == {"requirement-execution-spec-v2"}


def test_unseen_quantitative_requirement_gets_generic_fillable_contract() -> None:
    profile = _profile(
        "S1-custom-safety",
        "Number and rate of recordable work-related injuries during the reporting period [table]",
        "Own workforce",
    )
    assert profile.execution_strategy == "generic_local_rule"
    assert {slot.slot_id for slot in profile.required_slots} >= {"concept", "value", "unit", "period"}
    assert {"count_incident", "percent"}.issubset(set(profile.unit_families))
    assert profile.measure_aliases


def test_headcount_table_beats_gender_turnover_false_positive(tmp_path: Path) -> None:
    profile = _profile(
        "S1-6_01",
        "Characteristics of undertaking's employees - number of employees by gender [table]",
        "Own workforce",
    )
    turnover = _evidence_atom(
        "atom-turnover-region",
        "<table><tr><td>2024 employee turnover by gender</td><td>percentage</td></tr>"
        "<tr><td>Male</td><td>19.54%</td></tr><tr><td>Female</td><td>19.70%</td></tr></table>",
        section="Own workforce",
        table_id="table-turnover",
    )
    headcount = _evidence_atom(
        "atom-headcount-region",
        "<table><tr><td>2024 number of employees by gender</td><td>persons</td></tr>"
        "<tr><td>Male</td><td>9,199</td></tr><tr><td>Female</td><td>5,985</td></tr></table>",
        section="Own workforce",
        table_id="table-headcount",
    )
    answer = _assess(tmp_path, profile, [turnover, headcount])
    assert answer.status == "found"
    assert answer.selected_group_ids == ["disclosure-table:table-headcount"]
    assert "turnover" not in answer.quote.casefold()


def test_standard_index_routes_but_cannot_replace_explicit_zero_fact(tmp_path: Path) -> None:
    profile = _profile(
        "S1-17_01",
        "Number of incidents of discrimination [table]",
        "Own workforce",
    )
    index_row = _evidence_atom(
        "atom-discrimination-index",
        "406-1 | incidents of discrimination and corrective actions | employee rights",
        section="GRI Standards Index",
        table_id="table-index",
    )
    zero_fact = _evidence_atom(
        "atom-discrimination-zero",
        "In the reporting period, the company reported no incidents of discrimination.",
        section="Own workforce > Diversity and equality",
        atom_type="paragraph_atom",
    )
    answer = _assess(tmp_path, profile, [index_row, zero_fact])
    assert answer.status == "found"
    assert answer.value == "0"
    assert answer.selected_group_ids == ["disclosure-atom:atom-discrimination-zero"]


def test_separate_contract_and_gender_rows_do_not_fake_cross_tab(tmp_path: Path) -> None:
    profile = _profile(
        "S1-6_07",
        "Characteristics of undertaking's employees - information on employees by contract type and gender [table]",
        "Own workforce",
    )
    table_id = "table-separate-axes"
    region = _evidence_atom(
        "atom-separate-region",
        "<table><tr><td>2024 employees by gender and employment type</td><td>persons</td></tr>"
        "<tr><td>Male</td><td>100</td></tr><tr><td>Female</td><td>80</td></tr>"
        "<tr><td>Full-time</td><td>170</td></tr><tr><td>Part-time</td><td>10</td></tr></table>",
        section="Own workforce",
        table_id=table_id,
    )
    rows = [
        _evidence_atom("atom-row-male", "Male | 100", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=1),
        _evidence_atom("atom-row-female", "Female | 80", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=2),
        _evidence_atom("atom-row-full", "Full-time | 170", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=3),
        _evidence_atom("atom-row-part", "Part-time | 10", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=4),
    ]
    answer = _assess(tmp_path, profile, [region, *rows])
    assert answer.status == "uncertain"
    assert answer.internal_decision == "partial"
    assert any("cross-tab intersection" in item.reason for item in answer.slot_coverage)


def test_anti_corruption_training_table_beats_generic_business_ethics(tmp_path: Path) -> None:
    profile = _profile(
        "G1-4_03",
        "Prevention and detection of corruption or bribery - anti-corruption and bribery training table",
        "Business conduct",
    )
    generic = _evidence_atom(
        "atom-business-ethics",
        "Business ethics | fair competition and compliant sales practices",
        section="Business conduct",
        table_id="table-generic",
    )
    training = _evidence_atom(
        "atom-training-region",
        "<table><tr><td>2024 anti-corruption training</td><td>persons</td><td>hours</td></tr>"
        "<tr><td>Directors and employees trained</td><td>13,987</td><td>39,166</td></tr></table>",
        section="Business conduct",
        table_id="table-training",
    )
    answer = _assess(tmp_path, profile, [generic, training])
    assert answer.status == "found"
    assert answer.selected_group_ids == ["disclosure-table:table-training"]


def test_all_instances_policy_keeps_multiple_complete_disclosures(tmp_path: Path) -> None:
    profile = _profile(
        "G1-4_03",
        "Prevention and detection of corruption or bribery - anti-corruption and bribery training table",
        "Business conduct",
    )
    employee_training = _evidence_atom(
        "atom-employee-training",
        "<table><tr><td>2024 anti-corruption training for employees</td><td>persons</td></tr>"
        "<tr><td>Employees trained</td><td>12,000</td></tr></table>",
        section="Business conduct > Employees",
        table_id="table-employee-training",
    )
    supplier_training = _evidence_atom(
        "atom-supplier-training",
        "<table><tr><td>2024 anti-bribery training for suppliers</td><td>persons</td></tr>"
        "<tr><td>Supplier representatives trained</td><td>860</td></tr></table>",
        section="Business conduct > Suppliers",
        table_id="table-supplier-training",
    )
    answer = _assess(tmp_path, profile, [employee_training, supplier_training])
    assert answer.status == "found"
    assert set(answer.selected_group_ids) == {
        "disclosure-table:table-employee-training",
        "disclosure-table:table-supplier-training",
    }
    assert len(answer.facts) == 2
    assert "--- evidence ---" in answer.quote


def test_candidate_limit_forces_uncertain_instead_of_false_not_found(tmp_path: Path) -> None:
    profile = _profile(
        "S1-6_01",
        "Characteristics of undertaking's employees - number of employees by gender [table]",
        "Own workforce",
    )
    atoms = [
        _evidence_atom(
            f"atom-index-{index}",
            f"Employees by gender | number of employees | Male | Female | persons | reference {index}",
            section="GRI Standards Index",
            table_id=f"table-index-{index}",
        )
        for index in range(8)
    ]
    catalog = DisclosureCatalog.build(atoms)
    local_index = LocalEvidenceIndex(tmp_path / "limited-index.sqlite3")
    local_index.build(atoms)
    retrieval = HybridLocalRetriever(local_index, atoms, catalog=catalog).retrieve(
        [profile], mode_requested="local_strict", top_k=5
    )
    trace = retrieval.traces[0]
    assert trace.candidate_limit_reached is True
    answer = TargetedAssessor(atoms, catalog).assess(
        profile,
        retrieval.candidates[profile.requirement_id],
        search_truncated=trace.candidate_limit_reached,
    )
    assert answer.status == "uncertain"
    assert "上限" in answer.reasoning


def test_index_atom_is_blocked_even_when_section_heading_is_wrong(tmp_path: Path) -> None:
    profile = _profile(
        "E5.MDR-T_14-19",
        "Disclosures to be reported if the undertaking has not adopted targets",
        "Resource use and circular economy",
    )
    index = _evidence_atom(
        "atom-index-wrong-section",
        "HKEX ESG indicator index | circular economy | target established for waste reduction",
        section="Conclusion",
        atom_type="index_atom",
    )
    answer = _assess(tmp_path, profile, [index])
    assert answer.status == "uncertain"
    assert answer.selected_group_ids == []


def test_weak_compliance_word_cannot_prove_business_conduct_policy(tmp_path: Path) -> None:
    profile = _profile(
        "G1.MDR-P_07-08",
        "Disclosures to be reported in case the undertaking has not adopted policies",
        "Business conduct",
    )
    green_lease = _evidence_atom(
        "atom-green-lease",
        "Green leasing actions include compliant waste disposal and an established material reuse system.",
        section="Green value chain > Green leasing",
        atom_type="paragraph_atom",
    )
    answer = _assess(tmp_path, profile, [green_lease])
    assert answer.status == "uncertain"
    assert answer.selected_group_ids == []


def test_section_heading_alone_cannot_relabel_unrelated_policy(tmp_path: Path) -> None:
    profile = _profile(
        "E3.MDR-P_07-08",
        "Disclosures to be reported in case the undertaking has not adopted policies",
        "Water and marine resources",
    )
    dust_policy = _evidence_atom(
        "atom-dust-policy",
        "The undertaking established a dust-control policy for particulate air emissions.",
        section="Water resources management",
        atom_type="paragraph_atom",
    )
    answer = _assess(tmp_path, profile, [dust_policy])
    assert answer.status == "uncertain"
    assert answer.selected_group_ids == []


def test_fact_proposals_only_keep_rows_for_the_requested_dimension(tmp_path: Path) -> None:
    profile = _profile(
        "S1-6_01",
        "Characteristics of undertaking's employees - number of employees by gender [table]",
        "Own workforce",
    )
    table_id = "table-multi-axis-headcount"
    region = _evidence_atom(
        "atom-multi-axis-region",
        "<table><tr><td>2024 number of employees</td><td>persons</td></tr>"
        "<tr><td>Male</td><td>100</td></tr><tr><td>Female</td><td>80</td></tr>"
        "<tr><td>Full-time</td><td>170</td></tr><tr><td>China</td><td>160</td></tr></table>",
        section="Own workforce",
        table_id=table_id,
    )
    rows = [
        _evidence_atom("atom-gender-male", "Male | 100", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=1),
        _evidence_atom("atom-gender-female", "Female | 80", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=2),
        _evidence_atom("atom-contract-full", "Full-time | 170", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=3),
        _evidence_atom("atom-region-china", "China | 160", section="Own workforce", table_id=table_id, atom_type="table_row_atom", row_index=4),
    ]
    answer = _assess(tmp_path, profile, [region, *rows])
    assert answer.status == "found"
    assert len(answer.facts) == 2
    assert {fact.dimensions["gender"][0] for fact in answer.facts} == {"male", "female"}


def test_numeric_parser_preserves_repeated_period_values() -> None:
    assert DisclosureFeatureExtractor.numeric_values(
        "Overseas | 2021 | 2022 | 2023 | 2 | 8 | 4 | 0 | 3 | 3",
        years=[2021, 2022, 2023],
    ) == ["2", "8", "4", "0", "3", "3"]
