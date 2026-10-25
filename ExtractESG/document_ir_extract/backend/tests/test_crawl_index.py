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


def test_download_ledger_reports_package_availability_without_turning_it_into_a_pdf(tmp_path):
    path = tmp_path / "gb_filings_primary.csv"
    fields = ["report_id", "lei", "entity_name", "period_end", "fiscal_year",
              "country", "report_type", "sha256", "package_url", "viewer_url"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for report_id in ("R1", "R2"):
            writer.writerow({"report_id": report_id, "lei": "LEI-1", "entity_name": "MHP SE",
                             "period_end": "2025-12-31", "fiscal_year": "2025", "country": "GB",
                             "report_type": "ESEF", "sha256": "a" * 64,
                             "package_url": f"https://example.invalid/{report_id}.zip"})
    work = tmp_path / "work"
    packages = work / "packages"
    packages.mkdir(parents=True)
    (packages / "LEI-1_2025-12-31_aaaaaaaa.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    with (work / "download_ledger.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["report_id", "lei", "file", "size_mb", "status", "esg_flag"])
        writer.writeheader()
        writer.writerow({"report_id": "R1", "lei": "LEI-1", "file": "LEI-1_2025-12-31_aaaaaaaa.zip",
                         "size_mb": "12.5", "status": "ok", "esg_flag": "1"})
    reader = CrawlIndexReader(tmp_path, output_dir=work)
    result = reader.reports("mhp", 2025)
    assert result["downloaded_count"] == 1 and result["ledger_count"] == 1
    one, two = result["items"]
    assert one["package_available"] is True and one["download_status"] == "ok"
    assert one["package_filename"] == "LEI-1_2025-12-31_aaaaaaaa.zip"
    assert one["package_size_mb"] == 12.5 and one["esg_flag"] is True
    assert one["artifact_kind"] == "ESEF_ZIP" and result["pdf_available"] is False
    assert two["package_available"] is False and two["download_status"] == "not_downloaded"
    status = reader.status()
    assert status["download_ledger_available"] is True and status["downloaded_count"] == 1
    assert status["package_dir"] == str(packages)
