from __future__ import annotations

from extract_esg.contracts.cloud import CloudTaskResult
from extract_esg.contracts.document import DocumentIR


class LocalCloudFusion:
    """Fuse local deterministic evidence and cloud model candidates."""

    version = "local-cloud-fusion-0.1"

    def apply_cloud_result(self, document: DocumentIR, result: CloudTaskResult) -> DocumentIR:
        flags = list(document.conflict_flags)
        if not result.parsed_response:
            flags.append(f"cloud_result_unparsed:{result.request_id}")
        if "coordinates_missing" in result.quality_flags:
            flags.append(f"cloud_coordinates_missing:{result.request_id}")
        return document.model_copy(update={"conflict_flags": flags})

