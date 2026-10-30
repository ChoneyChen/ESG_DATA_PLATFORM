"""Publication owns its release; no extraction source tree or sys.path mutation."""
from pathlib import Path
from esg_runtime.release import ModuleRelease
from esg_database.client import module_release

def release():
    return ModuleRelease(Path(__file__).resolve().parents[1], "publication-backend", (
        "backend/*.py", "backend/*.mjs", "frontend/src/**/*.ts", "frontend/src/**/*.tsx",
        "frontend/src/**/*.css",
    ), dependencies=(("database", module_release()),))
