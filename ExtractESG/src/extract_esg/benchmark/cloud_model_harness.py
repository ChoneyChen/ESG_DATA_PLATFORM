from __future__ import annotations

from pathlib import Path

from pydantic import Field

from extract_esg.contracts.base import ContractModel
from extract_esg.workflows import ReportProcessingWorkflow


class BenchmarkCaseResult(ContractModel):
    pdf_path: Path
    report_id: str
    page_count: int
    packet_count: int
    quality_flags: list[str] = Field(default_factory=list)


class CloudModelBenchmarkHarness:
    """Benchmark harness for local evidence plus Qiniu model experiments.

    The first implementation measures local evidence readiness. Cloud model
    experiments should be added behind explicit Qiniu credentials and budget.
    """

    def __init__(self, workflow: ReportProcessingWorkflow | None = None) -> None:
        self.workflow = workflow or ReportProcessingWorkflow()

    def prepare_cases(self, pdf_paths: list[str | Path]) -> list[BenchmarkCaseResult]:
        results = []
        for pdf_path in pdf_paths:
            state = self.workflow.prepare_local_evidence(pdf_path)
            assert state.document_ir is not None
            results.append(
                BenchmarkCaseResult(
                    pdf_path=Path(pdf_path),
                    report_id=state.report_id,
                    page_count=len(state.document_ir.pages),
                    packet_count=len(state.packets),
                    quality_flags=state.document_ir.conflict_flags,
                )
            )
        return results

