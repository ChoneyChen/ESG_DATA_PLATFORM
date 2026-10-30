"""Minimal independent clause templates owned by the clause module."""

from esg_data.organization import (
    OrganizationSpec,
    OrganizationField,
    FieldBinding,
    Presentation,
)
from .contract import ClauseContract, ValueContract, ClausePurposes, EnrichmentPolicy
from .parts import ReadingContract, RetrievalProfile, EvidenceRequirement
from .scope import make_scope


def new_clause(clause_key, group_id, data_kind, title):
    quantitative = data_kind == "quantitative"
    fields = []
    for key, label in [
        ("reporting_entity", "统计主体"),
        ("reporting_period", "测量期间"),
        ("reporting_boundary", "统计边界"),
        ("measurement_method", "测量方法"),
    ]:
        fields.append(
            OrganizationField(
                key=key,
                label=label,
                role="reported_identity",
                binding=FieldBinding(source="identity", key=key),
                type_hint="json",
            )
        )
    fields.append(
        OrganizationField(
            key="raw_value",
            label="原值" if quantitative else "原文陈述",
            role="raw_value",
            binding=FieldBinding(
                source="value", key="value_raw" if quantitative else "statement_raw"
            ),
            required_for_confirmation=True,
        )
    )
    if quantitative:
        fields.append(
            OrganizationField(
                key="unit_raw",
                label="原单位",
                role="unit",
                binding=FieldBinding(source="value", key="unit_raw"),
            )
        )
    fields.append(
        OrganizationField(
            key="source_fact_id",
            label="事实编号",
            role="provenance",
            binding=FieldBinding(source="fact", key="source_fact_id"),
        )
    )
    organization = OrganizationSpec(
        title=title,
        fields=fields,
        row_axes=["reporting_entity", "reporting_period", "reporting_boundary"],
        presentation=Presentation(
            view="table" if quantitative else "statements",
            columns=[field.key for field in fields],
        ),
    )
    return (
        ClauseContract(
            clause_key=clause_key,
            group_id=group_id,
            labels={"zh": title},
            definition=title,
            exclusions=["标题、目录和检索未命中不能证明实际披露；未知信息不能补成零。"],
            data_kind=data_kind,
            purposes=ClausePurposes(enrichment=EnrichmentPolicy(identity_axes=[field.binding.key for field in fields if field.binding.source == "identity"])),
            value=ValueContract(),
            output_scopes=[make_scope("实际披露")],
            reading=ReadingContract(
                operator="numeric_label" if quantitative else "statement",
                instructions=[title],
            ),
            retrieval=RetrievalProfile(
                positive_terms=[title],
                source_priority=["table", "text"]
                if quantitative
                else ["text", "table"],
            ),
            evidence=[
                EvidenceRequirement(
                    field="value" if quantitative else "statement_context",
                    level="cell" if quantitative else "text",
                )
            ],
            organization=organization,
            readiness="blocked",
            blocking_reasons=[
                "需完成本条定义、单位、检索/阅读规则和样本核验后再启用。"
            ],
            clause_digest="0" * 64,
        )
        .seal()
        .verify()
    )
