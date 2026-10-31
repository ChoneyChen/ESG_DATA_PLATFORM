"""Credential-free service port for Console and Plat; no sibling filesystem access."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler


class ProductServiceError(ConnectionError):
    def __init__(self,status,code):
        super().__init__(code)
        self.status,self.code=status,code


class ProductClient:
    def __init__(self,base_url="http://127.0.0.1:18280",*,timeout=10):
        parsed=urlsplit(base_url)
        if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Database product endpoint must be an explicit credential-free service URL")
        self.base_url=base_url.rstrip("/");self.timeout=timeout
        self.opener=build_opener(ProxyHandler({}))

    def call(self,path,*,method="GET",body=None,model=None):
        content=self.download(path,method=method,body=body,max_bytes=16*1024*1024)[0]
        try:
            value=json.loads(content)
            return model.model_validate(value) if model else value
        except (ValueError,TypeError) as exc:
            raise ProductServiceError(502,"invalid_product_response") from exc

    def download(self,path,*,method="GET",body=None,max_bytes=256*1024*1024):
        if not path.startswith("/") or path.startswith("//") or any(char in path for char in "\r\n\x00"):
            raise ValueError("Invalid product endpoint path")
        request=Request(self.base_url+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
            headers={"Content-Type":"application/json"} if body is not None else {},method=method)
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                content=response.read(max_bytes+1)
                if len(content)>max_bytes:raise ProductServiceError(413,"product_response_capacity_exceeded")
                return content,response.headers.get("Content-Type","application/octet-stream")
        except HTTPError as exc:
            try:
                detail=json.loads(exc.read(4096)).get("detail")
                code=detail if isinstance(detail,str) and len(detail)<200 else "product_request_rejected"
            except (ValueError,AttributeError):code="product_request_rejected"
            raise ProductServiceError(exc.code,code) from exc
        except (URLError,TimeoutError,OSError) as exc:
            if isinstance(exc,ProductServiceError):raise
            raise ProductServiceError(503,"product_service_unavailable") from exc
