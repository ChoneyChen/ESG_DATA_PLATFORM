import sys
import time

import pytest

from esg_v2.control_plane.crawl_runner import CrawlRunner


def runner(tmp_path):
    cli = tmp_path / "fake_crawler.py"
    cli.write_text("import sys; print('ARGS ' + ' '.join(sys.argv[1:]))", encoding="utf-8")
    index = tmp_path / "index.csv"
    index.write_text("report_id\n", encoding="utf-8")
    work = tmp_path / "work"
    return CrawlRunner(python=sys.executable, cli=cli, index=index, work_dir=work), work


def wait(runner, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = runner.status(job_id)
        if job and job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_runner_invokes_the_cli_as_a_separate_process_and_records_output(tmp_path):
    crawl, work = runner(tmp_path)
    caps = crawl.capabilities()
    assert caps["mode"] == "external_process" and caps["read_only_crawl_tree"] is True
    job = crawl.start("plan", years=[2024], limit=2)
    finished = wait(crawl, job["job_id"])
    assert finished["status"] == "completed" and finished["returncode"] == 0
    assert "--years 2024" in finished["log_tail"]
    assert "--limit 2" in finished["log_tail"]
    assert (work / "packages").is_dir()


def test_download_requires_explicit_confirmation(tmp_path):
    crawl, _ = runner(tmp_path)
    with pytest.raises(ValueError, match="确认"):
        crawl.start("download", limit=1)
    job = crawl.start("download", limit=1, confirm=True)
    finished = wait(crawl, job["job_id"])
    assert finished["status"] == "completed"
    assert "--yes" in finished["log_tail"]


def test_runner_rejects_unknown_commands_and_invalid_ranges(tmp_path):
    crawl, _ = runner(tmp_path)
    for kwargs in ({"command": "index"}, {"command": "plan", "years": [1800]},
                   {"command": "plan", "limit": 0}, {"command": "plan", "workers": 12}):
        try:
            crawl.start(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"accepted invalid runner request: {kwargs}")
