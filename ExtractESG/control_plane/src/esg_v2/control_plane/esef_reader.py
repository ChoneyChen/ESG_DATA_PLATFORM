"""ESEF report-package reader.

This reader is deliberately independent of the PDF/OCR/IR chain: it opens an
ESEF ZIP, streams the inline-XBRL report and returns facts plus their contexts,
units and source file identity. It never extracts the ZIP to disk, never calls a
model, and never writes a report identity or candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "esef-reader-v1"
CACHE_VERSION = 3
MAX_TEXT = 100_000
_REPORT_SUFFIXES = (".xhtml", ".html", ".htm")
_XBRLI = "xbrli:"
_XBRLDI = "xbrldi:"
_IX = "ix:"
_ESG_TERMS = re.compile(
    r"esrs|sustainab|climate|emission|ghg|carbon|energy|water|waste|pollution|"
    r"workforce|employee|humanrights|human rights|governance|corruption|biodiversity",
    re.IGNORECASE,
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _tag(name: str) -> str:
    return (name or "").strip().lower()


def _attrs(values: list[tuple[str, str | None]]) -> dict[str, str | None]:
    return {(_tag(key)): value for key, value in values}


def _number(raw: str, *, scale: str | None = None, sign: str | None = None,
            format_name: str | None = None) -> tuple[str | None, str | None]:
    value = _text(raw).strip().replace(" ", " ").replace(" ", "")
    if not value:
        return None, "empty"
    fmt = _text(format_name).lower()
    if "fixed-zero" in fmt:
        return "0", None
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1]
    if "comma-decimal" in fmt or "numcommadecimal" in fmt:
        value = value.replace(".", "").replace(",", ".")
    else:
        value = value.replace(",", "")
    value = value.replace("%", "").replace("−", "-").replace("–", "-").strip()
    if value.startswith("+"):
        value = value[1:]
    if value.endswith("-"):
        value = "-" + value[:-1]
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None, "unparsed_numeric"
    try:
        exponent = int(_text(scale) or "0")
    except ValueError:
        exponent = 0
    if exponent:
        number *= Decimal(10) ** exponent
    if negative or _text(sign).strip() in {"-", "−", "–"}:
        number = -abs(number)
    return format(number.normalize(), "f"), None


def _safe_member(name: str) -> bool:
    path = Path(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _entry_kind(name: str) -> str:
    lower = name.lower()
    if lower.endswith(_REPORT_SUFFIXES):
        return "report"
    if lower.startswith("meta-inf/") or lower.endswith("reportpackage.json"):
        return "metadata"
    if lower.endswith((".xsd", ".xml", ".xbrl", ".xlink", ".json")):
        return "taxonomy"
    return "other"


class _InlineReader(HTMLParser):
    """Streaming iXBRL reader; no DOM is retained."""

    def __init__(self, *, file_name: str, max_facts: int = 50_000):
        super().__init__(convert_charrefs=True)
        self.file_name = file_name
        self.max_facts = max_facts
        self.stack: list[str] = []
        self.contexts: dict[str, dict[str, Any]] = {}
        self.units: dict[str, dict[str, Any]] = {}
        self.facts: list[dict[str, Any]] = []
        self.fact_count = 0
        self.continuations: dict[str, dict[str, Any]] = {}
        self.context: dict[str, Any] | None = None
        self.context_depth = 0
        self.context_field: str | None = None
        self.context_dimension: str | None = None
        self.context_dimension_value: list[str] = []
        self.unit: dict[str, Any] | None = None
        self.unit_depth = 0
        self.unit_measure_parts: list[str] | None = None
        self.fact: dict[str, Any] | None = None
        self.fact_depth = 0
        self.fact_parts: list[str] = []
        self.continuation: dict[str, Any] | None = None
        self.continuation_depth = 0
        self.exclude_depth = 0
        self.order = 0
        self.parse_errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = _tag(tag)
        attributes = _attrs(attrs)
        self.stack.append(tag)
        depth = len(self.stack)
        if tag == f"{_XBRLI}context":
            self.context = {"context_id": _text(attributes.get("id")), "dimensions": {}}
            self.context_depth = depth
            self.context_field = None
        elif tag == f"{_XBRLI}unit":
            self.unit = {"unit_id": _text(attributes.get("id")), "measures": [], "divide": False}
            self.unit_depth = depth
            self.unit_measure_parts = None
        elif tag == f"{_XBRLI}divide" and self.unit is not None:
            self.unit["divide"] = True
        elif self.context is not None:
            if tag == f"{_XBRLI}identifier":
                self.context_field = "identifier"
            elif tag == f"{_XBRLI}instant":
                self.context_field = "instant"
            elif tag == f"{_XBRLI}startdate":
                self.context_field = "period_start"
            elif tag == f"{_XBRLI}enddate":
                self.context_field = "period_end"
            elif tag in {f"{_XBRLDI}explicitmember", f"{_XBRLDI}typedmember"}:
                self.context_dimension = _text(attributes.get("dimension"))
                self.context_dimension_value = []
                self.context_field = "dimension"
        elif self.unit is not None and tag == f"{_XBRLI}measure":
            self.unit_measure_parts = []
        elif tag in {f"{_IX}nonfraction", f"{_IX}nonnumeric"}:
            self.fact = {"tag": tag, "attrs": attributes}
            self.fact_depth = depth
            self.fact_parts = []
        elif tag == f"{_IX}continuation":
            self.continuation = {"id": _text(attributes.get("id")), "continued_at": _text(attributes.get("continuedat"))}
            self.continuation_depth = depth
            self.continuation["parts"] = []
        elif tag == f"{_IX}exclude":
            self.exclude_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = _tag(tag)
        depth = len(self.stack)
        if tag == f"{_IX}exclude":
            self.exclude_depth = max(0, self.exclude_depth - 1)
        if self.fact is not None and depth == self.fact_depth and tag == self.fact["tag"]:
            self._finish_fact()
        elif self.continuation is not None and depth == self.continuation_depth and tag == f"{_IX}continuation":
            text = "".join(self.continuation.pop("parts", [])).strip()
            if self.continuation["id"]:
                self.continuations[self.continuation["id"]] = {**self.continuation, "text": text}
            self.continuation = None
        elif self.context is not None and depth == self.context_depth and tag == f"{_XBRLI}context":
            self.contexts[self.context["context_id"]] = self.context
            self.context = None
            self.context_field = None
        elif self.unit is not None and depth == self.unit_depth and tag == f"{_XBRLI}unit":
            self.units[self.unit["unit_id"]] = self.unit
            self.unit = None
            self.unit_measure_parts = None
        elif self.context is not None and self.context_field and tag in {
            f"{_XBRLI}identifier", f"{_XBRLI}instant", f"{_XBRLI}startdate", f"{_XBRLI}enddate",
        }:
            self.context_field = None
        elif self.context is not None and self.context_field == "dimension" and tag in {
            f"{_XBRLDI}explicitmember", f"{_XBRLDI}typedmember",
        }:
            if self.context_dimension:
                self.context["dimensions"][self.context_dimension] = "".join(self.context_dimension_value).strip()
            self.context_field = None
        elif self.unit is not None and self.unit_measure_parts is not None and tag == f"{_XBRLI}measure":
            self.unit["measures"].append("".join(self.unit_measure_parts).strip())
            self.unit_measure_parts = None
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self.exclude_depth:
            return
        if self.fact is not None:
            if sum(len(part) for part in self.fact_parts) < MAX_TEXT:
                self.fact_parts.append(data)
        elif self.continuation is not None:
            self.continuation["parts"].append(data)
        elif self.context is not None and self.context_field in {"identifier", "instant", "period_start", "period_end"}:
            self.context[self.context_field] = _text(self.context.get(self.context_field)) + data
        elif self.context is not None and self.context_field == "dimension":
            self.context_dimension_value.append(data)
        elif self.unit is not None and self.unit_measure_parts is not None:
            self.unit_measure_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")

    def _finish_fact(self) -> None:
        fact = self.fact
        self.fact = None
        if fact is None:
            return
        attrs = fact["attrs"]
        raw = "".join(self.fact_parts).strip()
        self.fact_parts = []
        self.fact_count += 1
        if len(self.facts) >= self.max_facts:
            return
        self.order += 1
        kind = "numeric" if fact["tag"] == f"{_IX}nonfraction" else "text"
        context_ref = _text(attrs.get("contextref"))
        context = self.contexts.get(context_ref, {})
        unit_ref = _text(attrs.get("unitref"))
        unit = self.units.get(unit_ref, {})
        value_numeric, parse_note = (None, None)
        if kind == "numeric":
            value_numeric, parse_note = _number(raw, scale=attrs.get("scale"), sign=attrs.get("sign"),
                                                 format_name=attrs.get("format"))
        text = raw if kind == "text" else None
        if text and len(text) > MAX_TEXT:
            text = text[:MAX_TEXT]
        self.facts.append({
            "fact_id": _text(attrs.get("id")) or None,
            "concept": _text(attrs.get("name")) or None,
            "kind": kind,
            "value_text": text,
            "value_numeric": value_numeric,
            "raw_text": raw[:1000],
            "parse_note": parse_note,
            "unit_ref": unit_ref or None,
            "unit": unit.get("measures", []),
            "unit_divide": bool(unit.get("divide")),
            "scale": _text(attrs.get("scale")) or None,
            "sign": _text(attrs.get("sign")) or None,
            "decimals": _text(attrs.get("decimals")) or None,
            "format": _text(attrs.get("format")) or None,
            "context_ref": context_ref or None,
            "period_start": context.get("period_start"),
            "period_end": context.get("period_end"),
            "instant": context.get("instant"),
            "dimensions": context.get("dimensions") or {},
            "file": self.file_name,
            "order": self.order,
            "continued_at": _text(attrs.get("continuedat")) or None,
            "esg_related": bool(_ESG_TERMS.search(_text(attrs.get("name")) + " " + raw)),
        })

    def finish(self) -> None:
        for fact in self.facts:
            target = fact.get("continued_at")
            seen = set()
            pieces = [fact.get("value_text") or ""]
            while target and target in self.continuations and target not in seen:
                seen.add(target)
                continuation = self.continuations[target]
                pieces.append(continuation.get("text") or "")
                target = continuation.get("continued_at")
            if len(pieces) > 1 and fact["kind"] == "text":
                fact["value_text"] = "\n".join(piece for piece in pieces if piece)
            fact.pop("continued_at", None)


def _package_metadata(archive: zipfile.ZipFile) -> dict[str, Any] | None:
    candidates = [name for name in archive.namelist() if name.lower().endswith("meta-inf/reportpackage.json")]
    if not candidates:
        return None
    info = archive.getinfo(candidates[0])
    if info.file_size > 1_000_000:
        return {"error": "reportPackage.json too large"}
    try:
        return json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "reportPackage.json is not readable JSON"}


def _summary(facts: list[dict[str, Any]], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    identifiers = sorted({context.get("identifier") for context in contexts.values() if context.get("identifier")})
    durations = sorted({(context.get("period_start"), context.get("period_end"))
                        for context in contexts.values()
                        if context.get("period_start") and context.get("period_end")})
    instants = sorted({context.get("instant") for context in contexts.values() if context.get("instant")})
    current = max(durations, key=lambda pair: (pair[1], pair[0])) if durations else (None, None)
    prefixes = Counter((fact.get("concept") or "").split(":")[0] for fact in facts if fact.get("concept"))
    return {
        "entity_identifiers": identifiers,
        "lei_candidates": [value for value in identifiers if re.fullmatch(r"[A-Z0-9]{20}", value)],
        "period_start": current[0],
        "period_end": current[1] if current[1] else (instants[-1] if instants else None),
        "periods": [{"period_start": start, "period_end": end} for start, end in durations],
        "instants": instants[:40],
        "context_count": len(contexts),
        "fact_count": len(facts),
        "numeric_facts": sum(1 for fact in facts if fact["kind"] == "numeric"),
        "text_facts": sum(1 for fact in facts if fact["kind"] == "text"),
        "esg_related_facts": sum(1 for fact in facts if fact["esg_related"]),
        "concept_prefixes": dict(prefixes.most_common(20)),
    }


def read_package(path: str | Path, *, max_facts: int = 50_000,
                 expected_sha256: str | None = None) -> dict[str, Any]:
    package = Path(path).expanduser()
    if not package.is_file():
        raise FileNotFoundError(str(package))
    if not zipfile.is_zipfile(package):
        raise ValueError("ESEF package is not a readable ZIP")
    digest = hashlib.sha256()
    with package.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if expected_sha256 and expected_sha256.lower() != actual_sha256:
        raise ValueError("ESEF package SHA-256 does not match the Crawl ledger/index")
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        entries = [{"name": info.filename, "size": info.file_size, "kind": _entry_kind(info.filename)}
                   for info in infos if _safe_member(info.filename)]
        reports = [entry["name"] for entry in entries if entry["kind"] == "report"
                   and not entry["name"].lower().endswith("ixbrlviewer.html")]
        contexts: dict[str, dict[str, Any]] = {}
        units: dict[str, dict[str, Any]] = {}
        facts: list[dict[str, Any]] = []
        parse_errors: list[str] = []
        total_facts = 0
        for name in reports:
            reader = _InlineReader(file_name=name, max_facts=max(0, max_facts - len(facts)))
            with archive.open(name) as raw:
                import io
                with io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="") as stream:
                    try:
                        while chunk := stream.read(4 * 1024 * 1024):
                            reader.feed(chunk)
                        reader.close()
                    except Exception as exc:  # keep partial facts; malformed report stays visible
                        parse_errors.append(f"{name}: {type(exc).__name__}: {exc}")
            reader.finish()
            contexts.update(reader.contexts)
            units.update(reader.units)
            facts.extend(reader.facts)
            total_facts += reader.fact_count
            parse_errors.extend(reader.parse_errors)
        metadata = _package_metadata(archive)
        stat = package.stat()
        return {
            "schema_version": SCHEMA_VERSION,
            "package": {
                "file_name": package.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "sha256": actual_sha256,
                "entries": entries,
                "report_files": reports,
                "metadata": metadata,
                "artifact_kind": "ESEF_ZIP",
                "pdf_available": False,
            },
            "identity": _summary(facts, contexts),
            "contexts": list(contexts.values()),
            "units": list(units.values()),
            "facts": facts,
            "parse_errors": parse_errors,
            "limits": {"max_facts": max_facts, "reported_facts": total_facts,
                       "truncated": total_facts > len(facts)},
        }


class EsefPackageReader:
    """Parse once, cache by verified package digest, then serve summaries/facts."""

    def __init__(self, cache_root: str | Path | None = None):
        import os
        default = Path(__file__).resolve().parents[6] / ".local" / "esef-cache"
        # The service runs from the repository; keep cache outside the protected CrawlESG tree.
        self.cache_root = Path(cache_root or os.getenv("ESG_ESEF_CACHE_DIR", default)).expanduser()

    def _cache_path(self, digest: str) -> Path:
        return self.cache_root / digest / "reader.json"

    def read(self, package_path: str | Path, *, expected_sha256: str | None = None,
             max_facts: int = 50_000) -> dict[str, Any]:
        digest = expected_sha256 or ""
        if digest:
            cached = self._cache_path(digest)
            if cached.is_file():
                try:
                    data = json.loads(cached.read_text(encoding="utf-8"))
                    if data.get("schema_version") == SCHEMA_VERSION:
                        return data
                except (OSError, ValueError):
                    pass
        data = read_package(package_path, max_facts=max_facts, expected_sha256=expected_sha256 or None)
        digest = data["package"]["sha256"]
        target = self._cache_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
        return data

    @staticmethod
    def summary(data: dict[str, Any], *, sample: int = 20) -> dict[str, Any]:
        return {
            "schema_version": data.get("schema_version"),
            "package": {key: value for key, value in (data.get("package") or {}).items() if key != "entries"},
            "entries": (data.get("package") or {}).get("entries", []),
            "identity": data.get("identity"),
            "stats": {
                "contexts": len(data.get("contexts") or []),
                "units": len(data.get("units") or []),
                "facts": len(data.get("facts") or []),
                "parse_errors": data.get("parse_errors") or [],
            },
            "sample_facts": (data.get("facts") or [])[:max(0, sample)],
            "limits": data.get("limits"),
        }

    @staticmethod
    def facts(data: dict[str, Any], *, query: str = "", offset: int = 0, limit: int = 100) -> dict[str, Any]:
        needle = query.strip().casefold()
        rows = []
        for fact in data.get("facts") or []:
            haystack = " ".join(str(fact.get(key) or "") for key in
                                ("concept", "value_text", "value_numeric", "context_ref", "file")).casefold()
            if needle and needle not in haystack:
                continue
            rows.append(fact)
        start = max(0, offset)
        size = max(1, min(limit, 200))
        return {"total": len(rows), "offset": start, "limit": size, "items": rows[start:start + size]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read one ESEF ZIP package without extracting it")
    parser.add_argument("--package", required=True)
    parser.add_argument("--out")
    parser.add_argument("--max-facts", type=int, default=50_000)
    args = parser.parse_args(argv)
    data = read_package(args.package, max_facts=args.max_facts)
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(EsefPackageReader.summary(data), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
