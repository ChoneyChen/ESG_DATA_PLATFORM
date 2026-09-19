"""Small, separately budgeted contextual evidence; never a quantity worklist."""
from __future__ import annotations

import json
import re

from esg_targeted.contracts import EvidenceInventory, EvidencePacket
from esg_targeted.retrieval.fact_pattern import contains_term, normalize_match_text


METHOD = re.compile(
    r"方法|核算|计算|計算|因子|系数|係數|边界|邊界|口径|口徑|"
    r"location[ -]?based|market[ -]?based|method|emission.factor|boundary|"
    r"accounting|calculated|restated", re.I,
)
NOTE = re.compile(r"^(?:注[：:]?|[0-9]+[.、．)]\s+[^0-9]|note\b|\*|[①②③④⑤])", re.I)


def _context_text(value):
    # Punctuation variants must not hide 'location based' behind an English
    # 'Location-based' alias. Only used to rank explanatory context.
    return re.sub(r"[\s-]+", "", normalize_match_text(value))


class EvidenceContextCompiler:
    def __init__(self, max_chars: int = 6500, max_items: int = 8):
        self.max_chars, self.max_items = max_chars, max_items

    def attach(self, packet: EvidencePacket, inventory: EvidenceInventory, package=None) -> EvidencePacket:
        if not packet.allowed_group_ids:
            return packet
        pages = {s.page_index for s in packet.spans if s.context_group_id in packet.allowed_group_ids}
        term_groups = [[_context_text(t) for t in group if len(_context_text(t)) >= 3]
                       for group in packet.query.intent.topic_term_groups]
        existing = {s.span_id for s in packet.spans}
        candidates = []
        for span in inventory.spans:
            if span.span_type != "block" or span.has_table_structure or span.span_id in existing:
                continue
            text = span.text.strip()
            if len(text) < 12 or len(text) > self.max_chars:
                continue
            normalized = _context_text(text)
            # Count concepts, not every redundant synonym; generic report
            # boundary vocabulary must not outrank the metric's method note.
            topic = sum(any(term in normalized for term in group) for group in term_groups)
            conflict = any(
                contains_term(text, term)
                for term in packet.query.intent.must_not_terms
            )
            distance = min(abs(span.page_index - p) for p in pages)
            method = bool(METHOD.search(text))
            note = bool(NOTE.search(text))
            if conflict and topic == 0:
                continue
            if not ((distance <= 2 and (note or method)) or (method and topic)):
                continue
            # Geographic proximity is a candidate-link signal, not a semantic
            # applicability assertion. The model sees page and scope explicitly.
            score = 12 * min(topic, 3) + 8 * method + 8 * note + max(0, 12 - 6 * distance)
            candidates.append((score, span))
        candidates.sort(key=lambda pair: (-pair[0], pair[1].page_index, pair[1].span_id))
        contexts, used, seen_text = [], 0, set()
        for _, span in candidates:
            text_key = " ".join(span.text.split())
            if text_key in seen_text or used + len(span.text) > self.max_chars:
                continue
            contexts.append(span)
            seen_text.add(text_key)
            used += len(span.text)
            if len(contexts) == self.max_items:
                break
        payload = json.loads(packet.model_context)
        if package is not None:
            code_sets = {c.code_set_id: c for c in [*package.core.common_code_sets, *package.code_sets]}
            for element in payload.get("elements", []):
                codes = code_sets.get(element.get("code_set_id"))
                if codes:
                    element["allowed_values"] = [
                        {"code": v.code, "label": v.labels.get("zh") or v.labels.get("en")}
                        for v in codes.values
                    ]
                    element["open_vocabulary"] = codes.extensible
        return packet.model_copy(update={
            "spans": [*packet.spans, *contexts],
            "context_only_span_ids": [s.span_id for s in contexts],
            "alias_map": {**packet.alias_map, "context": {f"L{i}": s.span_id for i, s in enumerate(contexts, 1)}},
            "model_context": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "budget": {**packet.budget, "linked_context_count": len(contexts), "linked_context_chars": used},
        })
