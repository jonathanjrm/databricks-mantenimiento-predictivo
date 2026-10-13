"""Metricas de retrieval y generación de preguntas extractivas (puro)."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

_WORD = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\-/\.]*[A-Za-zÀ-ÿ0-9]|[A-Za-zÀ-ÿ0-9]")
_SPLIT_SENT = re.compile(r"(?<=[.!?])\s+")


def recall_at_k(ranked_ids: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """1.0 si algún relevante esta entre los k primeros (hit rate con un único relevante)."""
    rel = set(relevant)
    return 1.0 if any(d in rel for d in list(ranked_ids)[:k]) else 0.0


def reciprocal_rank(ranked_ids: Sequence[str], relevant: Iterable[str]) -> float:
    rel = set(relevant)
    for i, d in enumerate(ranked_ids, start=1):
        if d in rel:
            return 1.0 / i
    return 0.0


def evaluate_retrieval(results: Sequence[Sequence[str]], relevants: Sequence[Iterable[str]],
                       ks: Sequence[int] = (1, 3, 5)) -> dict:
    """Media de recall@k y MRR sobre un conjunto de consultas."""
    if len(results) != len(relevants):
        raise ValueError("results y relevants deben tener la misma longitud")
    n = len(results)
    if n == 0:
        return {**{f"recall_at_{k}": 0.0 for k in ks}, "mrr": 0.0, "n_queries": 0}
    metrics = {f"recall_at_{k}": sum(recall_at_k(r, rel, k) for r, rel in zip(results, relevants)) / n
               for k in ks}
    metrics["mrr"] = sum(reciprocal_rank(r, rel) for r, rel in zip(results, relevants)) / n
    metrics["n_queries"] = n
    return metrics


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text)]


def extractive_question(chunk_text: str, corpus_doc_freq: Counter, n_docs: int,
                        max_terms: int = 8) -> str:
    """Pregunta de respaldo (sin LLM) a partir de un fragmento.

    Elige la frase con mayor suma de IDF y la reduce a sus `max_terms` terminos mas raros
    (en orden de aparicion). La consulta resultante comparte vocabulario con el fragmento,
    por lo que sobreestima el retrieval lexico: es un suelo de pruebas, no un sustituto
    de preguntas parafraseadas por un LLM.
    """
    def idf(t: str) -> float:
        return math.log((n_docs + 1) / (corpus_doc_freq.get(t, 0) + 1)) + 1.0

    sentences = [s for s in _SPLIT_SENT.split(chunk_text) if len(s.split()) >= 5] or [chunk_text]
    best = max(sentences, key=lambda s: sum(idf(t) for t in set(_tokens(s))) / (1 + len(_tokens(s)) ** 0.5))
    toks = _tokens(best)
    ranked = sorted(set(toks), key=lambda t: (-idf(t), -len(t), t))[:max_terms]  # desempate determinista
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
