"""Evidence interface consumer. Never opens an extraction/IR directory."""
import json
import re
import time
from urllib.parse import urlsplit, quote
from urllib.request import build_opener, ProxyHandler


class EvidenceResolver:
    def __init__(self, base_url):
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Evidence endpoint must be an explicit credential-free service URL")
        self.base_url = base_url.rstrip("/")
        self.cache = {}

    def resolve(self, ir_run_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", ir_run_id):
            raise ValueError("Invalid evidence identity")
        cached = self.cache.get(ir_run_id)
        if cached and time.monotonic() - cached[0] < 10:
            return cached[1]
        with build_opener(ProxyHandler({})).open(self.base_url + "/api/evidence/" + quote(ir_run_id, safe=""), timeout=3) as response:
            body = json.load(response)
        if not isinstance(body, dict) or body.get("ir_run_id") != ir_run_id:
            raise ValueError("Evidence service returned a different document")
        if len(self.cache) > 128:
            self.cache.clear()
        self.cache[ir_run_id] = (time.monotonic(), body)
        return body
