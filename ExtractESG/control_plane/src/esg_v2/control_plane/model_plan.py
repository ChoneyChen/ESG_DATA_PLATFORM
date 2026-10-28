"""Task-perspective model choices mapped to existing report preparation adapters."""
from hashlib import sha256
import json
from pathlib import Path

from esg_v2.control_plane.report_processing import ProcessingConfig


def _standard_processing_config(request):
    plan = request.model_plan
    roles = request.role_config
    if plan is None:
        return ProcessingConfig(
            ocr_provider="local_first" if roles.cloud_authorization.allow_ocr_api else "local_paddleocr",
            allow_cloud_ocr=roles.cloud_authorization.allow_ocr_api,
            review_provider="qiniu" if roles.semantic_brain.provider == "qiniu_vlm" else "local_qwen",
            allow_limited_evidence=request.allow_limited_evidence)
    return ProcessingConfig(ocr_provider=plan.ocr_provider, ocr_model=plan.ocr_model,
        allow_cloud_ocr=roles.cloud_authorization.allow_ocr_api,
        review_provider=plan.visual_review_provider,
        execute_optional_reviews=plan.execute_visual_reviews,
        execute_required_reviews=plan.execute_required_reviews,
        allow_limited_evidence=request.allow_limited_evidence)


def report_processing_candidates(request):
    """Preparations a request may use, most complete first.

    The foundation-16 route reads tables page by page with its own guards, so it
    needs neither every page nor the IR agent reviews: an existing full IR is
    reused when present, an existing full OCR is built without reviews, and
    otherwise only the candidate pages are recognised.
    """
    standard = _standard_processing_config(request)
    if getattr(request, "extraction_route", "standard") != "foundation16":
        return [standard]
    fast = {"execute_required_reviews": False, "execute_optional_reviews": False, "allow_limited_evidence": True}
    profile = getattr(request, "metric_profile", None) or "universal"
    return [standard, standard.model_copy(update=fast),
            standard.model_copy(update={**fast, "page_selection": "clauses", "clause_profile": profile})]


def report_processing_config(request):
    """The preparation a new run of this request uses when nothing can be reused."""
    return report_processing_candidates(request)[-1]


def _local(path):
    path = Path(path).expanduser()
    config = path / "config.json"
    result = {"model": path.name, "path": str(path), "installed": False,
        "runtime_state": "not_probed", "quality_verified": False}
    try:
        data = config.read_bytes()
        decoded = json.loads(data)
        result.update(installed=any(path.glob("*.safetensors")), config_sha256=sha256(data).hexdigest(),
            model_type=decoded.get("model_type"))
    except (OSError, ValueError, TypeError):
        pass
    return result


def report_model_task_options(settings):
    """Static installed-file inspection only: never load, download or infer."""
    qwen = _local(settings.qwen_model_path)
    nuextract = _local(settings.nuextract_model_path)
    paddle = _local(getattr(settings, "local_paddleocr_model_path", Path.home() / "Desktop/model/PaddleOCR-VL-1.6"))
    qwen_option = {"provider": "local_qwen", "label": "本地 Qwen3.5", "supported": True, **qwen}
    nuextract_option = {"provider": "local_nuextract", "label": "本地 NuExtract3", "supported": True, **nuextract}
    cloud = {"provider": "qiniu_vlm", "label": "用户授权的云视觉模型", "supported": True,
        "requires_cloud_authorization": True, "quality_verified": False}
    ocr_reason = "模型具备视觉能力；本产品尚未实现其 OCR 页面、坐标及布局输出适配器，当前不能作为报告识别引擎。"
    return {"schema_version": "report-model-task-options-v1", "calls_model": False,
        "tasks": {
            "ocr": {"label": "页面识别与布局", "providers": [
                {"provider": "local_paddleocr", "label": "本地 PaddleOCR-VL 1.6", "supported": True, **paddle},
                {"provider": "local_first", "label": "本地优先 PaddleOCR", "supported": True, "requires_fallback_authorization": True},
                {"provider": "paddle_api", "label": "授权的 Paddle OCR API", "supported": True, "requires_ocr_authorization": True},
                {"provider": "local_qwen", "label": "Qwen 页面识别", "supported": False, "disabled_reason": ocr_reason},
                {"provider": "local_nuextract", "label": "NuExtract 页面识别", "supported": False, "disabled_reason": ocr_reason}]},
            "visual_review": {"label": "可选视觉复核", "providers": [qwen_option, nuextract_option, {**cloud, "provider": "qiniu"}]},
            "semantic_brain": {"label": "报告理解、来源规划与指标映射", "providers": [qwen_option, cloud]},
            "specialist": {"label": "定向填表与冲突复核", "providers": [qwen_option, nuextract_option, cloud]}},
        "presets": [
            {"id": "qwen_unified", "label": "Qwen 统一理解与提取", "ocr_provider": "local_paddleocr", "visual_review_provider": "local_qwen",
                "semantic_brain_provider": "local_qwen", "specialist_provider": "local_qwen", "note": "页面识别仍由 PaddleOCR 完成；所选理解、提取和启用后的视觉复核均用 Qwen。"},
            {"id": "mixed", "label": "混合方案", "ocr_provider": "local_paddleocr", "visual_review_provider": "local_qwen",
                "semantic_brain_provider": "local_qwen", "specialist_provider": "local_nuextract"}],
        "execution_policy": "only_selected_adapters;no_silent_provider_fallback"}
