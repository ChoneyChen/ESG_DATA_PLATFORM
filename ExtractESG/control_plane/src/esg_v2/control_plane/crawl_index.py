"""Read-only bridge to a locally exported CrawlESG filing index.

The bridge deliberately knows only CSV, not the crawler, its database, or its
lifecycle. A Crawl record is a candidate for an operator to confirm; it is not
a local report, a PDF asset, or a registered platform entity.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
import re
from typing import Any

from esg_runtime.layout import EXTRACT_ROOT


_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    value = str(value).strip() if value is not None else ""
    return value or None


def _integer(row: dict[str, Any], key: str) -> int | None:
    value = _text(row, key)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _number(row: dict[str, Any], key: str) -> float | None:
    value = _text(row, key)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class CrawlIndexReader:
    def __init__(self, root: str | Path | None = None, output_dir: str | Path | None = None):
        self.root = Path(root or os.getenv("ESG_CRAWL_INDEX_DIR", EXTRACT_ROOT.parent / "CrawlESG" / "index")).expanduser()
        self.path = self.root / "gb_filings_primary.csv"
        self.output_dir = Path(output_dir or os.getenv("ESG_CRAWL_OUTPUT_DIR",
            EXTRACT_ROOT.parent.parent / ".local" / "crawl-work")).expanduser()
        self.ledger_path = Path(os.getenv("ESG_CRAWL_LEDGER", self.output_dir / "download_ledger.csv")).expanduser()
        self.package_dir = Path(os.getenv("ESG_CRAWL_PACKAGE_DIR", self.output_dir / "packages")).expanduser()
        self._cache_key: tuple[int, int] | None = None
        self._cache: list[dict[str, Any]] = []
        self._ledger_key: tuple[int, int] | None = None
        self._ledger: list[dict[str, Any]] = []

    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        stat = self.path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._cache_key:
            with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
                self._cache = list(csv.DictReader(stream))
            self._cache_key = key
        return self._cache

    def _ledger_rows(self) -> list[dict[str, Any]]:
        if not self.ledger_path.is_file():
            return []
        stat = self.ledger_path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._ledger_key:
            with self.ledger_path.open("r", encoding="utf-8-sig", newline="") as stream:
                self._ledger = list(csv.DictReader(stream))
            self._ledger_key = key
        return self._ledger

    def _ledger_by_report(self) -> dict[str, dict[str, Any]]:
        return {report_id: row for row in self._ledger_rows()
                if (report_id := _text(row, "report_id"))}

    def _package_state(self, ledger: dict[str, Any] | None) -> dict[str, Any]:
        filename = _text(ledger or {}, "file")
        safe = bool(filename) and Path(filename).name == filename
        exists = safe and (self.package_dir / filename).is_file()
        status = _text(ledger or {}, "status")
        esg_flag = _text(ledger or {}, "esg_flag")
        return {
            "artifact_kind": "ESEF_ZIP",
            "package_filename": filename,
            "package_available": bool(exists and status == "ok"),
            "download_status": ("file_missing" if status == "ok" and not exists else status) or "not_downloaded",
            "package_size_mb": _number(ledger or {}, "size_mb"),
            "esg_flag": None if esg_flag is None else esg_flag not in {"0", "false", "False"},
        }

    def status(self) -> dict[str, Any]:
        ledger = self._ledger_by_report()
        downloaded = sum(1 for row in ledger.values()
                         if _text(row, "status") == "ok" and _text(row, "file")
                         and Path(_text(row, "file")).name == _text(row, "file")
                         and (self.package_dir / _text(row, "file")).is_file())
        return {"available": self.path.is_file(), "source_system": "CrawlESG",
                "record_count": len(self._rows()) if self.path.is_file() else 0,
                "index_path": str(self.path), "download_ledger_available": self.ledger_path.is_file(),
                "ledger_path": str(self.ledger_path), "package_dir": str(self.package_dir),
                "downloaded_count": downloaded, "ledger_count": len(ledger),
                "artifact_kind": "ESEF_ZIP", "pdf_available": False, "mode": "read_only"}

    def reports(self, query: str = "", fiscal_year: int | None = None, limit: int = 50) -> dict[str, Any]:
        needle = query.strip().casefold()
        ledger = self._ledger_by_report()
        rows = []
        for row in self._rows():
            if fiscal_year is not None and _integer(row, "fiscal_year") != fiscal_year:
                continue
            haystack = " ".join(_text(row, key) or "" for key in
                                ("report_id", "fxo_id", "api_id", "lei", "entity_name", "ticker")).casefold()
            if needle and needle not in haystack:
                continue
            package_hash = _text(row, "sha256")
            source_report_id = _text(row, "report_id") or _text(row, "fxo_id") or _text(row, "api_id")
            package = self._package_state(ledger.get(source_report_id or ""))
            rows.append({
                "source_system": "CrawlESG",
                "source_report_id": source_report_id,
                "fxo_id": _text(row, "fxo_id"),
                "api_id": _text(row, "api_id"),
                "lei": _text(row, "lei"),
                "entity_name": _text(row, "entity_name"),
                "country_code": _text(row, "country"),
                "fiscal_year": _integer(row, "fiscal_year"),
                "period_end": _text(row, "period_end"),
                "report_type": _text(row, "report_type"),
                "source_package_sha256": package_hash if package_hash and _SHA256.fullmatch(package_hash) else None,
                "source_url": _text(row, "package_url"),
                "json_url": _text(row, "json_url"),
                "viewer_url": _text(row, "viewer_url"),
                "version_count": _integer(row, "n_versions"),
                **package,
                # These are intentionally not inferred from a filing index.
                "title": None,
                "publisher_entity_ref": None,
                "period_start": None,
                "requires_confirmation": True,
            })
            if len(rows) >= max(1, min(limit, 100)):
                break
        return {**self.status(), "items": rows, "query": query, "fiscal_year": fiscal_year,
                "returned": len(rows), "truncated": len(rows) >= max(1, min(limit, 100))}

    def companies(self, query: str = "", limit: int = 50) -> dict[str, Any]:
        needle = query.strip().casefold()
        grouped: dict[str, dict[str, Any]] = {}
        for row in self._rows():
            name = _text(row, "entity_name")
            lei = _text(row, "lei")
            if not name and not lei:
                continue
            key = lei or name or ""
            if needle and needle not in f"{name or ''} {lei or ''}".casefold():
                continue
            item = grouped.setdefault(key, {"lei": lei, "entity_name": name, "country_code": _text(row, "country"), "filing_count": 0})
            item["filing_count"] += 1
        values = sorted(grouped.values(), key=lambda item: (item["entity_name"] or "", item["lei"] or ""))
        return {**self.status(), "items": values[:max(1, min(limit, 100))], "query": query,
                "company_count": len(values)}
