import hashlib
import json
import zipfile

import pytest

from esg_v2.control_plane.esef_reader import CACHE_VERSION, SCHEMA_VERSION, EsefPackageReader, read_package


XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:ifrs="http://xbrl.ifrs.org/taxonomy/2022-03-24/ifrs-full">
<body>
<div style="display:none"><ix:header>
  <xbrli:context id="c-1">
    <xbrli:entity><xbrli:identifier scheme="urn:lei">529900T8BM49AURSDO55</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>
    <xbrli:scenario><xbrldi:explicitMember dimension="ifrs:SegmentAxis">ifrs:RetailMember</xbrldi:explicitMember></xbrli:scenario>
  </xbrli:context>
  <xbrli:context id="c-2">
    <xbrli:entity><xbrli:identifier scheme="urn:lei">529900T8BM49AURSDO55</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:unit id="u-1"><xbrli:measure>iso4217:GBP</xbrli:measure></xbrli:unit>
</ix:header></div>
<p>Revenue <ix:nonFraction name="ifrs:Revenue" contextRef="c-1" unitRef="u-1" decimals="-6" scale="6" sign="-">1,234</ix:nonFraction></p>
<p>Energy <ix:nonFraction name="esrs:E1_EnergyConsumption" contextRef="c-1" unitRef="u-1" scale="3">2.5<ix:exclude>footnote</ix:exclude></ix:nonFraction></p>
<p><ix:nonNumeric name="esrs:E1_Text" contextRef="c-1" continuedAt="cont-1">We report <b>climate</b> actions</ix:nonNumeric></p>
<ix:continuation id="cont-1"> and emissions.</ix:continuation>
</body></html>"""


def package(tmp_path, name="pkg.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("report/report.xhtml", XHTML)
        archive.writestr("META-INF/reportPackage.json", json.dumps({"documentInfo": {"documentType": "ESEF"}}))
        archive.writestr("META-INF/notes.txt", "not part of the report")
        archive.writestr("../outside.txt", "must never be read")
    return path


def test_inline_reader_streams_numeric_text_contexts_and_excludes_footnotes(tmp_path):
    data = read_package(package(tmp_path))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["package"]["artifact_kind"] == "ESEF_ZIP" and data["package"]["pdf_available"] is False
    assert data["package"]["sha256"] == hashlib.sha256(package(tmp_path).read_bytes()).hexdigest()
    assert data["package"]["metadata"]["documentInfo"]["documentType"] == "ESEF"
    assert all(not entry["name"].startswith("..") for entry in data["package"]["entries"])
    assert data["identity"]["lei_candidates"] == ["529900T8BM49AURSDO55"]
    assert data["identity"]["period_start"] == "2024-01-01"
    assert data["identity"]["period_end"] == "2024-12-31"
    numeric = next(fact for fact in data["facts"] if fact["concept"] == "ifrs:Revenue")
    assert numeric["value_numeric"] == "-1234000000"
    assert numeric["dimensions"] == {"ifrs:SegmentAxis": "ifrs:RetailMember"}
    energy = next(fact for fact in data["facts"] if fact["concept"] == "esrs:E1_EnergyConsumption")
    assert energy["value_numeric"] == "2500"
    assert "footnote" not in (energy["raw_text"] or "")
    text = next(fact for fact in data["facts"] if fact["concept"] == "esrs:E1_Text")
    assert "We report climate actions" in text["value_text"]
    assert "and emissions." in text["value_text"]
    assert data["parse_errors"] == []


def test_reader_rejects_a_package_whose_sha_does_not_match_the_crawl_identity(tmp_path):
    path = package(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        read_package(path, expected_sha256="0" * 64)


def test_reader_cache_is_content_addressed_and_reused(tmp_path):
    path = package(tmp_path)
    cache = tmp_path / "cache"
    first = EsefPackageReader(cache_root=cache).read(path)
    target = cache / first["package"]["sha256"] / "reader.json"
    assert target.is_file()
    second = EsefPackageReader(cache_root=cache).read(path)
    assert second == first
    assert CACHE_VERSION >= 1


def test_reader_reports_a_missing_package_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_package(tmp_path / "missing.zip")
