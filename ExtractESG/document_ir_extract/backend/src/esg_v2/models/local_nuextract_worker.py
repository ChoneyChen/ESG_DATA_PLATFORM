from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any


RESULT_PREFIX = "__ESG_NUEXTRACT_RESULT__"


def _message_parts(messages: list[dict[str, Any]]) -> tuple[str, str, list[str]]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    images: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and part.get("text"):
                target = system_parts if role == "system" else user_parts
                target.append(str(part["text"]))
            elif part.get("type") == "image_url":
                image_url = part.get("image_url") or {}
                value = image_url.get("url") if isinstance(image_url, dict) else image_url
                if value:
                    images.append(str(value))
    return "\n".join(system_parts), "\n\n".join(user_parts), list(dict.fromkeys(images))


class NuExtractWorker:
    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self.model = None
        self.processor = None

    def _ensure_loaded(self) -> None:
        if self.model is not None:
            return
        from mlx_vlm import load

        self.model, self.processor = load(str(self.model_path))

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        self._ensure_loaded()
        from mlx_vlm.generate import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        system_text, input_text, images = _message_parts(request.get("messages") or [])
        output_template = request.get("output_template") or {"result": "verbatim-json-value"}
        instructions = "\n".join(
            part
            for part in (
                system_text,
                "Fill the supplied JSON template. Return one JSON object only.",
                "Do not add facts, identifiers, values, or operations absent from the input.",
            )
            if part
        )
        prompt = apply_chat_template(
            self.processor,
            self.model.config,
            input_text,
            num_images=len(images),
            template=json.dumps(output_template, ensure_ascii=False),
            instructions=instructions,
            enable_thinking=False,
        )
        result = generate(
            self.model,
            self.processor,
            prompt=prompt,
            image=images or None,
            max_tokens=int(request.get("max_tokens") or 4096),
            temperature=float(request.get("temperature") or 0.0),
            verbose=False,
        )
        generation_tokens = getattr(result, "generation_tokens", None)
        return {
            "text": result.text,
            "usage": {
                "completion_tokens": generation_tokens,
            } if generation_tokens is not None else {},
        }


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    model_path = Path(args.model).expanduser().resolve()
    if args.probe:
        try:
            import mlx
            import mlx_vlm

            _emit({
                "ok": True,
                "model_path": str(model_path),
                "model_exists": model_path.exists(),
                "mlx_version": getattr(mlx, "__version__", "unknown"),
                "mlx_vlm_version": getattr(mlx_vlm, "__version__", "unknown"),
            })
            return 0
        except Exception as exc:
            _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return 1

    worker = NuExtractWorker(model_path)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = "unknown"
        try:
            envelope = json.loads(line)
            request_id = str(envelope.get("request_id") or "unknown")
            result = worker.generate(envelope.get("request") or {})
            _emit({"ok": True, "request_id": request_id, **result})
        except Exception as exc:
            _emit({
                "ok": False,
                "request_id": request_id,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=8),
            })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
