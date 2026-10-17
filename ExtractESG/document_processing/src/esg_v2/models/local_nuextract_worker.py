from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
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


def _check_explicit_schema(schema: dict[str, Any]) -> None:
    """Validate the delivered schema structure/references without MLX runtime dependencies.

    This is not token-mask enforcement or instance validation. The service's
    operation codecs validate every returned value before mutation.
    """
    json.dumps(schema, allow_nan=False)
    def check(node, depth=0):
        if depth > 64:
            raise ValueError("IR output schema exceeds nesting budget")
        if isinstance(node, bool): return
        if not isinstance(node, dict): raise ValueError("JSON Schema node must be an object or boolean")
        if "$ref" in node:
            ref=node["$ref"]
            if not isinstance(ref,str) or not ref.startswith("#/"):
                raise ValueError("IR output schema permits local references only")
            value=schema
            for key in ref[2:].split("/"):
                key=key.replace("~1","/").replace("~0","~")
                if not isinstance(value,dict) or key not in value:raise ValueError(f"Unresolved IR schema reference {ref}")
                value=value[key]
        if "type" in node:
            types=node['type'] if isinstance(node['type'],list) else [node['type']]
            if not types or any(t not in {"object","array","string","number","integer","boolean","null"} for t in types):
                raise ValueError("Unsupported JSON Schema type")
        for key in ["properties","$defs","patternProperties"]:
            if key in node:
                if not isinstance(node[key],dict):raise ValueError(f"Schema {key} must be an object")
                for child in node[key].values():check(child,depth+1)
        for key in ["oneOf","anyOf","allOf"]:
            if key in node:
                if not isinstance(node[key],list) or not node[key]:raise ValueError(f"Schema {key} must be a nonempty list")
                for child in node[key]:check(child,depth+1)
        for key in ["items","additionalProperties","not"]:
            if key in node:check(node[key],depth+1)
        if "required" in node and (not isinstance(node['required'],list) or any(not isinstance(k,str) for k in node['required'])):
            raise ValueError("Schema required must contain property names")
    check(schema)


def _generic_schema(template: Any) -> dict[str, Any]:
    """Translate the IR runner's typed shape for a general chat VLM.

    Dynamic AtomicPatch values are genuine JSON, not the invented NuExtract
    field type `verbatim-json-value`. Keep them unconstrained at this prompt
    boundary; operation-specific validation remains in the existing adapter.
    """
    if isinstance(template, dict) and "$schema" in template:
        if template.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or template.get("type") != "object" or not isinstance(template.get("properties"), dict):
            raise ValueError("Explicit IR output schema must be a draft2020-12 object schema with properties")
        from copy import deepcopy
        _check_explicit_schema(template)
        return deepcopy(template)
    if isinstance(template, dict):
        return {"type": "object", "properties": {k: _generic_schema(v) for k, v in template.items()},
                "required": list(template), "additionalProperties": False}
    if isinstance(template, list):
        if template and all(isinstance(v, str) for v in template) and template != ["verbatim-string"]:
            return {"type": "string", "enum": template}
        return {"type": "array", "items": _generic_schema(template[0]) if template else {"type": "string"}}
    if template == "verbatim-json-value":
        return {}
    if template in {"number", "integer", "boolean"}:
        return {"type": template}
    return {"type": ["string", "null"]}


class NuExtractWorker:
    def __init__(self, model_path: Path, provider: str = "local_nuextract") -> None:
        self.model_path = model_path
        self.model = None
        self.processor = None
        self.provider = provider

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
        explicit_schema = isinstance(output_template, dict) and "$schema" in output_template
        dynamic_patch = "verbatim-json-value" in json.dumps(output_template)
        if self.provider == "local_qwen" or dynamic_patch or explicit_schema:
            # A general chat model needs a JSON Schema, not NuExtract field-type
            # placeholders. NuExtract complex-patch requests also use content
            # mode because its native schema cannot describe arbitrary JSON.
            schema = _generic_schema(output_template)
            prompt = apply_chat_template(
                self.processor, self.model.config,
                instructions + "\nOUTPUT_SCHEMA=" + json.dumps(schema, ensure_ascii=False)
                + "\nDOCUMENT_CONTEXT=" + input_text,
                num_images=len(images), enable_thinking=False,
            )
        else:
            prompt = apply_chat_template(
                self.processor, self.model.config, input_text, num_images=len(images),
                template=json.dumps(output_template, ensure_ascii=False),
                instructions=instructions, enable_thinking=False,
            )
        output_budget = int(request.get("max_tokens") or 4096)
        result = generate(
            self.model,
            self.processor,
            prompt=prompt,
            image=images or None,
            max_tokens=output_budget,
            temperature=float(request.get("temperature") or 0.0),
            verbose=False,
        )
        generation_tokens = getattr(result, "generation_tokens", None)
        return {
            "text": result.text,
            "schema_delivery": "prompt_schema_and_server_validation" if explicit_schema or self.provider == "local_qwen" or dynamic_patch else "native_template",
            "token_level_schema_enforcement": False,
            "finish_reason": getattr(result, "finish_reason", None) or (
                "length" if generation_tokens is not None and generation_tokens >= output_budget else "stop"
            ),
            "usage": {
                "completion_tokens": generation_tokens,
            } if generation_tokens is not None else {},
        }


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}\n")
    sys.stdout.flush()


def _monitor_parent(owner_pid: int, *, interval: float = 2.0) -> None:
    """Do not retain local weights if the owning service has disappeared.

    EOF is not read while generation is inside native MLX. This separate thread
    covers owner loss during an active request; ordinary request timeouts remain
    enforced by the parent adapter, which terminates the worker process.
    """
    while True:
        if os.getppid() != owner_pid:
            os._exit(1)
            return
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--provider", choices=("local_nuextract", "local_qwen"), default="local_nuextract")
    parser.add_argument("--owner-pid", type=int)
    args = parser.parse_args()
    model_path = Path(args.model).expanduser().resolve()
    if args.probe:
        try:
            import importlib.metadata
            import mlx
            import mlx_vlm

            config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
            model_type = str(config.get("model_type") or "").lower()
            if not model_type:
                raise ValueError("Model config has no model_type")
            if not any(model_path.glob("*.safetensors")):
                raise ValueError("Model weights are missing")
            distribution = importlib.metadata.distribution("mlx-vlm")
            module = distribution.locate_file(f"mlx_vlm/models/{model_type}")
            if not (module / "__init__.py").is_file() and not module.with_suffix(".py").is_file():
                raise ValueError(f"Installed mlx-vlm does not support model_type={model_type}")

            _emit({
                "ok": True,
                "model_path": str(model_path),
                "model_exists": model_path.exists(),
                "mlx_version": getattr(mlx, "__version__", "unknown"),
                "mlx_vlm_version": distribution.version,
                "model_type": model_type,
            })
            return 0
        except Exception as exc:
            _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return 1

    threading.Thread(target=_monitor_parent, args=(args.owner_pid or os.getppid(),), daemon=True).start()
    worker = NuExtractWorker(model_path, args.provider)
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
