"""Lexical fallback for clause phrases; no indicator names or model meanings."""
import re
import unicodedata
from .normalize import fold

_FUNCTION_WORDS = frozenset("a an the of for to in on at by from with and or is are be that which as total amount number".split())


def _words(text):
    words = re.findall(r"[a-z]+", unicodedata.normalize("NFKC", text).casefold())
    return {word[:-3] + "y" if word.endswith("ies") else
        word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
        for word in words if word not in _FUNCTION_WORDS}


def lexical_score(terms, text):
    """Match meaningful word groups after phrase matching failed.

    Parenthetical qualifiers can refine meaning without hiding the main noun
    phrase. The clause reader still determines the measurement and its scope.
    """
    observed = _words(text)
    seen, score = set(), 0.0
    for term in terms:
        if term.startswith("re:"):
            if re.search(term[3:], fold(text), re.I):
                score += 1
            continue
        for variant in (term, re.sub(r"[（(][^（）()]*[）)]", " ", term)):
            words = frozenset(_words(variant))
            if not words or words in seen:
                continue
            seen.add(words)
            hits = words & observed
            if words <= observed:
                score += len(hits) / len(words)
    return score
