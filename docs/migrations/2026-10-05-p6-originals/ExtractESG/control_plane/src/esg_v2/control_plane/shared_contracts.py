"""Use the report domain's single contracts from both backend services."""
from pathlib import Path
import sys

_targeted_src = Path(__file__).resolve().parents[5] / "targeted_table_extract" / "backend" / "src"
if str(_targeted_src) not in sys.path:
    sys.path.insert(0, str(_targeted_src))
from esg_targeted.contracts import AvailableAction, EvidenceGap, ExtractionRequest, ReportExtractionRequest, StageOutcome

__all__ = ["AvailableAction", "EvidenceGap", "ExtractionRequest", "ReportExtractionRequest", "StageOutcome"]
