"""Composition-root paths. Discover the checkout without loading clause data."""
from pathlib import Path


def project_root(anchor: Path | None = None) -> Path:
    anchor = (anchor or Path(__file__)).resolve()
    for parent in (anchor.parent, *anchor.parents):
        if (parent / "ExtractESG").is_dir() and (parent / "PlatESG").is_dir():
            return parent
    raise RuntimeError("ESG_DATA_PLATFORM project markers not found; configure a complete checkout")


PROJECT_ROOT = project_root()
EXTRACT_ROOT = PROJECT_ROOT / "ExtractESG"
WORKSPACE_ROOT = PROJECT_ROOT.parent
DOCUMENT_APP_ROOT = EXTRACT_ROOT / "document_ir_extract"
DOCUMENT_API_SOURCE = DOCUMENT_APP_ROOT / "backend/src/esg_v2"

CLAUSES_ROOT = EXTRACT_ROOT / "standard"
