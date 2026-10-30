"""Composition-root paths. Discover the checkout without loading clause data."""

from pathlib import Path
import os


def project_root(anchor: Path | None = None) -> Path:
    configured = os.getenv("ESG_PROJECT_ROOT")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_dir():
            raise RuntimeError("ESG_PROJECT_ROOT does not exist")
        return path
    anchor = (anchor or Path(__file__)).resolve()
    for parent in (anchor.parent, *anchor.parents):
        if (parent / "ExtractESG").is_dir() and (parent / "PlatESG").is_dir():
            return parent
    raise RuntimeError(
        "ESG_DATA_PLATFORM project markers not found; configure a complete checkout"
    )


PROJECT_ROOT = project_root()
EXTRACT_ROOT = PROJECT_ROOT / "ExtractESG"
WORKSPACE_ROOT = PROJECT_ROOT.parent
DOCUMENT_ROOT = EXTRACT_ROOT / "document"
# Keep registered evidence at its existing location. New checkouts use the module data root.
_previous_document_data = EXTRACT_ROOT / "document_ir_extract"
DOCUMENT_DATA_ROOT = (
    _previous_document_data
    if (_previous_document_data / "document_ir_output").is_dir()
    else DOCUMENT_ROOT / ".local"
)
DOCUMENT_API_SOURCE = DOCUMENT_ROOT / "src/esg_document"

CLAUSES_ROOT = EXTRACT_ROOT / "standard"
