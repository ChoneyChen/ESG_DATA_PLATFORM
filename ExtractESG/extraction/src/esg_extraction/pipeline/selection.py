"""Select reading candidates from clause-owned rules before a model call."""

from esg_clauses.reading import check_reading
from esg_data.units import unit_dimension
from .reading_frame import statement_scope
from .numeric_context import closed_sources, context_sources, uses_local_context


def _local_requirements(clause, frame, row, sources):
    """Check only positive clause requirements against each literal's sources."""
    options = []
    for cell in row.cells:
        links = [link for link in frame.context_links if link.cell_id == cell.cell_id
            and all(key in sources for key in link.source_ids)]
        section = " › ".join(source.text for link in links if link.relation == "region"
            for source in closed_sources(frame, [sources[key] for key in link.source_ids]))
        labels = [link for link in links if link.relation in {"same_clause", "row", "column"}]
        if labels:
            options.extend((" › ".join(source.text for source in closed_sources(frame,
                [sources[key] for key in link.source_ids])), section) for link in labels)
        else:
            options.append((row.label, " › ".join(filter(None,
                [frame.title, row.section, section]))))
    if not options:
        options.append((row.label, row.section or frame.title))
    for label, section in options:
        # Unit text may be a positive requirement. Dimension guards and other
        # negative rules belong to the selected measurement's final binding.
        decision = check_reading(clause, label, section, row.unit_raw, None,
            evidence=" ".join(filter(None, [label, section, row.unit_raw])),
            requirements_only=True)
        if decision.ok:
            return decision
    return decision


def quantitative_candidates(clause, frame):
    rows, excluded = [], []
    sources = context_sources(frame)
    for row in frame.rows:
        if uses_local_context(frame,row):
            decision = _local_requirements(clause, frame, row, sources)
        else:
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
    cell_ids = {cell.cell_id for row in rows for cell in row.cells}
    return frame.model_copy(update={"rows": rows, "context_links": [
        link for link in frame.context_links if link.cell_id in cell_ids]}), excluded


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
