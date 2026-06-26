from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from extract_esg.persistence import SqliteStore


def _json_response(handler: BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _html_response(handler: BaseHTTPRequestHandler, html: str, status: int = 200) -> None:
    data = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ExtractESG Local Console</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #172033; }
    h1 { margin-bottom: 4px; }
    .muted { color: #6b7280; }
    .card { border: 1px solid #d7dde8; border-radius: 12px; padding: 16px; margin: 16px 0; }
    code { background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }
    button { padding: 8px 12px; border-radius: 8px; border: 1px solid #9aa4b2; background: white; cursor: pointer; }
    pre { white-space: pre-wrap; background: #0f172a; color: #e5e7eb; padding: 16px; border-radius: 12px; overflow: auto; }
    a { color: #2563eb; }
  </style>
</head>
<body>
  <h1>ExtractESG Local Console</h1>
  <p class="muted">本地只读控制台。所有能力也都可以通过 CLI/API 直接使用。</p>
  <div class="card">
    <button onclick="loadReports()">刷新报告列表</button>
    <span class="muted">API: <code>/api/reports</code></span>
    <div id="reports"></div>
  </div>
  <div class="card">
    <h2>详情</h2>
    <pre id="detail">点击报告查看详情。</pre>
  </div>
<script>
async function loadReports() {
  const res = await fetch('/api/reports');
  const data = await res.json();
  const root = document.getElementById('reports');
  if (!data.length) { root.innerHTML = '<p class="muted">暂无报告。先运行 run-local。</p>'; return; }
  root.innerHTML = '<ul>' + data.map(r => `<li><a href="#" onclick="loadReport('${r.report_id}')">${r.report_id}</a> - pages=${r.page_count}, packets=${r.packet_count}, disclosures=${r.disclosure_count}</li>`).join('') + '</ul>';
}
async function loadReport(id) {
  const res = await fetch('/api/reports/' + encodeURIComponent(id));
  document.getElementById('detail').textContent = JSON.stringify(await res.json(), null, 2);
}
loadReports();
</script>
</body>
</html>"""


class LocalConsoleHandler(BaseHTTPRequestHandler):
    store: SqliteStore

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            _html_response(self, _page())
            return
        if path == "/api/reports":
            _json_response(self, self.store.list_reports())
            return
        if path.startswith("/api/reports/"):
            report_id = unquote(path.removeprefix("/api/reports/"))
            report = self.store.get_report(report_id)
            if report is None:
                _json_response(self, {"error": "report_not_found", "report_id": report_id}, status=404)
                return
            _json_response(self, report)
            return
        _json_response(self, {"error": "not_found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(*, db_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    LocalConsoleHandler.store = SqliteStore(db_path)
    server = ThreadingHTTPServer((host, port), LocalConsoleHandler)
    print(f"ExtractESG local console: http://{host}:{port}")
    print(f"Database: {Path(db_path)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ExtractESG local console.")
    finally:
        server.server_close()

