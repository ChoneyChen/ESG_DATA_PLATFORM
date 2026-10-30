from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from esg_document.config import Settings
from esg_document.models.contracts import ModelRequest, ModelResult
from esg_document.models.local_nuextract_adapter import (
    LocalNuExtractAdapter,
    LocalQwenAdapter,
)
from esg_document.models.local_nuextract_registry import LocalNuExtractRegistry
from esg_document.models.local_qwen_registry import LocalQwenRegistry
from esg_document.models.model_registry import ModelProfile, QiniuModelRegistry
from esg_document.models.provider_rate_limit import ProviderRateLimitCoordinator
from esg_document.models.qiniu_adapter import QiniuModelAdapter
from esg_document.models.vision_input import (
    LocalVisionInputResolver,
    QiniuVisionInputResolver,
)


ReviewProviderName = Literal["qiniu", "local_nuextract", "local_qwen"]


class ReviewModelAdapter(Protocol):
    provider_name: str

    def chat_completions(self, request: ModelRequest) -> ModelResult: ...


class ReviewModelRegistry(Protocol):
    verification_policy: str

    def refresh(self) -> dict[str, object]: ...

    def candidates(
        self,
        role: str,
        *,
        exclude_family: str | None = None,
        limit: int = 3,
    ) -> list[ModelProfile]: ...

    def catalog_snapshot(self) -> dict[str, object]: ...


class VisionInputResolver(Protocol):
    def resolve(self, refs: list[str], *, limit: int = 3) -> list[str]: ...


@dataclass(frozen=True)
class ReviewProviderBundle:
    name: ReviewProviderName
    adapter: ReviewModelAdapter
    registry: ReviewModelRegistry
    input_resolver: VisionInputResolver
    rate_limits: ProviderRateLimitCoordinator | None
    verification_policy: str
    independent_model_family: bool


def create_review_provider(
    settings: Settings,
    provider: ReviewProviderName,
    *,
    api_key: str | None = None,
) -> ReviewProviderBundle:
    if provider in {"local_nuextract", "local_qwen"}:
        adapter = (
            LocalQwenAdapter(settings)
            if provider == "local_qwen"
            else LocalNuExtractAdapter(settings)
        )
        registry = (
            LocalQwenRegistry(settings, adapter)
            if provider == "local_qwen"
            else LocalNuExtractRegistry(settings, adapter)
        )
        return ReviewProviderBundle(
            name=provider,
            adapter=adapter,
            registry=registry,
            input_resolver=LocalVisionInputResolver(),
            rate_limits=None,
            verification_policy=registry.verification_policy,
            independent_model_family=False,
        )
    if provider == "qiniu":
        adapter = QiniuModelAdapter(settings, api_key=api_key)
        registry = QiniuModelRegistry(settings, adapter)
        registry.verification_policy = "different_model_family"
        return ReviewProviderBundle(
            name=provider,
            adapter=adapter,
            registry=registry,
            input_resolver=QiniuVisionInputResolver(),
            rate_limits=ProviderRateLimitCoordinator(
                settings, adapter.credential_scope_id
            ),
            verification_policy="different_model_family",
            independent_model_family=True,
        )
    raise ValueError(f"Unsupported Document IR review provider: {provider}")


def provider_status(
    settings: Settings,
    provider: ReviewProviderName | None = None,
) -> dict[str, Any]:
    providers: dict[str, Any] = {}
    if provider in {None, "qiniu"}:
        qiniu_adapter = QiniuModelAdapter(settings)
        qiniu_limits = ProviderRateLimitCoordinator(
            settings, qiniu_adapter.credential_scope_id
        )
        try:
            qiniu = QiniuModelRegistry(settings, qiniu_adapter).refresh()
        except Exception as exc:
            qiniu = {
                "catalog_error": str(exc),
                "approved_vision_models": [],
                "health": [],
            }
        qiniu.update(
            {
                "provider": "qiniu",
                "verification_policy": "different_model_family",
                "independent_model_family": True,
                "provider_rate_limit": qiniu_limits.snapshot(),
            }
        )
        providers["qiniu"] = qiniu

    if provider in {None, "local_nuextract"}:
        local_adapter = LocalNuExtractAdapter(settings)
        providers["local_nuextract"] = LocalNuExtractRegistry(
            settings, local_adapter
        ).refresh()
    if provider in {None, "local_qwen"}:
        qwen_adapter = LocalQwenAdapter(settings)
        providers["local_qwen"] = LocalQwenRegistry(settings, qwen_adapter).refresh()
    return {
        "default_provider": settings.default_review_provider,
        "providers": providers,
    }
