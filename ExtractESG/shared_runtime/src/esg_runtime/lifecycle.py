"""Reference checks shared by report, extraction and artifact lifecycles.

Archiving is a reversible visibility change. Moving or purging source artifacts
requires an authoritative publication check; connectivity failure is not proof
that a source is unreferenced. Reservations serialize cleanup with candidate
import in the publication database.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LifecycleBlocked(ValueError):
    def __init__(self, blockers, *, uncertain=False):
        self.blockers = blockers
        self.uncertain = uncertain
        super().__init__("；".join(item["message"] for item in blockers))


def require_reason(reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise ValueError("生命周期操作需要填写有效原因（1—4000字）。")
    return reason.strip()


def blocker(code, message, **detail):
    return {"code": code, "message": message, **detail}


class PublicationReferenceGuard:
    def __init__(self, url=None, token=None, *, timeout=4, transport=None):
        self.url = (url or os.getenv("PLATESG_LIFECYCLE_URL", "http://127.0.0.1:8765")).rstrip("/")
        self.token = token or os.getenv("PLATESG_LIFECYCLE_TOKEN") or os.getenv("PLATESG_REVIEW_TOKEN")
        self.timeout = timeout
        self.transport = transport

    def _call(self, operation, payload):
        if self.transport:
            return self.transport(operation, payload)
        token=self.token
        if not token:
            token_file=Path(os.getenv("PLATESG_LIFECYCLE_TOKEN_FILE",Path(__file__).resolve().parents[4]/"PlatESG/.local/review-token"))
            if token_file.is_file() and not token_file.is_symlink():
                if token_file.stat().st_mode & 0o077:
                    raise ValueError("本地来源保护凭据文件权限应为0600。")
                token=token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError("未配置来源引用检查凭据。")
        request = Request(self.url + "/internal/v1/lifecycle/" + operation,
            data=json.dumps(payload).encode(), method="POST", headers={
                "Content-Type": "application/json", "Authorization": "Bearer " + token})
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def check(self, scope):
        try:
            result = self._call("references", scope)
            if result.get("schema_version") != "lifecycle-references-v1" or not isinstance(result.get("blockers"),list):
                raise ValueError("引用检查合同不可用。")
            if result.get("verified") is not True and not result["blockers"]:
                raise ValueError("引用检查未确认。")
            return result
        except (HTTPError, URLError, OSError, ValueError, KeyError) as exc:
            return {"schema_version": "lifecycle-references-v1", "verified": False,
                "dependencies": [], "blockers": [blocker("reference_check_unavailable",
                    "无法确认候选及正式数据的来源依赖；可归档记录，暂不能清理来源。", detail=str(exc))]}

    def reserve(self, scope, reason, *, reservation_id=None, local_files_only=False):
        try:
            payload = {**scope, "reason": require_reason(reason)}
            if reservation_id:
                payload["reservation_id"] = reservation_id
            if local_files_only:
                payload["local_files_only"] = True
            result = self._call("reserve", payload)
        except (HTTPError, URLError, OSError) as exc:
            # The server may have committed before its response was lost. The
            # caller journals this stable ID and releases it through recovery.
            raise LifecycleBlocked([blocker("reference_check_unavailable", "来源保护响应未确认；文件未移动，请恢复回收记录以释放可能的保留权限。", detail=str(exc))], uncertain=True) from exc
        except ValueError as exc:
            raise LifecycleBlocked([blocker("reference_check_unavailable", "来源保护服务不可用，清理未执行。", detail=str(exc))]) from exc
        if not result.get("verified") or result.get("blockers") or not result.get("reservation_id"):
            raise LifecycleBlocked(result.get("blockers") or [blocker("reference_check_unavailable", "无法安全保留来源清理权限。")])
        return result["reservation_id"]

    def update_reservation(self, reservation_id, state, reason):
        return self._call("reservations/" + reservation_id, {"state": state, "reason": require_reason(reason)})
