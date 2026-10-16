"""Retrieval metrics and extractive question generation (pure)."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

# Latin letters including accented ones, so that Spanish and French text tokenises as words.
_WORD = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\-/\.]*[A-Za-zÀ-ÿ0-9]|[A-Za-zÀ-ÿ0-9]")
_SPLIT_SENT = re.compile(r"(?<=[.!?])\s+")
MIN_SENTENCE_WORDS = 5


def recall_at_k(ranked_ids: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """1.0 if any relevant id is among the first k (hit rate with a single relevant item)."""
    rel = set(relevant)
    return 1.0 if any(d in rel for d in list(ranked_ids)[:k]) else 0.0


def reciprocal_rank(ranked_ids: Sequence[str], relevant: Iterable[str]) -> float:
    rel = set(relevant)
    for i, d in enumerate(ranked_ids, start=1):
        if d in rel:
            return 1.0 / i
    return 0.0


def evaluate_retrieval(
    results: Sequence[Sequence[str]], relevants: Sequence[Iterable[str]], ks: Sequence[int] = (1, 3, 5)
) -> dict:
    """Mean recall@k and MRR over a set of queries."""
    if len(results) != len(relevants):
        raise ValueError("results and relevants must have the same length")
    n = len(results)
    if n == 0:
        return {**{f"recall_at_{k}": 0.0 for k in ks}, "mrr": 0.0, "n_queries": 0}
    metrics = {
        f"recall_at_{k}": sum(recall_at_k(r, rel, k) for r, rel in zip(results, relevants)) / n for k in ks
    }
    metrics["mrr"] = sum(reciprocal_rank(r, rel) for r, rel in zip(results, relevants)) / n
    metrics["n_queries"] = n
    return metrics


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def extractive_question(chunk_text: str, corpus_doc_freq: Counter, n_docs: int, max_terms: int = 8) -> str:
    """Fallback question (no LLM) built from a chunk.

    Picks the sentence with the highest IDF sum and reduces it to its `max_terms` rarest
    terms (in order of appearance). The resulting query shares vocabulary with the chunk,
    so it overestimates lexical retrieval: it is a floor for testing, not a substitute for
    LLM-paraphrased questions.
    """

    def idf(t: str) -> float:
        return math.log((n_docs + 1) / (corpus_doc_freq.get(t, 0) + 1)) + 1.0

    sentences = [s for s in _SPLIT_SENT.split(chunk_text) if len(s.split()) >= MIN_SENTENCE_WORDS] or [
        chunk_text
    ]
    best = max(sentences, key=lambda s: sum(idf(t) for t in set(_tokens(s))) / (1 + len(_tokens(s)) ** 0.5))
    toks = _tokens(best)
    ranked = sorted(set(toks), key=lambda t: (-idf(t), -len(t), t))[:max_terms]  # deterministic tie-break
    keep = [t for t in toks if t in ranked]
    seen, ordered = set(), []
    for t in keep:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return " ".join(ordered)


def doc_frequency(chunks: Sequence[str]) -> Counter:
    df: Counter = Counter()
    for c in chunks:
        df.update(set(_tokens(c)))
    return df
