from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any


EVENT_PREFIX = "ESG_OCR_EVENT "
_EMIT_LOCK = threading.Lock()


def _emit(event: dict[str, Any]) -> None:
    with _EMIT_LOCK:
        print(EVENT_PREFIX + json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


class _ProgressHeartbeat:
    """Expose liveness while Paddle is blocked inside one page prediction."""

    def __init__(self, *, total_pages: int, interval_seconds: float) -> None:
        self.total_pages = max(0, total_pages)
        self.interval_seconds = max(5.0, interval_seconds)
        self.started_at = time.monotonic()
        self.last_progress_at = self.started_at
        self.extracted_pages = 0
        self.phase = "initializing"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="local-ocr-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self.phase = phase

    def page_completed(self, count: int) -> tuple[float, float]:
        now = time.monotonic()
        with self._lock:
            page_seconds = now - self.last_progress_at
            self.extracted_pages = count
            self.last_progress_at = now
        return page_seconds, now - self.started_at

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds + 1.0))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            now = time.monotonic()
            with self._lock:
                extracted = self.extracted_pages
                phase = self.phase
                no_progress_seconds = now - self.last_progress_at
            next_page = extracted + 1 if not self.total_pages or extracted < self.total_pages else extracted
            _emit(
                {
                    "state": "heartbeat",
                    "phase": phase,
                    "extractedPages": extracted,
                    "totalPages": self.total_pages,
                    "nextPage": next_page,
                    "noProgressSeconds": round(no_progress_seconds, 1),
                    "elapsedSeconds": round(now - self.started_at, 1),
                    "message": (
                        f"Local OCR alive: page {next_page}/{self.total_pages or '?'} is still processing"
                    ),
                }
            )


def _prune_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _prune_result(item)
            for key, item in value.items()
            if key not in {"input_path", "page_index"}
        }
    if isinstance(value, (list, tuple)):
        return [_prune_result(item) for item in value]
    return value


def _safe_name(value: str, fallback: str) -> str:
    name = Path(value).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return stem or fallback


