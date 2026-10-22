"""Resolve the workbench principal without persisting or exposing its credential."""
import os

import requests
from fastapi import HTTPException
from esg_runtime.local_operator import operator_headers


def authorize_operator(authorization, role, *, publication_url=None):
    headers = operator_headers(authorization)
    if headers is None:
        raise HTTPException(401, "工作台未获本机操作员授权")
    base = (publication_url or os.getenv("ESG_PUBLICATION_API_URL", "http://127.0.0.1:8765")).rstrip("/")
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(base + "/internal/v1/me", headers=headers,
                                   timeout=15, allow_redirects=False)
        if response.status_code in {401, 403}:
            raise HTTPException(response.status_code, "工作台权限验证失败，请重新连接")
        if response.status_code != 200:
            raise HTTPException(503, "操作员权限服务暂不可用，已保存的生产计划保留")
        principal = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(503, "操作员权限服务暂不可用，已保存的生产计划保留") from exc
    if (not isinstance(principal, dict) or not isinstance(principal.get("operator_ref"), str)
            or not principal["operator_ref"].strip() or not isinstance(principal.get("roles"), list)
            or any(not isinstance(r, str) for r in principal["roles"])):
        raise HTTPException(503, "操作员权限响应无效，未执行生产操作")
    if role not in principal["roles"] and "admin" not in principal["roles"]:
        raise HTTPException(403, "当前操作员没有此生产操作所需的权限")
    return principal
