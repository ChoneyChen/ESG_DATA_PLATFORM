"""The publication service shares the same boot-frozen workspace release."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

EXTRACT_ROOT=Path(__file__).resolve().parents[2]/"ExtractESG"
spec=spec_from_file_location("platesg_workspace_release",EXTRACT_ROOT/"scripts/workbench_release.py")
module=module_from_spec(spec)
spec.loader.exec_module(module)


def release():
    return module.RuntimeRelease(EXTRACT_ROOT,"publication-backend")
