"""Semantic polarity checks for authoring; historical releases stay readable.

This lint detects exact self-exclusion, not arbitrary-language contradictions.
Definitions, interpretation guidance and exclusions retain separate roles.
"""
from __future__ import annotations

import unicodedata


def _normalized(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def semantic_diagnostics(metrics) -> list[dict]:
    issues = []
    for metric in metrics:
        contract = metric.measurement_contract
        if contract is None:
            continue
        positive = {value for value in (
            _normalized(metric.description),
            _normalized(contract.positive_definition or ''),
            *(_normalized(value) for value in metric.labels.values()),
        ) if value}
        if contract.semantic_contract_version == 'partitioned-semantics-v1' and not (
                contract.positive_definition and contract.positive_definition.strip()):
            issues.append({'code':'missing_positive_definition','metric_id':metric.metric_id,
                           'source_datapoint_id':metric.source_datapoint_id,
                           'path':'measurement_contract.positive_definition'})
        for field in ('exclusions','distinguish_from'):
            for index, value in enumerate(getattr(contract, field)):
                if not value.strip() or _normalized(value) in positive:
                    issues.append({'code':'self_exclusion' if value.strip() else 'empty_semantic_rule',
                                   'metric_id':metric.metric_id,'source_datapoint_id':metric.source_datapoint_id,
                                   'path':f'measurement_contract.{field}[{index}]','value':value})
    return issues
