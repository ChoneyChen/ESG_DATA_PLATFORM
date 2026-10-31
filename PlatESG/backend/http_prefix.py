"""Stable public /api prefix for development proxy and standalone service."""


class APIPrefixMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope["type"]=="http" and scope.get("path","").startswith("/api/"):
            scope={**scope,"path":scope["path"][4:],"raw_path":scope.get("raw_path",b"")[4:]}
        await self.app(scope,receive,send)
