"""Resolve declared roles to actual adapters without inference or fallback."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model_id: str
    model_path: Path | None

    @property
    def identity(self):
        return self.provider, str(self.model_path) if self.model_path else self.model_id


def model_selection(request, settings, *, role="semantic_brain"):
    roles = getattr(request, "roles", None) or getattr(request, "role_config", None)
    selected = getattr(roles, role) if roles else None
    provider = selected.provider if selected else request.semantic_provider
    model_id = selected.model if selected else request.semantic_model
    if provider == "qiniu_vlm":
        return ModelSelection(provider, model_id or settings.qiniu_model, None)
    if provider not in {"local_qwen", "local_nuextract"}:
        raise ValueError("Unsupported selected model provider")
    configured = Path(settings.qwen_model_path if provider == "local_qwen" else settings.nuextract_model_path).expanduser().resolve()
    if model_id:
        requested = Path(model_id).expanduser()
        requested = requested.resolve() if requested.is_absolute() else (configured.parent / requested).resolve()
        if requested != configured:
            raise ValueError(f"{provider} is bound to configured model {configured.name}; choose its registered name or path, not another provider's weights")
    return ModelSelection(provider, configured.name, configured)


def same_model_backend(left, right):
    """Role budget wrappers may share one runtime without closing each other."""
    if left is None or right is None:
        return False
    return getattr(left, "_delegate", left) is getattr(right, "_delegate", right)


def report_role_assignments(request, settings):
    return {role: {"provider": selected.provider, "model": selected.model_id,
            "configured_model_path": str(selected.model_path) if selected.model_path else None,
            "supports_vision": True, "supports_structured_output": True}
        for role in ("semantic_brain", "specialist")
        for selected in [model_selection(request, settings, role=role)]}


def freeze_report_role_models(request, settings):
    """New task-plan requests pin actual registered names; old inputs stay exact."""
    if request.model_plan is None:
        return request
    choices = {role: getattr(request.role_config, role).model_copy(update={"model": model_selection(request, settings, role=role).model_id})
        for role in ("semantic_brain", "specialist")}
    return request.model_copy(update={"role_config": request.role_config.model_copy(update=choices)})
