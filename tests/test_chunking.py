import pytest

from lib import chunking as K


def _words(n, end_every=0):
    out = []
    for i in range(n):
        w = f"w{i}"
        if end_every and (i + 1) % end_every == 0:
            w += "."
        out.append(w)
    return " ".join(out)


def test_short_text_is_a_single_chunk():
    assert K.chunk_text("Hello world. This is short.") == ["Hello world. This is short."]
    assert K.chunk_text("   \n  ") == []


def test_size_and_overlap():
    text = _words(3000)
    chunks = K.chunk_text(text, max_tokens=800, overlap_tokens=100)
    max_w = int(800 / K.TOKENS_PER_WORD)
    assert len(chunks) >= 5
    for c in chunks:
        assert 0 < len(c.split()) <= max_w
        assert K.estimate_tokens(c) <= 800 + 2
    # overlap: the last words of a chunk reappear at the start of the next one
    a, b = chunks[0].split(), chunks[1].split()
    ov = int(100 / K.TOKENS_PER_WORD)
    assert a[-ov:] == b[:ov]
    # full coverage
    assert chunks[0].split()[0] == "w0" and chunks[-1].split()[-1] == "w2999"
    assert set(" ".join(chunks).split()) == set(text.split())


def test_cuts_at_sentence_end_when_possible():
    text = _words(2000, end_every=40)
    chunks = K.chunk_text(text, max_tokens=800, overlap_tokens=100)
    for c in chunks[:-1]:
        assert c.split()[-1].endswith(".")


def test_always_advances_with_a_large_overlap():
    chunks = K.chunk_text(_words(500), max_tokens=20, overlap_tokens=19)
    assert len(chunks) > 10 and chunks[-1].split()[-1] == "w499"


def test_invalid_parameters():
    with pytest.raises(ValueError):
        K.chunk_text("a b c", max_tokens=0)
    with pytest.raises(ValueError):
        K.chunk_text("a b c", max_tokens=10, overlap_tokens=10)


def test_cleans_hyphenation_and_whitespace():
    t = K.chunk_text("pre-\nventive   maintenance \x00 of the   engine")[0]
    assert t == "preventive maintenance of the engine"


def test_chunk_pages_cites_page_and_has_stable_ids():
    pages = [(1, "cover"), (2, _words(1500)), (3, _words(30))]
    out = K.chunk_pages("manual_a.pdf", pages, max_tokens=800, overlap_tokens=100)
    assert {c["page"] for c in out} == {2, 3}  # the cover (<20 words) is skipped
    assert all(c["source"] == "manual_a.pdf" for c in out)
    ids = [c["chunk_id"] for c in out]
    assert len(set(ids)) == len(ids)
    again = K.chunk_pages("manual_a.pdf", pages, 800, 100)
    assert ids == [c["chunk_id"] for c in again]
    assert K.chunk_id("x.pdf", 1, 0) != K.chunk_id("y.pdf", 1, 0)
