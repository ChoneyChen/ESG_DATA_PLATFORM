"""Keep uncaught service errors machine readable without exposing internal state."""
import logging
from uuid import uuid4

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register_api_errors(app, layer):
    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        identity = "error-" + uuid4().hex
        logger.error("Unhandled service error %s %s %s [%s]", layer, request.method,
                     request.url.path, identity, exc_info=(type(exc), exc, exc.__traceback__))
        message = ("服务读取失败，未取得最新状态，请按诊断编号核查。"
                   if request.method in {"GET", "HEAD"} else
                   "服务处理失败，请先核对本次操作结果，勿重复提交。")
        return JSONResponse(status_code=500, content={"detail": {
            "code": "internal_service_error", "message": message,
            "request_id": identity, "layer": layer}}, headers={"X-Request-ID": identity})
