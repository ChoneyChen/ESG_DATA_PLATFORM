"""Publication's own boot-frozen code identity, independent of extraction data."""
import sys
from pathlib import Path
EXTRACT_ROOT=Path(__file__).resolve().parents[2]/"ExtractESG"
sys.path.insert(0,str(EXTRACT_ROOT/"shared_runtime/src"))
from esg_runtime.release import RuntimeRelease

def release():return RuntimeRelease(EXTRACT_ROOT,"publication-backend")
