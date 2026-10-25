import csv

from esg_v2.control_plane.crawl_index import CrawlIndexReader


def test_crawl_index_is_read_only_and_keeps_external_identity_separate(tmp_path):
    path = tmp_path / "gb_filings_primary.csv"
    fields = ["report_id", "fxo_id", "api_id", "lei", "entity_name", "period_end", "fiscal_year",
              "country", "report_type", "sha256", "n_versions", "package_url", "json_url", "viewer_url"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"report_id": "R1", "fxo_id": "F1", "lei": "LEI-1", "entity_name": "MHP SE",
                         "period_end": "2025-12-31", "fiscal_year": "2025", "country": "CY",
                         "report_type": "ESEF", "sha256": "a" * 64, "n_versions": "2",
                         "package_url": "https://example.invalid/package.zip"})
    reader = CrawlIndexReader(tmp_path)
    result = reader.reports("mhp", 2025)
    assert result["available"] is True and result["pdf_available"] is False
    item = result["items"][0]
    assert item["source_report_id"] == "R1" and item["lei"] == "LEI-1"
    assert item["source_package_sha256"] == "a" * 64
    assert item["title"] is None and item["publisher_entity_ref"] is None and item["period_start"] is None
    assert item["requires_confirmation"] is True
    assert reader.companies()["items"][0]["filing_count"] == 1
    assert path.read_text(encoding="utf-8").startswith("report_id,")


def test_missing_crawl_index_is_an_explicit_unavailable_source(tmp_path):
    result = CrawlIndexReader(tmp_path).reports()
    assert result["available"] is False and result["items"] == []
