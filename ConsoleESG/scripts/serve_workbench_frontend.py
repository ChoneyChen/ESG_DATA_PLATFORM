"""Static local workbench server with a boot-frozen compatibility endpoint."""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPConnection
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from esg_runtime.release import RuntimeRelease

ROOT = Path(__file__).resolve().parents[2] / "ExtractESG"
release = RuntimeRelease(ROOT, "workbench-frontend")


class WorkbenchHandler(SimpleHTTPRequestHandler):
    routes = {
        "/document": os.getenv("ESG_DOCUMENT_BACKEND_URL", "http://127.0.0.1:18080"),
        "/database": os.getenv("ESG_DATABASE_INGEST_URL", "http://127.0.0.1:18280"),
        "/extraction": os.getenv(
            "ESG_EXTRACTION_BACKEND_URL", "http://127.0.0.1:18180"
        ),
    }

    def proxy(self):
        prefix = next((p for p in self.routes if self.path.startswith(p + "/")), None)
        if not prefix:
            return False
        upstream = urlsplit(self.routes[prefix])
        if (
            upstream.hostname not in {"127.0.0.1", "localhost"}
            or upstream.scheme != "http"
        ):
            self.send_error(503, "Local operator proxy requires a loopback upstream")
            return True
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 512 * 1024 * 1024:
            self.send_error(413, "Upload is too large")
            return True
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower()
            not in {
                "host",
                "connection",
                "transfer-encoding",
                "proxy-authorization",
                "cookie",
            }
        }
        connection = HTTPConnection(upstream.hostname, upstream.port, timeout=90)
        sent = False
        try:
            connection.request(
                self.command,
                self.path[len(prefix) :],
                self.rfile.read(length) if length else None,
                headers,
            )
            response = connection.getresponse()
            self.send_response(response.status)
            sent = True
            for key, value in response.getheaders():
                if key.lower() not in {
                    "connection",
                    "transfer-encoding",
                    "server",
                    "date",
                    "set-cookie",
                }:
                    self.send_header(key, value)
            self.end_headers()
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, TimeoutError):
            if sent:
                self.close_connection = True
            else:
                self.send_error(503, "Upstream service unavailable")
        finally:
            connection.close()
        return True

    def do_POST(self):
        if not self.proxy():
            self.send_error(405)

    do_PATCH = do_POST
    do_DELETE = do_POST
    do_PUT = do_POST

    def do_GET(self):
        if self.proxy():
            return
        if self.path.split("?", 1)[0] == "/api/compatibility":
            payload = release.snapshot()
            payload["readiness"] = {
                "ready": not payload["restart_required"],
                "blockers": ["前端文件已更新，需要重启前端服务"]
                if payload["restart_required"]
                else [],
            }
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not Path(self.path.split("?", 1)[0]).suffix:
            self.path = "/index.html"
        super().do_GET()

    def end_headers(self):
        # This is a development/operator application, not immutable CDN assets.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    ThreadingHTTPServer(
        ("127.0.0.1", 18081),
        partial(WorkbenchHandler, directory=str(ROOT.parent / "ConsoleESG/frontend/dist")),
    ).serve_forever()
