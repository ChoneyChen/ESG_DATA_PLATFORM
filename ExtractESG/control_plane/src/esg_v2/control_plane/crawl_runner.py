"""Process-boundary control for the CrawlESG CLI.

The console never imports CrawlESG or edits its index. It starts the documented
CLI as a separate process in a writable runtime directory, then reads its exit
code and log. Downloads land in the same runtime directory the read-only Crawl
adapter consumes. Stopping a job signals only that job's own process group.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_COMMANDS = {"plan", "download", "verify"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawlRunner:
    def __init__(self, *, python: str | Path | None = None, cli: str | Path | None = None,
                 index: str | Path | None = None, work_dir: str | Path | None = None):
        workspace = Path(__file__).resolve().parents[6]
        repo = Path(__file__).resolve().parents[5]
        self.python = Path(python or os.getenv("ESG_CRAWL_PYTHON", workspace / ".local" / "crawl-venv" / "bin" / "python")).expanduser()
        self.cli = Path(cli or os.getenv("ESG_CRAWL_CLI", repo / "CrawlESG" / "src" / "esef_crawler.py")).expanduser()
        self.index = Path(index or os.getenv("ESG_CRAWL_INDEX", repo / "CrawlESG" / "index" / "gb_filings_primary.csv")).expanduser()
        self.work = Path(work_dir or os.getenv("ESG_CRAWL_WORK_DIR", workspace / ".local" / "crawl-work")).expanduser()
        self.root = self.work / ".runner"
        self.packages = self.work / "packages"
        self._processes: dict[str, subprocess.Popen] = {}

    def capabilities(self) -> dict[str, Any]:
        return {"python": str(self.python), "python_available": self.python.is_file(),
                "cli": str(self.cli), "cli_available": self.cli.is_file(),
                "index": str(self.index), "index_available": self.index.is_file(),
                "work_dir": str(self.work), "package_dir": str(self.packages),
                "commands": sorted(_COMMANDS), "mode": "external_process", "read_only_crawl_tree": True}

    def _job_paths(self, job_id: str) -> tuple[Path, Path, Path]:
        return (self.root / f"{job_id}.json", self.root / f"{job_id}.log", self.root / f"{job_id}.exit")

    def _load(self, job_id: str) -> dict[str, Any] | None:
        path, _, _ = self._job_paths(job_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _save(self, job: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path, _, _ = self._job_paths(job["job_id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _refresh(self, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("status") != "running":
            return job
        process = self._processes.get(job["job_id"])
        if process is not None:
            code = process.poll()
            if code is None:
                return job
            self._processes.pop(job["job_id"], None)
        else:
            pid = int(job.get("pid") or 0)
            if self._alive(pid):
                return job
            exit_path = self._job_paths(job["job_id"])[2]
            try:
                code = int(exit_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                code = None
        job.update(status="completed" if code == 0 else "failed" if code is not None else "terminated",
                   returncode=code, finished_at=_now())
        self._save(job)
        return job

    @staticmethod
    def _owned(pid: int) -> bool:
        """Only signal a process whose command still names the CrawlESG CLI."""
        if pid <= 0:
            return False
        try:
            output = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                                    text=True, timeout=2, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "esef_crawler.py" in output

    def jobs(self, limit: int = 12) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        rows = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(job, dict) and job.get("job_id"):
                rows.append(self._refresh(job))
        return rows[:max(1, min(limit, 50))]

    def status(self, job_id: str) -> dict[str, Any] | None:
        job = self._load(job_id)
        if not job:
            return None
        job = self._refresh(job)
        _, log_path, _ = self._job_paths(job_id)
        try:
            log = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        except OSError:
            log = ""
        return {**job, "log_tail": log}

    def start(self, command: str, *, years: list[int] | None = None, limit: int | None = None,
              workers: int = 4, no_esg: bool = False, confirm: bool = False) -> dict[str, Any]:
        command = str(command or "").strip().lower()
        if command not in _COMMANDS:
            raise ValueError("command 必须是 plan、download 或 verify")
        caps = self.capabilities()
        for key in ("python_available", "cli_available", "index_available"):
            if not caps[key]:
                raise ValueError(f"CrawlESG 未就绪：{key}")
        if any(self._refresh(job).get("status") == "running" for job in self.jobs(50)):
            raise ValueError("已有 CrawlESG 任务在运行；先停止或等待结束")
        years = sorted({int(year) for year in (years or [])})
        if len(years) > 20 or any(year < 1900 or year > 2200 for year in years):
            raise ValueError("years 必须是 1900–2200 的年份，最多 20 个")
        if limit is not None and not 1 <= int(limit) <= 2000:
            raise ValueError("limit 必须在 1–2000 之间")
        if not 1 <= int(workers) <= 8:
            raise ValueError("workers 必须在 1–8 之间")
        if command == "download" and not confirm:
            raise ValueError("下载需要显式确认（confirm=true）")
        self.work.mkdir(parents=True, exist_ok=True)
        self.packages.mkdir(parents=True, exist_ok=True)
        argv = [str(self.python), str(self.cli), command, "--index", str(self.index),
                "--out-dir", str(self.work), "--pkg-dir", str(self.packages)]
        if years:
            argv += ["--years", *[str(year) for year in years]]
        if limit is not None:
            argv += ["--limit", str(limit)]
        if command == "download":
            argv += ["--workers", str(workers), "--yes"]
            if no_esg:
                argv += ["--no-esg"]
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.root.mkdir(parents=True, exist_ok=True)
        job_path, log_path, exit_path = self._job_paths(job_id)
        wrapper = ("import json,subprocess,sys; "
                   "result=subprocess.run(json.loads(sys.argv[1])); "
                   "open(sys.argv[2],'w').write(str(result.returncode))")
        process_argv = [str(self.python), "-c", wrapper, json.dumps(argv), str(exit_path)]
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(process_argv, cwd=str(self.work), stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, close_fds=True)
        self._processes[job_id] = process
        job = {"job_id": job_id, "command": command, "status": "running", "pid": process.pid,
               "started_at": _now(), "years": years, "limit": limit, "workers": workers,
               "no_esg": bool(no_esg), "work_dir": str(self.work), "index": str(self.index),
               "log_path": str(log_path)}
        self._save(job)
        return job

    def stop(self, job_id: str) -> dict[str, Any]:
        job = self._load(job_id)
        if not job:
            raise KeyError(job_id)
        job = self._refresh(job)
        if job.get("status") != "running":
            return job
        pid = int(job.get("pid") or 0)
        if not self._owned(pid):
            job.update(status="terminated", finished_at=_now(),
                       note="进程已不在本任务控制下，未发送停止信号")
            self._save(job)
            return job
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(20):
            if not self._alive(pid):
                break
            time.sleep(0.1)
        if self._alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        job.update(status="stopped", finished_at=_now())
        self._save(job)
        return job
