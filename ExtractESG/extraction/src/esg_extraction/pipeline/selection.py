"""Select reading candidates from clause-owned rules before a model call."""

from esg_clauses.reading import check_reading
from esg_data.units import unit_dimension
from .reading_frame import statement_scope


def quantitative_candidates(clause, frame):
    rows, excluded = [], []
    for row in frame.rows:
        decision = check_reading(clause, row.label, row.section, row.unit_raw,
            unit_dimension(row.unit_raw), evidence=" ".join([
                frame.title or "", *[part for parts in frame.columns.values() for part in parts],
                *[text.text for text in frame.texts if text.object_type != "cell"],
            ]))
        if decision.ok:
            rows.append(row)
        else:
            excluded.append({"clause_key":clause.clause_key, "object_id":frame.object_id,
                "row_id":row.row_id, "selection":"different_target",
                "reason":decision.flags[0] if decision.flags else "clause_reading_rule"})
    return frame.model_copy(update={"rows":rows}), excluded


def qualitative_candidates(clause, frame):
    """A valid quote's positive terms also occur in its containing span.

    Negative rules stay with the selected quote: an unrelated sentence in a
    mixed paragraph must not exclude its relevant statement.
    """
    spans, excluded = [], []
    for span in frame.texts:
        decision = check_reading(clause, span.text, frame.title, None, None,
            evidence=statement_scope(frame, span), requirements_only=True)
        if decision.ok:
            spans.append(span)
        else:
            excluded.append({"clause_key":clause.clause_key, "object_id":frame.object_id,
                "span_id":span.span_id or span.object_id, "selection":"different_target",
                "reason":decision.flags[0] if decision.flags else "clause_reading_rule"})
    return frame.model_copy(update={"texts":spans}), excluded
