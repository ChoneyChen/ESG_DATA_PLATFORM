"""Evidence interface consumer. Never opens an extraction/IR directory."""
from hashlib import sha256
import re
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler


class EvidenceResolver:
    def __init__(self, base_url):
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Evidence endpoint must be an explicit credential-free service URL")
        self.base_url = base_url.rstrip("/")

    def pdf(self,source_sha256):
        if not re.fullmatch(r"[a-f0-9]{64}",source_sha256):raise ValueError("Invalid published source identity")
        with build_opener(ProxyHandler({})).open(self.base_url+"/api/report-sources/"+source_sha256+"/file",timeout=30) as response:
            content=response.read(256*1024*1024+1)
        if len(content)>256*1024*1024:raise ValueError("Published source exceeds response capacity")
        if sha256(content).hexdigest()!=source_sha256:raise ValueError("Source bytes differ from the published PDF identity")
        return content
