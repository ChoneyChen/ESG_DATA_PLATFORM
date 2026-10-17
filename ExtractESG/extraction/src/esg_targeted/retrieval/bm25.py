from __future__ import annotations

import math
from collections import Counter, defaultdict

from esg_targeted.retrieval.tokenizer import tokenize


class Bm25Index:
    def __init__(self, documents: list[str], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.documents = [tokenize(document) for document in documents]
        self.term_frequencies = [Counter(document) for document in self.documents]
        self.doc_lengths = [len(document) for document in self.documents]
        self.avg_doc_length = sum(self.doc_lengths) / max(1, len(self.doc_lengths))
        doc_frequency: dict[str, int] = defaultdict(int)
        for document in self.documents:
            for term in set(document):
                doc_frequency[term] += 1
        count = len(self.documents)
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in doc_frequency.items()
        }

    def scores(self, query: str) -> list[float]:
        query_terms = tokenize(query)
        results: list[float] = []
        for frequencies, length in zip(self.term_frequencies, self.doc_lengths, strict=True):
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(self.avg_doc_length, 1)
                )
                score += self.idf.get(term, 0.0) * (frequency * (self.k1 + 1)) / denominator
            results.append(score)
        return results

