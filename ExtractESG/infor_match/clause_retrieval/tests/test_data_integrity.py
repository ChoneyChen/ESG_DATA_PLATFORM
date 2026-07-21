from __future__ import annotations

import csv
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CLAUSE_FILES = sorted((ROOT / "data" / "clauses").glob("*.csv"))
DICTIONARY_FILES = sorted((ROOT / "data" / "dictionaries").glob("*.csv"))
OFFICIAL_HOSTS = {
    "www.globalreporting.org",
    "en-rules.hkex.com.hk",
    "cn-rules.hkex.com.hk",
    "www.ifrs.org",
}


def read_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return reader.fieldnames or [], rows


class DataIntegrityTests(unittest.TestCase):
    def test_csv_rows_have_exactly_the_declared_columns(self):
        for path in CLAUSE_FILES + DICTIONARY_FILES:
            fields, rows = read_rows(path)
            self.assertTrue(fields, path)
            for index, row in enumerate(rows, start=2):
                self.assertNotIn(None, row, f"{path}:{index} has extra columns")
                self.assertEqual(set(row), set(fields), f"{path}:{index}")

    def test_clause_sources_use_known_official_hosts(self):
        for path in CLAUSE_FILES:
            _, rows = read_rows(path)
            for row in rows:
                url = row["source_url"]
                self.assertEqual(urlparse(url).scheme, "https", row["id"])
                self.assertIn(urlparse(url).hostname, OFFICIAL_HOSTS, row["id"])
                self.assertTrue(row["source_locator"], row["id"])

    def test_fixture_contains_only_the_minimal_document_ir_contract(self):
        path = ROOT / "data" / "fixtures" / "yuexiu_2024_pages_62_67" / "document_ir.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(document["pages"]), 6)
        required = {"page_id", "page_index", "printed_page_label", "text"}
        for page in document["pages"]:
            self.assertEqual(set(page), required)
            self.assertTrue(page["text"])

    def test_package_has_no_local_paths_or_embedded_secrets(self):
        patterns = [
            re.compile(r"[A-Za-z]:\\Users\\", re.I),
            re.compile("/" + "Users/"),
            re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
        ]
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() in {".html", ".pyc"}:
                continue
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
            for pattern in patterns:
                self.assertIsNone(pattern.search(text), path)


if __name__ == "__main__":
    unittest.main()
