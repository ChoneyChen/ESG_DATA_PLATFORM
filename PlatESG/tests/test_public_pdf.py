from __future__ import annotations

import hashlib
import json

import pytest

from PlatESG.backend.public_pdf import published_pdf


def test_published_source_resolves_only_registered_matching_bytes(tmp_path):
    content=b"%PDF-1.7\nregistered fixture only"
    pdf=tmp_path/"report.pdf"
    pdf.write_bytes(content)
    digest=hashlib.sha256(content).hexdigest()
    catalog=tmp_path/"report-catalog.json"
    catalog.write_text(json.dumps({"assets":[{"sha256":digest,"relative_path":"report.pdf"}]}))
    assert published_pdf({"source_sha256":digest},[tmp_path])==pdf
    pdf.write_bytes(b"%PDF-1.7\nchanged bytes")
    with pytest.raises(ValueError,match="differs"):
        published_pdf({"source_sha256":digest},[tmp_path])
    with pytest.raises(FileNotFoundError):
        published_pdf({"source_sha256":"0"*64},[tmp_path])


@pytest.mark.parametrize("relative",["../outside.pdf","/etc/report.pdf","../source.json"])
def test_catalog_cannot_escape_the_public_report_collection(tmp_path,relative):
    digest="0"*64
    (tmp_path/"report-catalog.json").write_text(json.dumps({"assets":[{"sha256":digest,"relative_path":relative}]}))
    with pytest.raises(ValueError):
        published_pdf({"source_sha256":digest},[tmp_path])
