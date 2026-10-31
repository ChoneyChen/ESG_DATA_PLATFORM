#!/usr/bin/env python3
"""Install current worker distributions into the configured local model Pythons, offline."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    "contracts": ("ExtractESG/contracts", ["src"]),
    "runtime": ("ExtractESG/shared_runtime", ["src"]),
    "clauses": ("ClauseESG", ["src"]),
    "workbench": ("ExtractESG", ["document/src", "pipeline/src", "extraction/src"]),
}


def main():
    runtimes = {
        "MLX": Path(
            os.getenv(
                "ESG_MODEL_PYTHON",
                str(Path.home() / "Desktop/model/.runtime/venv/bin/python"),
            )
        ),
        "Paddle": Path(
            os.getenv(
                "LOCAL_PADDLEOCR_RUNTIME_PYTHON",
                str(Path.home() / "Desktop/model/.runtime/paddleocr-vl/bin/python"),
            )
        ),
    }
    for name, python in runtimes.items():
        if not python.is_file():
            raise SystemExit("Configured local " + name + " Python is missing")
    with tempfile.TemporaryDirectory(prefix="esg-worker-install-") as folder:
        work = Path(folder)
        projects = []
        # Clean source staging prevents obsolete build/lib modules entering wheels.
        for name, (relative, sources) in MODULES.items():
            project = work / name
            project.mkdir()
            shutil.copy2(ROOT / relative / "pyproject.toml", project / "pyproject.toml")
            for source in sources:
                shutil.copytree(
                    ROOT / relative / source,
                    project / source,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
                )
            projects.append(str(project))
        wheels = work / "wheels"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-index",
                "--no-cache-dir",
                "-w",
                str(wheels),
                *projects,
            ],
            check=True,
        )
        for python in dict.fromkeys(runtimes.values()):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "--python",
                    str(python),
                    "install",
                    "--no-deps",
                    "--no-index",
                    "--no-compile",
                    "--force-reinstall",
                    *[str(path) for path in sorted(wheels.glob("*.whl"))],
                ],
                check=True,
            )


if __name__ == "__main__":
    main()
