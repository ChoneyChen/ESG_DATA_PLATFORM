"""Use the report domain's single contracts from both backend services."""
from pathlib import Path
import sys
from esg_runtime.layout import TARGETED_COMPAT_SOURCE

_targeted_src = TARGETED_COMPAT_SOURCE
if str(_targeted_src) not in sys.path:
    sys.path.insert(0, str(_targeted_src))
from esg_targeted.contracts import AvailableAction, EvidenceGap, ExtractionRequest, ReportExtractionRequest, RoleConfig, StageOutcome

__all__ = ["AvailableAction", "EvidenceGap", "ExtractionRequest", "ReportExtractionRequest", "RoleConfig", "StageOutcome"]
