# Databricks notebook source
# MAGIC %md
# MAGIC # 07. Evaluation of the maintenance assistant (retrieval and citation grounding)
# MAGIC
# MAGIC Runs the evaluation set (`eval/rag_eval_set.json`) through one or more pipeline
# MAGIC configurations of `app/rag.py` (`v1_baseline` = behaviour before the changes, `v2` = after)
# MAGIC with the SAME index, LLM endpoint and judge, and records:
# MAGIC
# MAGIC - **Retrieval**: hit@k (a gold page among the k passages given to the model), MRR of the
# MAGIC   first gold passage, share of gold pages retrieved, share of non-body passages
# MAGIC   (table of contents, index, front matter, glossary).
# MAGIC - **Grounding**: every claim (v1: every sentence) is checked against the passages it cites by an
# MAGIC   LLM judge (SUPPORTED / PARTIAL / NOT_SUPPORTED) plus a lexical overlap signal.
# MAGIC - **Status**: whether the answer type is one of the accepted statuses for the question.
# MAGIC
# MAGIC Limitation: the judge is the same LLM family that writes the answers, so it is a screening
# MAGIC tool, not ground truth. The case study of the report is checked by hand against the PDF text.
# MAGIC
# MAGIC Code comes from the `codigo` volume (workaround for the workspace files I/O error).

# COMMAND ----------

import json
import re
import sys
import time
from datetime import datetime, timezone

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "mantenimiento")
dbutils.widgets.text("configs", "v1_baseline,v2")
dbutils.widgets.text("llm_endpoint", "databricks-gpt-oss-120b")
dbutils.widgets.text("judge_endpoint", "databricks-gpt-oss-120b")
dbutils.widgets.text("label", "")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r}")
CONFIGS = [c.strip() for c in dbutils.widgets.get("configs").split(",") if c.strip()]
LLM_ENDPOINT = dbutils.widgets.get("llm_endpoint").strip()
JUDGE_ENDPOINT = dbutils.widgets.get("judge_endpoint").strip()
LABEL = dbutils.widgets.get("label").strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
CODE = f"/Volumes/{CATALOG}/{SCHEMA}/codigo"
INDEX = f"{CATALOG}.{SCHEMA}.manual_chunks_index"
if CODE not in sys.path:
    sys.path.insert(0, CODE)

import rag  # noqa: E402  (app/rag.py, copied to the volume)
from databricks.sdk import WorkspaceClient  # noqa: E402

w = WorkspaceClient()
llm, search = rag.databricks_clients(w, LLM_ENDPOINT, INDEX)
judge, _ = rag.databricks_clients(w, JUDGE_ENDPOINT, INDEX)
eval_set = json.load(open(f"{CODE}/eval/rag_eval_set.json", encoding="utf-8"))["questions"]
first = spark.sql(f"SELECT source, page, text FROM {CATALOG}.{SCHEMA}.manual_chunks WHERE chunk_idx = 0").collect()
kinds = rag.page_kind_map([r.asDict() for r in first])
print(len(eval_set), "questions |", len(kinds), "pages classified | configs:", CONFIGS)

# COMMAND ----------

def gold_set(q):
    return {(g["source"], p) for g in q["gold"] for p in g["pages"]}


def score_retrieval(q, chunks):
    gold = gold_set(q)
    if not gold:
        return {"hit": None, "rr": None, "gold_page_recall": None}
    ranks = [i for i, c in enumerate(chunks, 1) if (c["source"], c["page"]) in gold]
    found = {(c["source"], c["page"]) for c in chunks} & gold
    return {"hit": bool(ranks), "rr": (1.0 / ranks[0]) if ranks else 0.0,
            "gold_page_recall": round(len(found) / len(gold), 3)}