def _save_image(image: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        if isinstance(image, Image.Image):
            suffix = path.suffix.lower()
            if suffix == ".png":
                image.save(path, format="PNG")
            elif suffix == ".webp":
                image.convert("RGB").save(path, format="WEBP", quality=92)
            else:
                image.convert("RGB").save(path, format="JPEG", quality=92)
            return
    except ImportError:
        pass

    import cv2
    import numpy as np

    array = np.asarray(image)
    if array.ndim not in {2, 3}:
        raise ValueError(f"Unsupported local OCR image shape: {array.shape}")
    if not cv2.imwrite(str(path), array):
        raise RuntimeError(f"Failed to write local OCR image: {path}")


def _resource_ref(relative: Path) -> str:
    return f"local-resource://{relative.as_posix()}"


def _page_payload(item: Any, *, page_index: int, asset_root: Path) -> dict[str, Any]:
    raw_result = item.json["res"]
    markdown_result = item._to_markdown(pretty=True, show_formula_number=False)

    markdown_refs: dict[str, str] = {}
    for sequence, (source_name, image) in enumerate(
        sorted((markdown_result.get("markdown_images") or {}).items()),
        start=1,
    ):
        source_file_name = _safe_name(str(source_name), "image.jpg")
        relative = (
            Path("markdown")
            / f"page-{page_index + 1:04d}"
            / f"image-{sequence:04d}-{source_file_name}"
        )
        if relative.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            relative = relative.with_suffix(".jpg")
        _save_image(image, asset_root / relative)
        markdown_refs[str(source_name)] = _resource_ref(relative)

    output_refs: dict[str, str] = {}
    output_images = item.img
    for key, image in sorted(output_images.items()):
        if key != "layout_det_res" and not key.startswith("layout_det_res_"):
            continue
        relative = Path("layouts") / f"{_safe_name(key, 'layout_det_res')}-{page_index + 1:04d}.jpg"
        _save_image(image, asset_root / relative)
        output_refs[str(key)] = _resource_ref(relative)

    return {
        "prunedResult": _prune_result(raw_result),
        "markdown": {
            "text": str(markdown_result.get("markdown_texts") or ""),
            "images": markdown_refs,
        },
        "outputImages": output_refs,
        "inputImage": None,
    }


def run(config: dict[str, Any]) -> None:
    runtime = config["runtime"]
    from esg_v2.ocr.loopback import loopback_proxy_bypass_environment

    os.environ.update(
        loopback_proxy_bypass_environment(os.environ, runtime["vlm_server_url"])
    )
    os.environ["PADDLE_PDX_CACHE_HOME"] = runtime["pipeline_cache"]
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

    from paddleocr import PaddleOCRVL

    backend = runtime["vlm_backend"]
    pipeline_kwargs: dict[str, Any] = {
        "pipeline_version": runtime["pipeline_version"],
        "use_doc_orientation_classify": bool(config["options"]["useDocOrientationClassify"]),
        "use_doc_unwarping": bool(config["options"]["useDocUnwarping"]),
        "use_chart_recognition": bool(config["options"]["useChartRecognition"]),
        "use_layout_detection": True,
        "device": "cpu",
    }
    if backend == "mlx-vlm-server":
        pipeline_kwargs.update(
            vl_rec_backend=backend,
            vl_rec_server_url=runtime["vlm_server_url"],
            vl_rec_api_model_name=runtime["model_path"],
            vl_rec_max_concurrency=1,
        )
    elif backend == "native":
        pipeline_kwargs.update(
            vl_rec_backend="native",
            vl_rec_model_dir=runtime["model_path"],
        )
    else:
        raise ValueError(f"Unsupported local PaddleOCR-VL backend: {backend}")

    expected_pages = int(config.get("expected_page_count") or 0)
    heartbeat = _ProgressHeartbeat(
        total_pages=expected_pages,
        interval_seconds=float(config.get("heartbeat_seconds") or 15),
    )
    heartbeat.start()
    try:
        _emit(
            {
                "state": "initializing",
                "message": "Loading local PaddleOCR-VL pipeline",
                "extractedPages": 0,
                "totalPages": expected_pages,
            }
        )
        pipeline = PaddleOCRVL(**pipeline_kwargs)
        heartbeat.set_phase("recognition")
        input_path = config["input_path"]
        asset_root = Path(config["asset_root"])
        result_path = Path(config["result_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True)
        page_count = 0
        _emit(
            {
                "state": "running",
                "message": "Local PaddleOCR-VL pipeline is ready",
                "extractedPages": 0,
                "totalPages": expected_pages,
            }
        )
        with result_path.open("w", encoding="utf-8") as handle:
            for page_index, item in enumerate(
                pipeline.predict_iter(
                    input_path,
                    use_doc_orientation_classify=bool(config["options"]["useDocOrientationClassify"]),
                    use_doc_unwarping=bool(config["options"]["useDocUnwarping"]),
                    use_chart_recognition=bool(config["options"]["useChartRecognition"]),
                    use_layout_detection=True,
                )
            ):
                page_payload = _page_payload(item, page_index=page_index, asset_root=asset_root)
                line = {
                    "errorCode": 0,
                    "errorMsg": "Success",
                    "logId": config["run_id"],
                    "result": {
                        "layoutParsingResults": [page_payload],
                        "dataInfo": {"pageIndex": page_index},
                    },
                }
                handle.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                page_count = page_index + 1
                page_seconds, elapsed_seconds = heartbeat.page_completed(page_count)
                _emit(
                    {
                        "state": "running",
                        "extractedPages": page_count,
                        "totalPages": expected_pages,
                        "pageSeconds": round(page_seconds, 1),
                        "elapsedSeconds": round(elapsed_seconds, 1),
                    }
                )
        _emit(
            {
                "state": "done",
                "extractedPages": page_count,
                "totalPages": expected_pages or page_count,
                "resultPath": str(result_path),
            }
        )
    finally:
        heartbeat.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated PaddleOCR-VL local worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    try:
        run(config)
    except Exception as exc:
        _emit({"state": "failed", "error": f"{type(exc).__name__}: {exc}"})
        raise


if __name__ == "__main__":
    main()
