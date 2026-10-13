"""Tests of the assistant's RAG logic (app/rag.py) with fake LLM and search clients."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import rag  # noqa: E402


def chunk(cid, page, text, source="amt_powerplant_handbook.pdf", score=0.5):
    return {"chunk_id": cid, "source": source, "page": page, "text": text, "score": score}


BODY_413 = ("10-45 Compressor Section Maintenance of the compressor ... Minor damage to axial-flow "
            "engine compressor blades may be repaired if the damage can be removed without exceeding "
            "the allowable limits established by the manufacturer.")
GLOSSARY = "G-14 High-pressure compressor. The second-stage compressor in a dual-spool gas turbine engine."
INDEX = "I-3 Compressor Section ........ 10-45 Inspection and Cleaning ........ 10-45 Blending ........ 10-47"
PREFACE = "ii The Aviation Maintenance Technician Handbook-Powerplant (FAA-H-8083-32B) is one of a series"


# --- page classification -----------------------------------------------------
@pytest.mark.parametrize("text,kind", [
    (BODY_413, "body"), (GLOSSARY, "glossary"), (INDEX, "toc_index"), (PREFACE, "front_matter")])
def test_page_kind(text, kind):
    assert rag.page_kind(text) == kind


def test_page_kind_map_uses_first_chunk_of_page():
    m = rag.page_kind_map([chunk("a", 472, GLOSSARY)])
    assert m[("amt_powerplant_handbook.pdf", 472)] == "glossary"


# --- retrieval -------------------------------------------------------------
def test_rrf_rewards_agreement_between_queries():
    a = [chunk("x", 1, "x"), chunk("y", 2, "y")]
    b = [chunk("y", 2, "y"), chunk("z", 3, "z")]
    fused = rag.rrf_fuse([a, b])
    assert fused[0]["chunk_id"] == "y"
    assert {c["chunk_id"] for c in fused} == {"x", "y", "z"}


def test_v2_drops_toc_and_front_matter_keeps_glossary_in_rank_order():
    res = [chunk("idx", 499, INDEX, score=0.9), chunk("pre", 3, PREFACE, score=0.8),
           chunk("glo", 472, GLOSSARY, score=0.7), chunk("body", 413, BODY_413, score=0.6)]
    kinds = rag.page_kind_map(res)
    top, trace = rag.retrieve(["q"], lambda q, n: [dict(c) for c in res], rag.CONFIGS["v2"], kinds)
    assert [c["chunk_id"] for c in top] == ["glo", "body"]
    assert trace[0]["query"] == "q" and len(trace[0]["results"]) == 4


def test_v1_keeps_ranking_untouched():
    res = [chunk("idx", 499, INDEX), chunk("body", 413, BODY_413)]
    top, _ = rag.retrieve(["q"], lambda q, n: [dict(c) for c in res], rag.CONFIGS["v1_baseline"])
    assert [c["chunk_id"] for c in top] == ["idx", "body"]


def test_multi_query_falls_back_to_question_on_bad_json():
    qs = rag.rewrite_queries("¿Cómo se inspecciona el HPC?", lambda m, n: "not json", multi=True)
    assert qs == ["¿Cómo se inspecciona el HPC?"]


def test_multi_query_parses_and_deduplicates():
    out = json.dumps(["How is the HPC inspected?", "HPC inspection", "compressor section inspection"])
    qs = rag.rewrite_queries("q", lambda m, n: out, multi=True)
    assert qs[:3] == ["How is the HPC inspected?", "HPC inspection", "compressor section inspection"]
    assert qs[-1] == "q"


# --- generation and grounding ----------------------------------------------
def test_free_text_claims_keep_their_citations_including_fullwidth_brackets():
    claims = rag.claims_from_free_text("Inspect the blades with a strong light 【1】. No cracks are tolerated [2].")
    assert [c["citations"] for c in claims] == [[1], [2]]


def test_structured_output_unparseable_shows_no_claims():
    g = rag.generate("q", [chunk("a", 1, BODY_413)], lambda m, n: "sorry", rag.CONFIGS["v2"])
    assert g["status"] == "insufficient_evidence" and g["claims"] == []


def test_invalid_and_missing_citations_are_not_supported():
    chunks = [chunk("a", 413, BODY_413)]
    out = rag.verify_claims([{"text": "x", "citations": [5]}, {"text": "y", "citations": []}],
                            chunks, llm=lambda m, n: '{"verdict": "SUPPORTED"}')
    assert [c["verdict"] for c in out] == ["NOT_SUPPORTED", "NOT_SUPPORTED"]
    assert out[0]["invalid_citations"] == [5]


def test_lexical_support():
    assert rag.lexical_support("compressor blades repaired within manufacturer limits", BODY_413) > 0.7
    assert rag.lexical_support("torque the bolts to 450 inch-pounds", BODY_413) < 0.3


class FakeLLM:
    """Answers the rewrite, generation and judge prompts like a well-behaved model."""

    def __init__(self, generation: dict, verdicts: dict, coverage: str = "specific"):
        self.generation, self.verdicts, self.coverage = generation, verdicts, coverage

    def __call__(self, messages, max_tokens):
        system = messages[0]["content"]
        if system.startswith("You turn a maintenance question"):
            return json.dumps(["How is the high pressure compressor inspected?", "HPC inspection",
                               "compressor section inspection"])
        if system.startswith("You decide how well"):
            return json.dumps({"coverage": self.coverage, "explanation": "fake coverage"})
        if system.startswith("You check whether a claim"):
            claim = messages[1]["content"].split("\n")[0].replace("Claim: ", "")
            return json.dumps({"verdict": self.verdicts.get(claim, "NOT_SUPPORTED"), "reason": "fake"})
        return json.dumps(self.generation)


def test_unsupported_claims_are_removed_not_rewritten():
    gen = {"status": "insufficient_evidence", "summary": "General compressor guidance only.",
           "claims": [{"text": "Minor blade damage may be repaired within manufacturer limits.", "citations": [1]},
                      {"text": "HPC blades must be inspected every 500 cycles.", "citations": [1]}],
           "gaps": "No HPC-specific procedure.", "manufacturer_note": "Limits come from the manufacturer."}
    llm = FakeLLM(gen, {"Minor blade damage may be repaired within manufacturer limits.": "SUPPORTED",
                        "General compressor guidance only.": "SUPPORTED"}, coverage="general_only")
    a = rag.answer("¿Cómo se inspecciona el compresor de alta presión?", llm,
                   lambda q, n: [chunk("c413", 413, BODY_413)], "v2")
    assert a.status == "insufficient_evidence"
    assert [c["text"] for c in a.claims] == ["Minor blade damage may be repaired within manufacturer limits."]
    assert [c["text"] for c in a.removed_claims] == ["HPC blades must be inspected every 500 cycles."]
    assert a.gaps == "No HPC-specific procedure."


def test_answered_without_supported_claims_is_downgraded():
    gen = {"status": "answered", "summary": "s", "claims": [{"text": "made up", "citations": [1]}],
           "gaps": "", "manufacturer_note": ""}
    a = rag.answer("q", FakeLLM(gen, {}), lambda q, n: [chunk("c", 413, BODY_413)], "v2")
    assert a.status == "insufficient_evidence" and a.claims == []


def test_no_retrieval_is_not_found():
    a = rag.answer("q", FakeLLM({}, {}), lambda q, n: [], "v2")
    assert a.status == "not_found" and a.claims == []


def test_eval_set_is_well_formed():
    path = os.path.join(os.path.dirname(__file__), "..", "eval", "rag_eval_set.json")
    qs = json.load(open(path, encoding="utf-8"))["questions"]
    assert len({q["id"] for q in qs}) == len(qs)
    for q in qs:
        assert set(q["accepted_status"]) <= set(rag.STATUSES)
        assert q["gold"] or "not_found" in q["accepted_status"] or \
            "requires_manufacturer_documentation" in q["accepted_status"]


def test_judge_failure_is_unverified_not_unsupported():
    out = rag.verify_claims([{"text": "x", "citations": [1]}], [chunk("a", 413, BODY_413)],
                            llm=lambda m, n: "thinking...")
    assert out[0]["verdict"] == "UNVERIFIED"


def test_general_only_coverage_downgrades_answered_and_records_why():
    gen = {"status": "answered", "summary": "The HPC is inspected with a strong light.",
           "claims": [{"text": "Inspect compressor inducers with a strong light.", "citations": [1]}],
           "gaps": "", "manufacturer_note": ""}
    llm = FakeLLM(gen, {"Inspect compressor inducers with a strong light.": "SUPPORTED",
                        "The HPC is inspected with a strong light.": "PARTIAL"}, coverage="general_only")
    a = rag.answer("¿Cómo se inspecciona el compresor de alta presión?", llm,
                   lambda q, n: [chunk("c413", 413, BODY_413)], "v2")
    assert a.status == "insufficient_evidence"
    assert a.gaps == "fake coverage"
    assert a.summary == ""                                  # PARTIAL summary is hidden, not reworded
    assert any("coverage check = general_only" in x for x in a.adjustments)
    assert any(r["reason"].startswith("summary") for r in a.removed_claims)


def test_numeric_request_withholds_values_and_requires_manufacturer():
    gen = {"status": "answered", "summary": "",
           "claims": [{"text": "Steel blades stages 1-4: 5/16 in radius, 1/32 in depth.", "citations": [1]},
                      {"text": "Repair limits are established by the manufacturer.", "citations": [1]}],
           "gaps": "", "manufacturer_note": ""}
    llm = FakeLLM(gen, {"Steel blades stages 1-4: 5/16 in radius, 1/32 in depth.": "SUPPORTED",
                        "Repair limits are established by the manufacturer.": "SUPPORTED"})
    a = rag.answer("¿Cuáles son los límites exactos de reparación de los álabes?", llm,
                   lambda q, n: [chunk("c414", 414, BODY_413)], "v2")
    assert a.status == "requires_manufacturer_documentation"
    assert [c["text"] for c in a.claims] == ["Repair limits are established by the manufacturer."]
    assert a.removed_claims[0]["verdict"] == "WITHHELD"


@pytest.mark.parametrize("q,expected", [
    ("¿Cuál es el par de apriete de los pernos del HPC?", True),
    ("¿Cada cuántos ciclos hay que hacer la inspección boroscópica?", True),
    ("What are the repair limits for steel blades?", True),
    ("¿Qué es un boroscopio?", False),
    ("¿Cómo se inspecciona el compresor de alta presión?", False)])
def test_asks_for_specific_values(q, expected):
    assert rag.asks_for_specific_values(q) is expected
