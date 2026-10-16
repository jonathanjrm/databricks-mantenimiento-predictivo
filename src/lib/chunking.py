"""Text chunking for the RAG index (pure: no Spark, no external dependencies)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

# Rough token estimate: about 1.33 tokens per word for English/Spanish with common BPE
# tokenizers. The embedding model uses its own tokenizer, so this is only a sizing guide.
TOKENS_PER_WORD = 1.33
MIN_WORDS_PER_PAGE = 20  # near-empty pages (covers, separators) are skipped
SENTENCE_LOOKBACK_SHARE = 0.2  # how far back (share of the window) a chunk may end to finish a sentence

_SENTENCE_END = re.compile(r"[.!?;:]\)?$")


def estimate_tokens(text: str) -> int:
    return round(len(text.split()) * TOKENS_PER_WORD)


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"-\n(?=\w)", "", text)  # words hyphenated across a line break
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, max_tokens: int = 800, overlap_tokens: int = 100) -> list[str]:
    """Splits `text` into chunks of about `max_tokens` with an overlap of about `overlap_tokens`.

    Sliding window over words. To avoid cutting mid-sentence, the end of each chunk moves
    back (at most 20 % of the window) to the last word that ends with punctuation.
    Guarantees: every chunk has at least one word, the window always advances and the
    whole text is covered.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if not 0 <= overlap_tokens < max_tokens:
        raise ValueError("overlap_tokens must be in [0, max_tokens)")
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
            lo = max(start + 1, end - max(1, int(SENTENCE_LOOKBACK_SHARE * max_w)))
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
    """Stable identifier (primary key of the index) derived from file, page and chunk index."""
    return hashlib.sha1(f"{source}|{page}|{idx}".encode()).hexdigest()[:16]


def chunk_pages(
    source: str, pages: Iterable[tuple[int, str]], max_tokens: int = 800, overlap_tokens: int = 100
) -> list[dict]:
    """Chunks a document page by page, so that every chunk can cite an exact file and page.

    `pages` is a sequence of (page_number, text) with 1-based page numbers.
    """
    out: list[dict] = []
    for page_no, text in pages:
        if len(_clean(text or "").split()) < MIN_WORDS_PER_PAGE:
            continue
        for i, piece in enumerate(chunk_text(text, max_tokens, overlap_tokens)):
            out.append(
                {
                    "chunk_id": chunk_id(source, page_no, i),
                    "source": source,
                    "page": int(page_no),
                    "chunk_idx": i,
                    "text": piece,
                    "n_tokens_est": estimate_tokens(piece),
                }
            )
    return out
