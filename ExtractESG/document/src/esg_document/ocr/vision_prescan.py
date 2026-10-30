"""Page text for scanned PDFs from the macOS Vision framework (on-device).

Used only to choose which pages of a PDF without a usable text layer go to
visual OCR; the extracted figures still come from the OCR provider. The Swift
helper is compiled once per source version into the user cache directory.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable

SOURCE = Path(__file__).with_name("vision_ocr.swift")
CACHE = Path.home() / "Library" / "Caches" / "ESG_DATA_PLATFORM"
LANGUAGES = "zh-Hans,zh-Hant,en-US"

LogFn = Callable[[str], None]


def available() -> bool:
    return (
        platform.system() == "Darwin"
        and SOURCE.is_file()
        and (shutil.which("swiftc") is not None or _binary().is_file())
    )


def _binary() -> Path:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:12]
    return CACHE / f"vision_ocr-{digest}"


def binary(log: LogFn | None = None) -> Path:
    path = _binary()
    if path.is_file():
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    if log:
        log("Compiling the on-device Vision text reader (first use)")
    temporary = path.with_suffix(".tmp")
    subprocess.run(
        ["swiftc", "-O", "-o", str(temporary), str(SOURCE)],
        check=True,
        capture_output=True,
        timeout=600,
    )
    temporary.replace(path)
    return path


def read_pages(
    pdf_path: str | Path,
    page_indices: list[int] | None = None,
    *,
    scale: float = 1.5,
    jobs: int = 3,
    words: bool = False,
    timeout: float = 3600,
    log: LogFn | None = None,
) -> list[dict]:
    """Vision results per page (lines with pixel boxes), in page order."""
    pages = (
        "all"
        if page_indices is None
        else ",".join(str(index) for index in page_indices)
    )
    command = [
        str(binary(log)),
        str(pdf_path),
        pages,
        str(scale),
        LANGUAGES,
        "--jobs",
        str(jobs),
    ]
    if words:
        command.append("--words")
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=timeout
    )
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def page_text(result: dict) -> str:
    """Lines in reading order: top to bottom, left to right within a row."""
    lines = sorted(
        result.get("lines") or [],
        key=lambda line: (round(line["bbox"][1] / 12), line["bbox"][0]),
    )
    return "\n".join(str(line["text"]) for line in lines)


def page_texts(
    pdf_path: str | Path, page_count: int, log: LogFn | None = None
) -> list[str]:
    texts = [""] * page_count
    for result in read_pages(pdf_path, log=log):
        index = int(result.get("page_index", -1))
        if 0 <= index < page_count and not result.get("error"):
            texts[index] = page_text(result)
    return texts


def region_lines(
    pdf_path: str | Path, layouts: dict[int, dict], log: LogFn | None = None
) -> dict[int, list[dict]]:
    """On-device OCR lines of pages in the pixel space of their layout results."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        widths = {index: document[index].get_size()[0] for index in layouts}
    finally:
        document.close()
    by_scale: dict[float, list[int]] = {}
    for index, layout in layouts.items():
        by_scale.setdefault(
            round(float(layout["width"]) / widths[index], 4), []
        ).append(index)
    lines: dict[int, list[dict]] = {}
    for scale, indices in by_scale.items():
        for result in read_pages(pdf_path, sorted(indices), scale=scale, log=log):
            index = int(result.get("page_index", -1))
            if index in layouts and not result.get("error"):
                factor = float(layouts[index]["width"]) / max(
                    1.0, float(result.get("width") or layouts[index]["width"])
                )
                lines[index] = [
                    {**line, "bbox": [v * factor for v in line["bbox"]]}
                    for line in result.get("lines") or []
                ]
    return lines
