"""Resolve model-selected quotations to original source bytes."""

import re
def source_quote(text, quote):
    """Resolve a unique quote to source bytes; only whitespace may differ."""
    if quote in text and text.count(quote) == 1:
        start = text.index(quote)
        return start, start + len(quote)
    positions = [index for index, char in enumerate(text) if not char.isspace()]
    compact = "".join(text[index] for index in positions)
    needle = "".join(char for char in quote if not char.isspace())
    start = compact.find(needle)
    if not needle or start < 0 or compact.find(needle, start + 1) >= 0:
        raise ValueError("Statement quote is missing or ambiguous within its source span")
    return positions[start], positions[start + len(needle) - 1] + 1


def measurement_quote(text, quote):
    """Keep the complete contiguous clause, including wrapped qualifiers."""
    start, end = source_quote(text, quote)
    prefix, suffix = text[:start].rstrip(), text[end:].lstrip()
    boundary = r"and|but|while|whereas"
    if prefix and not re.search(rf"(?:[,;:，；：]|\b(?:{boundary}))$", prefix, re.I):
        raise ValueError("Measurement quote starts inside its subject or qualifier")
    if suffix and not re.match(rf"(?:[,;:.!?，；：。！？]|(?:{boundary})\b)", suffix, re.I):
        raise ValueError("Measurement quote omits its trailing qualifier")
    return start, end
