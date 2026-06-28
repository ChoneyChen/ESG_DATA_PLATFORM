from __future__ import annotations

import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from extract_esg.ai import QiniuModelAdapter, QiniuModelRegistry, api_model_assessment_payload
from extract_esg.config import Settings
from extract_esg.persistence import SqliteStore
from extract_esg.workflows import ReportProcessingWorkflow


def _json_response(handler: BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _html_response(handler: BaseHTTPRequestHandler, html: str, status: int = 200) -> None:
    data = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def _safe_filename(name: str) -> str:
    stem = Path(name or "uploaded.pdf").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return cleaned or "uploaded.pdf"


def _selected_model(selected_models: dict[str, str], key: str) -> str | None:
    value = selected_models.get(key)
    return value if value else None


def _pipeline_steps(
    selected_models: dict[str, str],
    *,
    completed: bool = False,
    api_completed: bool = False,
) -> list[dict[str, object]]:
    status = "completed" if completed else "pending"
    api_status = "completed" if api_completed else "planned_not_run" if completed else "planned"
    return [
        {
            "key": "local_pdf",
            "label": "Local PDF inspection",
            "mode": "local",
            "status": status,
            "model": None,
            "description": "Extract native text, page metadata, hashes, and local quality flags.",
        },
        {
            "key": "evidence_packets",
            "label": "Evidence packet build",
            "mode": "local",
            "status": status,
            "model": None,
            "description": "Convert pages and local text into replayable evidence packets.",
        },
        {
            "key": "page_classification",
            "label": "Page / section classification",
            "mode": "api",
            "status": api_status,
            "model": _selected_model(selected_models, "page_classification"),
            "description": "Future cloud call: classify ESG topic, table pages, appendix pages, and standard references.",
        },
        {
            "key": "vision_ocr",
            "label": "Scanned-page OCR",
            "mode": "api",
            "status": api_status,
            "model": _selected_model(selected_models, "vision_ocr"),
            "description": "Future cloud call: recover visual text and reading order when local text is weak.",
        },
        {
            "key": "table_vision_extract",
            "label": "Image/table extraction",
            "mode": "api",
            "status": api_status,
            "model": _selected_model(selected_models, "table_vision_extract"),
            "description": "Future cloud call: reconstruct table cells, headers, units, and footnotes.",
        },
        {
            "key": "structured_extract",
            "label": "Full-harvest extraction",
            "mode": "api",
            "status": api_status,
            "model": _selected_model(selected_models, "structured_extract"),
            "description": "Future cloud call: emit quantitative and qualitative disclosure candidates from packets.",
        },
        {
            "key": "qualitative_summarization",
            "label": "Qualitative compression",
            "mode": "api",
            "status": api_status,
            "model": _selected_model(selected_models, "qualitative_summarization"),
            "description": "Future cloud call: summarize policies, targets, controls, and governance claims with evidence ids.",
        },
        {
            "key": "mapping_review",
            "label": "Standard mapping review",
            "mode": "api",
            "status": api_status,
            "model": _selected_model(selected_models, "mapping_review"),
            "description": "Future cloud call: map candidates to HKEX, ESRS, GRI, ISSB, and internal concepts.",
        },
        {
            "key": "verification",
            "label": "Independent verification",
            "mode": "api",
            "status": api_status,
            "model": _selected_model(selected_models, "verification"),
            "description": "Future cloud call: evidence-only audit before publication.",
        },
        {
            "key": "sqlite_save",
            "label": "SQLite persistence",
            "mode": "local",
            "status": status,
            "model": None,
            "description": "Save document, packets, and local candidate disclosures to the local database.",
        },
    ]


def _page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ExtractESG Local Console</title>
  <style>
    :root { color-scheme: light; --bg: #f6f8fb; --card: #ffffff; --line: #dbe2ee; --ink: #172033; --muted: #687386; --blue: #2563eb; --green: #12805c; --amber: #a16207; --slate: #475569; }
    body { margin: 0; background: var(--bg); color: var(--ink); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    header { padding: 28px 36px 18px; background: linear-gradient(135deg, #0f172a, #1d4ed8); color: white; }
    h1 { margin: 0 0 8px; font-size: 28px; }
    h2 { margin: 0 0 12px; font-size: 18px; }
    p { line-height: 1.55; }
    main { padding: 24px 36px 40px; display: grid; grid-template-columns: minmax(340px, 460px) minmax(520px, 1fr); gap: 18px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 18px; box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05); }
    .stack { display: grid; gap: 14px; }
    .muted { color: var(--muted); }
    .tiny { font-size: 12px; }
    label { display: block; font-weight: 650; margin: 12px 0 6px; }
    input, select, button, textarea { font: inherit; }
    input[type="text"], input[type="password"], input[type="number"], select { width: 100%; box-sizing: border-box; border: 1px solid #c8d1df; border-radius: 10px; padding: 9px 10px; background: white; }
    input[type="file"] { width: 100%; }
    button { border: 1px solid #1d4ed8; background: #1d4ed8; color: white; border-radius: 10px; padding: 10px 14px; cursor: pointer; font-weight: 650; }
    button.secondary { background: white; color: #1d4ed8; }
    button:disabled { opacity: .6; cursor: wait; }
    code { background: #eef2f7; padding: 2px 6px; border-radius: 6px; }
    pre { white-space: pre-wrap; background: #0f172a; color: #e5e7eb; padding: 14px; border-radius: 12px; overflow: auto; max-height: 520px; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .step { border: 1px solid var(--line); border-radius: 12px; padding: 12px; margin: 8px 0; }
    .step-head { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
    .badge { display: inline-block; border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 700; }
    .local { background: #e0f2fe; color: #075985; }
    .api { background: #fef3c7; color: #92400e; }
    .completed { background: #dcfce7; color: #166534; }
    .planned, .planned_not_run, .pending { background: #e2e8f0; color: #334155; }
    .error { background: #fee2e2; color: #991b1b; }
    .report-link { color: var(--blue); cursor: pointer; text-decoration: underline; }
    .model-row { border-top: 1px solid #edf1f7; padding-top: 10px; }
    @media (max-width: 980px) { main { grid-template-columns: 1fr; padding: 18px; } header { padding: 22px 18px; } }
  </style>
</head>
<body>
  <header>
    <h1>ExtractESG Local Console</h1>
    <p><strong>真实云抽取版</strong>：已接入七牛 /chat/completions，可运行小样本真实云模型抽取并入库。</p>
    <p>本地演示控制台：选择 PDF、选择每个 API 步骤的模型、查看流程状态，并展示 SQLite 入库结果。CLI/headless 能力仍然完整保留。</p>
  </header>
  <main>
    <section class="stack">
      <div class="card">
        <h2>1. 选择 PDF</h2>
        <label for="pdfPath">本机路径模式</label>
        <input id="pdfPath" type="text" placeholder="/Users/.../report.pdf" />
        <p class="muted tiny">适合大 PDF。浏览器不会自动暴露文件绝对路径，所以也保留下面的上传模式。</p>
        <label for="pdfFile">上传模式</label>
        <input id="pdfFile" type="file" accept="application/pdf,.pdf" />
        <label for="reportId">Report ID</label>
        <input id="reportId" type="text" placeholder="可留空，系统自动生成" />
      </div>
      <div class="card">
        <h2>2. 七牛 API Key</h2>
        <label for="apiKey">QINIU_API_KEY</label>
        <input id="apiKey" type="password" autocomplete="off" placeholder="在这里粘贴你的七牛 API Key" />
        <p class="muted tiny">只保存在当前浏览器会话里，不写入 .env、不写入数据库、不在响应里回显。</p>
        <button class="secondary" id="refreshModelsBtn" onclick="refreshQiniuModels()">刷新七牛模型列表</button>
        <p id="apiKeyStatus" class="muted tiny">尚未刷新模型。</p>
      </div>
      <div class="card">
        <h2>3. 模型选择</h2>
        <p class="muted tiny">默认值来自当前能力评估。这里先用于流程规划展示；真实云调用接入后会写入 cloud task 审计记录。</p>
        <div id="modelSelectors">加载中...</div>
      </div>
      <div class="card">
        <button id="runBtn" onclick="runLocal()">运行本地链路并入库</button>
        <button id="runCloudBtn" onclick="runCloud()">运行真实云抽取</button>
        <button class="secondary" onclick="loadReports()">刷新数据库展示</button>
        <div class="grid2">
          <div>
            <label for="startPacket">云抽取起始 packet</label>
            <input id="startPacket" type="number" min="0" value="4" />
          </div>
          <div>
            <label for="maxPackets">最多发送 packets</label>
            <input id="maxPackets" type="number" min="1" max="10" value="2" />
          </div>
        </div>
        <p class="muted tiny">本地链路不会调用付费云模型；真实云抽取默认从 packet 4 开始、最多发送 2 个 evidence packets，用于低成本验证端到端链路。</p>
      </div>
    </section>
    <section class="stack">
      <div class="card">
        <h2>4. 流程状态</h2>
        <div id="steps"></div>
      </div>
      <div class="card">
        <h2>5. 数据库报告列表</h2>
        <div id="reports"></div>
      </div>
      <div class="card">
        <h2>6. 结果详情</h2>
        <pre id="detail">等待运行或点击报告查看详情。</pre>
      </div>
    </section>
  </main>
<script>
let assessments = [];
let cachedModels = [];

function getApiKey() {
  return document.getElementById('apiKey').value.trim();
}

function setApiStatus(message, isError = false) {
  const node = document.getElementById('apiKeyStatus');
  node.textContent = message;
  node.style.color = isError ? '#991b1b' : '';
}

function uniq(values) {
  return [...new Set(values.filter(Boolean))];
}

function optionNames(item) {
  const recommended = [item.default_model.name].concat((item.alternatives || []).map(x => x.name));
  const cacheNames = cachedModels.map(m => m.id || m.name);
  return uniq(recommended.concat(cacheNames));
}

function renderModelSelectors() {
  const root = document.getElementById('modelSelectors');
  if (!assessments.length) {
    root.innerHTML = '<p class="muted">暂无模型评估。</p>';
    return;
  }
  root.innerHTML = '';
  for (const item of assessments) {
    const wrap = document.createElement('div');
    wrap.className = 'model-row';
    const label = document.createElement('label');
    label.textContent = item.label;
    const select = document.createElement('select');
    select.id = 'model-' + item.key;
    for (const name of optionNames(item)) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name === item.default_model.name ? name + '（默认）' : name;
      select.appendChild(option);
    }
    const note = document.createElement('p');
    note.className = 'muted tiny';
    note.textContent = item.default_model.why;
    wrap.appendChild(label);
    wrap.appendChild(select);
    wrap.appendChild(note);
    root.appendChild(wrap);
  }
}

function collectSelectedModels() {
  const selected = {};
  for (const item of assessments) {
    const node = document.getElementById('model-' + item.key);
    selected[item.key] = node ? node.value : item.default_model.name;
  }
  return selected;
}

function renderSteps(steps) {
  const root = document.getElementById('steps');
  if (!steps || !steps.length) {
    root.innerHTML = '<p class="muted">暂无流程状态。</p>';
    return;
  }
  root.innerHTML = '';
  for (const step of steps) {
    const div = document.createElement('div');
    div.className = 'step';
    const model = step.model ? `<p class="tiny muted">模型：<code>${step.model}</code></p>` : '';
    div.innerHTML = `
      <div class="step-head">
        <strong>${step.label}</strong>
        <span>
          <span class="badge ${step.mode}">${step.mode}</span>
          <span class="badge ${step.status}">${step.status}</span>
        </span>
      </div>
      <p class="muted tiny">${step.description || ''}</p>
      ${model}
    `;
    root.appendChild(div);
  }
}

async function loadAssessment() {
  const res = await fetch('/api/model-assessment');
  const data = await res.json();
  assessments = data.recommendations || [];
  cachedModels = data.cached_models || [];
  renderModelSelectors();
  renderSteps(data.pipeline_steps || []);
}

async function refreshQiniuModels() {
  const btn = document.getElementById('refreshModelsBtn');
  const apiKey = getApiKey();
  if (!apiKey) {
    setApiStatus('请先填写 QINIU_API_KEY。', true);
    return;
  }
  sessionStorage.setItem('extract_esg_qiniu_api_key', apiKey);
  btn.disabled = true;
  btn.textContent = '刷新中...';
  setApiStatus('正在调用本机后端刷新七牛模型列表...');
  try {
    const res = await fetch('/api/refresh-models', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({qiniu_api_key: apiKey})
    });
    const data = await res.json();
    if (!res.ok) {
      setApiStatus(data.error || '刷新失败。', true);
      return;
    }
    cachedModels = data.cached_models || [];
    renderModelSelectors();
    setApiStatus(`刷新成功：${data.model_count} 个模型已缓存。`);
  } catch (err) {
    setApiStatus(String(err), true);
  } finally {
    btn.disabled = false;
    btn.textContent = '刷新七牛模型列表';
  }
}

async function loadReports() {
  const res = await fetch('/api/reports');
  const data = await res.json();
  const root = document.getElementById('reports');
  if (!data.length) {
    root.innerHTML = '<p class="muted">暂无报告。先运行本地链路。</p>';
    return;
  }
  root.innerHTML = '';
  const list = document.createElement('ul');
  for (const r of data) {
    const li = document.createElement('li');
    const a = document.createElement('span');
    a.className = 'report-link';
    a.textContent = r.report_id;
    a.onclick = () => loadReport(r.report_id);
    li.appendChild(a);
    li.appendChild(document.createTextNode(` - pages=${r.page_count}, packets=${r.packet_count}, disclosures=${r.disclosure_count}, cloud=${r.cloud_result_count || 0}`));
    list.appendChild(li);
  }
  root.appendChild(list);
}

async function loadReport(id) {
  const res = await fetch('/api/reports/' + encodeURIComponent(id));
  document.getElementById('detail').textContent = JSON.stringify(await res.json(), null, 2);
}

async function fileToBase64(file) {
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

async function runLocal() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  btn.textContent = '运行中...';
  const selectedModels = collectSelectedModels();
  renderSteps([
    {key:'local_pdf', label:'Local PDF inspection', mode:'local', status:'pending', description:'正在准备本地 PDF 证据。'},
    {key:'evidence_packets', label:'Evidence packet build', mode:'local', status:'pending', description:'等待构造证据包。'},
    {key:'api_plan', label:'API model plan', mode:'api', status:'planned', description:'云模型步骤本轮仅展示模型选择，不实际调用。'},
    {key:'sqlite_save', label:'SQLite persistence', mode:'local', status:'pending', description:'等待入库。'}
  ]);
  try {
    const body = {
      path: document.getElementById('pdfPath').value.trim(),
      report_id: document.getElementById('reportId').value.trim(),
      selected_models: selectedModels,
      qiniu_api_key: getApiKey()
    };
    const file = document.getElementById('pdfFile').files[0];
    if (file) {
      body.upload = { filename: file.name, content_base64: await fileToBase64(file) };
      body.path = '';
    }
    const res = await fetch('/api/run-local', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) {
      renderSteps([{key:'error', label:'Run failed', mode:'local', status:'error', description:data.error || 'unknown error'}]);
    } else {
      renderSteps(data.steps || []);
      document.getElementById('detail').textContent = JSON.stringify(data, null, 2);
      await loadReports();
    }
  } catch (err) {
    renderSteps([{key:'error', label:'Run failed', mode:'local', status:'error', description:String(err)}]);
  } finally {
    btn.disabled = false;
    btn.textContent = '运行本地链路并入库';
  }
}

async function runCloud() {
  const btn = document.getElementById('runCloudBtn');
  btn.disabled = true;
  btn.textContent = '云抽取中...';
  const selectedModels = collectSelectedModels();
  renderSteps([
    {key:'local_pdf', label:'Local PDF inspection', mode:'local', status:'pending', description:'正在准备本地 PDF 证据。'},
    {key:'evidence_packets', label:'Evidence packet build', mode:'local', status:'pending', description:'等待构造证据包。'},
    {key:'structured_extract', label:'Full-harvest extraction', mode:'api', status:'pending', description:'即将真实调用七牛模型。'},
    {key:'sqlite_save', label:'SQLite persistence', mode:'local', status:'pending', description:'等待保存 cloud result 和候选披露。'}
  ]);
  try {
    const body = {
      path: document.getElementById('pdfPath').value.trim(),
      report_id: document.getElementById('reportId').value.trim(),
      selected_models: selectedModels,
      qiniu_api_key: getApiKey(),
      start_packet: Number(document.getElementById('startPacket').value || 0),
      max_packets: Number(document.getElementById('maxPackets').value || 2)
    };
    const file = document.getElementById('pdfFile').files[0];
    if (file) {
      body.upload = { filename: file.name, content_base64: await fileToBase64(file) };
      body.path = '';
    }
    const res = await fetch('/api/run-cloud', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) {
      renderSteps([{key:'error', label:'Cloud run failed', mode:'api', status:'error', description:data.error || 'unknown error'}]);
    } else {
      renderSteps(data.steps || []);
      document.getElementById('detail').textContent = JSON.stringify(data, null, 2);
      await loadReports();
    }
  } catch (err) {
    renderSteps([{key:'error', label:'Cloud run failed', mode:'api', status:'error', description:String(err)}]);
  } finally {
    btn.disabled = false;
    btn.textContent = '运行真实云抽取';
  }
}

async function init() {
  const storedApiKey = sessionStorage.getItem('extract_esg_qiniu_api_key');
  if (storedApiKey) {
    document.getElementById('apiKey').value = storedApiKey;
    setApiStatus('已从当前浏览器会话恢复 API Key。');
  }
  await loadAssessment();
  await loadReports();
}
init();
</script>
</body>
</html>"""


class LocalConsoleHandler(BaseHTTPRequestHandler):
    settings: Settings
    store: SqliteStore

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            _html_response(self, _page())
            return
        if path == "/api/model-assessment":
            _json_response(self, self._model_assessment())
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/refresh-models":
            self._refresh_models()
            return
        if parsed.path == "/api/run-local":
            self._run_local()
            return
        if parsed.path == "/api/run-cloud":
            self._run_cloud()
            return
        _json_response(self, {"error": "not_found"}, status=404)

    def _model_assessment(self) -> dict[str, object]:
        cached_models: list[dict[str, object]] = []
        registry = QiniuModelRegistry(cache_path=self.settings.model_cache)
        try:
            cached_models = [model.model_dump(mode="json") for model in registry.load_cache()]
        except Exception as exc:  # pragma: no cover - diagnostic only
            cached_models = [{"error": f"model cache unavailable: {exc}"}]
        return {
            "recommendations": api_model_assessment_payload(),
            "cached_models": cached_models,
            "pipeline_steps": _pipeline_steps({}),
            "note": "Model names are planning defaults. Refresh Qiniu models with CLI before real cloud calls.",
        }

    def _refresh_models(self) -> None:
        try:
            payload = _read_json_body(self)
            api_key = str(payload.get("qiniu_api_key") or "").strip()
            if not api_key:
                raise ValueError("QINIU_API_KEY is required to refresh Qiniu models.")

            settings = self.settings.__class__(**{**self.settings.__dict__, "qiniu_api_key": api_key})
            registry = QiniuModelRegistry(
                cache_path=self.settings.model_cache,
                adapter=QiniuModelAdapter(settings),
            )
            models = registry.refresh()
            _json_response(
                self,
                {
                    "status": "refreshed",
                    "cache": str(registry.cache_path),
                    "model_count": len(models),
                    "cached_models": [model.model_dump(mode="json") for model in models],
                    "note": "API key was used for this request only and was not returned.",
                },
            )
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=400)

    def _run_cloud(self) -> None:
        try:
            payload = _read_json_body(self)
            selected_models = {
                str(key): str(value)
                for key, value in (payload.get("selected_models") or {}).items()  # type: ignore[union-attr]
                if value
            }
            api_key = str(payload.get("qiniu_api_key") or "").strip()
            settings = self.settings.__class__(**{**self.settings.__dict__, "qiniu_api_key": api_key or self.settings.qiniu_api_key})
            if not settings.qiniu_api_key:
                raise ValueError("QINIU_API_KEY is required for real cloud extraction.")

            artifact_path = self._artifact_path_from_payload(payload)
            report_id_raw = payload.get("report_id")
            report_id = str(report_id_raw).strip() if report_id_raw else None
            model_id = self._exact_cached_model_id(selected_models.get("structured_extract"))
            start_packet = int(payload.get("start_packet") or 0)
            max_packets = int(payload.get("max_packets") or 2)
            state = ReportProcessingWorkflow().run_cloud_pipeline(
                artifact_path,
                report_id=report_id or None,
                store=self.store,
                settings=settings,
                model_id=model_id,
                start_packet=max(0, start_packet),
                max_packets=max(1, min(max_packets, 10)),
            )
            result = {
                "status": state.status,
                "report_id": state.report_id,
                "artifact_path": str(artifact_path),
                "selected_models": selected_models,
                "actual_model_ids": sorted({item.model_id for item in state.cloud_results}),
                "steps": _pipeline_steps(selected_models, completed=True, api_completed=True),
                "counts": {
                    "pages": len(state.document_ir.pages) if state.document_ir else 0,
                    "packets_total": len(state.packets),
                    "cloud_results": len(state.cloud_results),
                    "disclosures": len(state.disclosures),
                },
                "usage": [item.usage for item in state.cloud_results],
                "note": "Real cloud extraction completed. Results are candidate disclosures and still require review gates.",
            }
            _json_response(self, result)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=400)

    def _exact_cached_model_id(self, selected: str | None) -> str | None:
        if not selected:
            return None
        try:
            registry = QiniuModelRegistry(cache_path=self.settings.model_cache)
            ids = {model.id for model in registry.load_cache()}
        except Exception:
            ids = set()
        return selected if selected in ids else None

    def _run_local(self) -> None:
        try:
            payload = _read_json_body(self)
            selected_models = {
                str(key): str(value)
                for key, value in (payload.get("selected_models") or {}).items()  # type: ignore[union-attr]
                if value
            }
            artifact_path = self._artifact_path_from_payload(payload)
            report_id_raw = payload.get("report_id")
            report_id = str(report_id_raw).strip() if report_id_raw else None
            state = ReportProcessingWorkflow().run_local_pipeline(artifact_path, report_id=report_id or None, store=self.store)
            result = {
                "status": state.status,
                "report_id": state.report_id,
                "artifact_path": str(artifact_path),
                "selected_models": selected_models,
                "steps": _pipeline_steps(selected_models, completed=True),
                "counts": {
                    "pages": len(state.document_ir.pages) if state.document_ir else 0,
                    "packets": len(state.packets),
                    "disclosures": len(state.disclosures),
                },
                "note": "This local-console run does not call paid cloud models yet; API steps are planned_not_run.",
            }
            _json_response(self, result)
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=400)

    def _artifact_path_from_payload(self, payload: dict[str, object]) -> Path:
        upload = payload.get("upload")
        if isinstance(upload, dict) and upload.get("content_base64"):
            filename = _safe_filename(str(upload.get("filename") or "uploaded.pdf"))
            upload_dir = self.settings.object_store / "uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = upload_dir / filename
            artifact_path.write_bytes(base64.b64decode(str(upload["content_base64"])))
            return artifact_path

        path_value = str(payload.get("path") or "").strip()
        if not path_value:
            raise ValueError("Provide either a PDF path or a PDF upload.")
        artifact_path = Path(path_value).expanduser()
        if not artifact_path.exists():
            raise FileNotFoundError(f"PDF not found: {artifact_path}")
        return artifact_path

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(*, db_path: str | Path, host: str = "127.0.0.1", port: int = 18765, settings: Settings | None = None) -> None:
    LocalConsoleHandler.settings = settings or Settings.from_env()
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
