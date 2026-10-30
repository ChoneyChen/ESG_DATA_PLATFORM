"""The publication dictionary stays frozen during the extraction-only rebuild."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'database'
def test_existing_public_dictionary_is_byte_identical():
    assert hashlib.sha256((ROOT/'catalog/metric-dictionary.v1.json').read_bytes()).hexdigest()=='c4d73f543d4a20aa604e44c422c7c3a41a50001332f18dbecaa05c1e5173c134'
def test_existing_public_dictionary_keeps_its_metric_identities():
    data=json.loads((ROOT/'catalog/metric-dictionary.v1.json').read_text())
    assert data['dictionary_version']=='1.5.0'
    assert len(data['metrics'])==55
    assert len({m['source_datapoint_id'] for m in data['metrics']})==55
