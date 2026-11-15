"""Document regressions own their configuration and PDF resource metadata."""
import pytest


@pytest.fixture(autouse=True)
def isolated_report_resource_library(tmp_path,monkeypatch):
    from esg_document import config
    monkeypatch.setattr(config,'load_default_env_files',lambda: None)
    # Settings constructed with a temporary OCR output must never register its
    # synthetic input in the real user's default PDF library sidecar.
    monkeypatch.setenv('ESG_REPORT_ASSET_DIR',str(tmp_path/'report-resource-library'))
