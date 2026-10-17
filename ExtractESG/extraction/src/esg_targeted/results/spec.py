from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Final

from esg_targeted.io import read_json


RESULT_SCHEMA_VERSION: Final = "targeted-extraction-result-v1"
RESULT_BUNDLE_LAYOUT_VERSION: Final = "targeted-result-bundle-v1"
INSPECTION_SCHEMA_VERSION: Final = "targeted-result-inspection-v1"

RECORD_LAYOUT: Final = {
    "reporting_tasks": {
        "record_type": "reporting_task",
        "primary_key": "task_id",
        "filename": "reporting-tasks.jsonl",
    },
    "quantitative_observations": {
        "record_type": "quantitative_observation",
        "primary_key": "observation_id",
        "filename": "quantitative-observations.jsonl",
    },
    "qualitative_assertions": {
        "record_type": "qualitative_assertion",
        "primary_key": "assertion_id",
        "filename": "qualitative-assertions.jsonl",
    },
    "attribute_values": {
        "record_type": "attribute_value",
        "primary_key": "attribute_id",
        "filename": "attribute-values.jsonl",
    },
    "dimension_values": {
        "record_type": "dimension_value",
        "primary_key": "dimension_value_id",
        "filename": "dimension-values.jsonl",
    },
    "evidence_references": {
        "record_type": "evidence_reference",
        "primary_key": "evidence_id",
        "filename": "evidence-references.jsonl",
    },
}

RECORD_FILES: Final = {
    key: value["filename"] for key, value in RECORD_LAYOUT.items()
}

RECORD_ID_PREFIXES: Final = {
    "reporting_tasks": "task-",
    "quantitative_observations": "obs-",
    "qualitative_assertions": "assert-",
    "attribute_values": "attr-",
    "dimension_values": "dim-",
    "evidence_references": "evidence-",
}

EXPORT_FILES: Final = {
    "workbook": "exports/machine-fill.xlsx",
    "task_csv": "exports/task-summary.csv",
    "fact_csv": "exports/machine-fill.csv",
    "result_package": "exports/extraction-result.json",
}


def result_contract() -> dict:
    resource = files("esg_targeted.resources").joinpath("result-bundle-contract-v1.json")
    return read_json(Path(str(resource)))
