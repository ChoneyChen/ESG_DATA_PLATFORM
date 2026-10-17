"""Source layout and stable legacy runtime locations, independent of symlink depth.

Moving editable code never relocates existing PDF, OCR, IR, Bundle or SQLite
data. Applications may override runtime roots with their existing environment
configuration; these defaults retain the original storage contract.
"""
from pathlib import Path


def project_root(anchor: Path | None = None) -> Path:
    anchor = (anchor or Path(__file__)).resolve()
    for parent in (anchor.parent, *anchor.parents):
        if (parent / "ExtractESG/standards/registry").is_dir() and (parent / "PlatESG").is_dir():
            return parent
    raise RuntimeError("ESG_DATA_PLATFORM project markers not found; configure a complete checkout")


PROJECT_ROOT = project_root()
EXTRACT_ROOT = PROJECT_ROOT / "ExtractESG"
WORKSPACE_ROOT = PROJECT_ROOT.parent
DOCUMENT_APP_ROOT = EXTRACT_ROOT / "document_ir_extract"
EXTRACTION_APP_ROOT = EXTRACT_ROOT / "targeted_table_extract"
DOCUMENT_COMPAT_SOURCE = DOCUMENT_APP_ROOT / "backend/src/esg_v2"
TARGETED_COMPAT_SOURCE = EXTRACTION_APP_ROOT / "backend/src"
STANDARDS_ROOT = EXTRACT_ROOT / "standards"
