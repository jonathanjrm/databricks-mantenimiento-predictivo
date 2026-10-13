import pytest

from lib import retrieval as R


def test_recall_y_mrr():
    assert R.recall_at_k(["a", "b", "c"], {"c"}, 3) == 1.0
    assert R.recall_at_k(["a", "b", "c"], {"c"}, 2) == 0.0
    assert R.reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert R.reciprocal_rank(["a"], {"z"}) == 0.0


def test_evaluate_retrieval():
    res = [["a", "b"], ["x", "y"], ["q", "r"]]
    rel = [{"a"}, {"y"}, {"zzz"}]
    m = R.evaluate_retrieval(res, rel, ks=(1, 2))
    assert m["recall_at_1"] == pytest.approx(1 / 3)
    assert m["recall_at_2"] == pytest.approx(2 / 3)
    assert m["mrr"] == pytest.approx((1 + 0.5 + 0) / 3)
    assert R.evaluate_retrieval([], [])["n_queries"] == 0
    with pytest.raises(ValueError):
        R.evaluate_retrieval([["a"]], [])


def test_pregunta_extractiva_usa_terminos_raros():
    chunks = ["La bomba de aceite del motor debe revisarse cada 500 horas de servicio.",
              "El motor se arranca con el procedimiento normal del manual.",
              "El motor y el motor y el motor repiten palabras comunes del motor."]
    df = R.doc_frequency(chunks)
    q = R.extractive_question(chunks[0], df, len(chunks))
    assert "bomba" in q and "aceite" in q
    assert len(q.split()) <= 8
