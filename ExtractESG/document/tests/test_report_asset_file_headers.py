from esg_document.api import main


def test_report_asset_file_is_served_inline_for_the_workbench(tmp_path, monkeypatch):
    """A click on the console must show the page, not download a PDF."""
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(main.report_assets, "resolve", lambda asset_id: pdf)
    response = main.get_report_asset_file("pdf-test")
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
