"""Local operator boundary. Document operations do not depend on Plat or a token file."""

import os

from fastapi import HTTPException
from esg_runtime.local_operator import operator_headers


def authorize_operator(authorization, role):
    headers = operator_headers(authorization)
    if headers != {} or os.getenv("ESG_LOCAL_OPERATOR","1") != "1":
        raise HTTPException(401, "工作台未获本机操作员授权")
    return {"operator_ref":"local-operator","roles":["read","admin"]}
