"""Troceo de texto para el RAG (puro, sin Spark ni dependencias externas)."""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

# Aproximacion de tokens: ~1.33 tokens por palabra en inglés/espanol con tokenizadores
# BPE habituales. Es una estimación; el modelo de embeddings usa su propio tokenizador.
TOKENS_PER_WORD = 1.33
MIN_WORDS_PER_PAGE = 20  # páginas casi vacias (portadas, separadores) se omiten

_SENTENCE_END = re.compile(r"[.!?;:]\)?$")


def estimate_tokens(text: str) -> int:
    return int(round(len(text.split()) * TOKENS_PER_WORD))


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"-\n(?=\w)", "", text)        # palabras partidas por guion al final de linea
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, max_tokens: int = 800, overlap_tokens: int = 100) -> list[str]:
    """Trocea `text` en fragmentos de ~max_tokens con solape de ~overlap_tokens.

    Ventana deslizante sobre palabras. Para no cortar a mitad de frase, el final de cada
    fragmento retrocede (como mucho el 20 % de la ventana) hasta una palabra que termine
    en signo de puntuacion. Garantias: cada fragmento tiene al menos una palabra, el
    avance es siempre positivo y todo el texto queda cubierto.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens debe ser positivo")
    if not 0 <= overlap_tokens < max_tokens:
        raise ValueError("overlap_tokens debe estar en [0, max_tokens)")
    words = _clean(text).split()
    if not words:
        return []
    max_w = max(1, int(max_tokens / TOKENS_PER_WORD))
    ov_w = int(overlap_tokens / TOKENS_PER_WORD)
    if len(words) <= max_w:
        return [" ".join(words)]
    chunks: list[str] = []
    start = 0
    n = len(words)
    while start < n:
        end = min(start + max_w, n)
        if end < n:
            lo = max(start + 1, end - max(1, int(0.2 * max_w)))
            for j in range(end, lo, -1):
                if _SENTENCE_END.search(words[j - 1]):
                    end = j
                    break
        chunks.append(" ".join(words[start:end]))
        if end >= n:
            break
        start = max(end - ov_w, start + 1)
    return chunks


def chunk_id(source: str, page: int, idx: int) -> str:
    """Identificador estable (clave primaria del índice) a partir de archivo, página e índice."""
    return hashlib.sha1(f"{source}|{page}|{idx}".encode("utf-8")).hexdigest()[:16]


def chunk_pages(source: str, pages: Iterable[tuple[int, str]], max_tokens: int = 800,
                overlap_tokens: int = 100) -> list[dict]:
    """Trocea un documento página a página (para poder citar archivo y página exactos).

    `pages` es una secuencia de (numero_de_pagina, texto) con numeracion desde 1.
    """
    out: list[dict] = []
    for page_no, text in pages:
        if len(_clean(text or "").split()) < MIN_WORDS_PER_PAGE:
            continue
        for i, piece in enumerate(chunk_text(text, max_tokens, overlap_tokens)):
            out.append({
                "chunk_id": chunk_id(source, page_no, i),
                "source": source,
                "page": int(page_no),
                "chunk_idx": i,
                "text": piece,
                "n_tokens_est": estimate_tokens(piece),
            })
    return out