rows = []
for cfg in CONFIGS:
    for q in eval_set:
        t0 = time.time()
        a = rag.answer(q["question"], llm, search, cfg, judge=judge, kinds=kinds)
        # Grounding is scored on what the user would see. v2 already verified its claims;
        # v1 claims are judged here only for scoring (its answer is not modified).
        shown = a.claims if rag.CONFIGS[cfg]["verify"] else rag.verify_claims(a.claims, a.chunks, judge)
        generated = shown + a.removed_claims
        verdicts = [c.get("verdict") for c in shown]
        gen_verdicts = [c.get("verdict") for c in generated]
        rows.append({
            "label": LABEL, "config": cfg, "id": q["id"], "question": q["question"],
            "queries": a.queries, "status": a.status,
            "status_ok": a.status in q["accepted_status"], "accepted_status": q["accepted_status"],
            **score_retrieval(q, a.chunks),
            "non_body_share": round(sum(1 for c in a.chunks if c.get("kind") != "body") / max(1, len(a.chunks)), 3),
            "chunks": [{"n": i, "chunk_id": c["chunk_id"], "source": c["source"], "page": c["page"],
                        "score": c.get("score"), "rrf": c.get("rrf"), "kind": c.get("kind"),
                        "text": c["text"]} for i, c in enumerate(a.chunks, 1)],
            "retrieval_trace": a.retrieval_trace,
            "claims_shown": shown, "claims_removed": a.removed_claims,
            "n_claims_shown": len(shown),
            "shown_supported": verdicts.count("SUPPORTED"), "shown_partial": verdicts.count("PARTIAL"),
            "shown_not_supported": verdicts.count("NOT_SUPPORTED"),
            "shown_unverified": verdicts.count("UNVERIFIED"),
            "generated_claims": len(generated),
            "generated_not_supported": gen_verdicts.count("NOT_SUPPORTED"),
            "generated_unverified": gen_verdicts.count("UNVERIFIED"),
            "cited_judged": sum(1 for c in shown if c.get("valid_citations") and c.get("verdict") != "UNVERIFIED"),
            "cited_supported": sum(1 for c in shown if c.get("valid_citations") and c.get("verdict") == "SUPPORTED"),
            "uncited_claims": sum(1 for c in generated if not c.get("citations")),
            "invalid_citations": sum(len(c.get("invalid_citations", [])) for c in generated),
            "summary": a.summary, "gaps": a.gaps, "manufacturer_note": a.manufacturer_note,
            "raw": a.raw, "seconds": round(time.time() - t0, 1),
        })
        print(f"{cfg:12s} {q['id']:26s} status={a.status:36s} ok={rows[-1]['status_ok']!s:5s} "
              f"hit={rows[-1]['hit']!s:5s} claims={len(shown)} unsupported_shown={verdicts.count('NOT_SUPPORTED')}")

# COMMAND ----------

def summarize(rs):
    ans = [r for r in rs if r["hit"] is not None]
    n_claims = sum(r["n_claims_shown"] for r in rs)
    return {
        "questions": len(rs),
        "status_accuracy": round(sum(r["status_ok"] for r in rs) / len(rs), 3),
        "hit_at_k": round(sum(r["hit"] for r in ans) / len(ans), 3),
        "mrr": round(sum(r["rr"] for r in ans) / len(ans), 3),
        "gold_page_recall": round(sum(r["gold_page_recall"] for r in ans) / len(ans), 3),
        "non_body_share": round(sum(r["non_body_share"] for r in rs) / len(rs), 3),
        "claims_shown": n_claims,
        "shown_supported_rate": round(sum(r["shown_supported"] for r in rs) / max(1, n_claims), 3),
        "shown_partial_rate": round(sum(r["shown_partial"] for r in rs) / max(1, n_claims), 3),
        "shown_not_supported_rate": round(sum(r["shown_not_supported"] for r in rs) / max(1, n_claims), 3),
        "shown_unverified": sum(r["shown_unverified"] for r in rs),
        "cited_claims_supported_rate": round(sum(r["cited_supported"] for r in rs) / max(1, sum(r["cited_judged"] for r in rs)), 3),
        "generated_claims": sum(r["generated_claims"] for r in rs),
        "generated_not_supported": sum(r["generated_not_supported"] for r in rs),
        "uncited_claims": sum(r["uncited_claims"] for r in rs),
        "invalid_citations": sum(r["invalid_citations"] for r in rs),
    }


summary = {cfg: summarize([r for r in rows if r["config"] == cfg]) for cfg in CONFIGS}
print(json.dumps(summary, indent=2))
out = {"label": LABEL, "llm_endpoint": LLM_ENDPOINT, "judge_endpoint": JUDGE_ENDPOINT,
       "index": INDEX, "summary": summary, "rows": rows}
path = f"{CODE}/eval/results_{LABEL}.json"
with open(path, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print("Saved", path)
