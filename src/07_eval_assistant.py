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
# MAGIC - **Deterministic checks** (no LLM): citation validity (cited source/page exists in the index),
# MAGIC   lexical support of each shown claim by its cited chunks, `must_not` regex violations in the shown
# MAGIC   text, `required_qualifier` preservation (a condition stated by the cited page, e.g. "centrifugal",
# MAGIC   must stay in the claim), appropriate abstention, citation precision, coverage, latency and LLM calls.
# MAGIC - **Independent judge**: a second LLM of a different family (`eval_judge_endpoint`) re-grades every
# MAGIC   shown claim, so the generator does not grade itself; its disagreement with the pipeline's own
# MAGIC   verifier is reported.
# MAGIC
# MAGIC Parameters: `dataset` = `dev` (eval/rag_eval_set.json, 12 questions used during development) or
# MAGIC `holdout` (eval/rag_eval_set_holdout.json, held-out set). `judge_endpoint` is the pipeline's own
# MAGIC verifier (what the app uses); `eval_judge_endpoint` is the independent grader.
# MAGIC
# MAGIC Limitation: LLM judges are screening tools, not ground truth. The highest-risk cases are reviewed by
# MAGIC hand against the PDF text (eval/MANUAL_REVIEW.md).
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
dbutils.widgets.text("dataset", "dev")
dbutils.widgets.text("eval_judge_endpoint", "databricks-meta-llama-3-3-70b-instruct")
dbutils.widgets.text("lexical_threshold", "0.5")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r}")
CONFIGS = [c.strip() for c in dbutils.widgets.get("configs").split(",") if c.strip()]
LLM_ENDPOINT = dbutils.widgets.get("llm_endpoint").strip()
JUDGE_ENDPOINT = dbutils.widgets.get("judge_endpoint").strip()
LABEL = dbutils.widgets.get("label").strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
DATASET = dbutils.widgets.get("dataset").strip() or "dev"
if DATASET not in ("dev", "holdout"):
    raise ValueError(f"dataset must be dev or holdout, got {DATASET!r}")
EVAL_JUDGE_ENDPOINT = dbutils.widgets.get("eval_judge_endpoint").strip()
LEXICAL_THRESHOLD = float(dbutils.widgets.get("lexical_threshold").strip() or 0.5)
EVAL_FILE = {"dev": "rag_eval_set.json", "holdout": "rag_eval_set_holdout.json"}[DATASET]
CODE = f"/Volumes/{CATALOG}/{SCHEMA}/codigo"
INDEX = f"{CATALOG}.{SCHEMA}.manual_chunks_index"
if CODE not in sys.path:
    sys.path.insert(0, CODE)

import rag  # noqa: E402  (app/rag.py, copied to the volume)
from databricks.sdk import WorkspaceClient  # noqa: E402

w = WorkspaceClient()


class Counted:
    """Wraps an llm callable and counts calls and seconds (per question, reset by the loop)."""

    def __init__(self, fn):
        self.fn, self.calls, self.seconds, self.errors = fn, 0, 0.0, 0

    def reset(self):
        self.calls, self.seconds, self.errors = 0, 0.0, 0

    def __call__(self, messages, max_tokens=800):
        self.calls += 1
        t = time.time()
        try:
            return self.fn(messages, max_tokens)
        except Exception:
            self.errors += 1
            raise
        finally:
            self.seconds += time.time() - t


_llm, search = rag.databricks_clients(w, LLM_ENDPOINT, INDEX)
_judge, _ = rag.databricks_clients(w, JUDGE_ENDPOINT, INDEX)
llm, judge = Counted(_llm), Counted(_judge)

# Independent grader: a different model family than the generator, so the model does not grade itself.
# If the endpoint is not usable in this workspace, fall back to the pipeline judge and record it.
_eval_judge_raw, _ = rag.databricks_clients(w, EVAL_JUDGE_ENDPOINT, INDEX)
try:
    _probe = _eval_judge_raw([{"role": "user", "content": "Reply with the single word OK."}], 20)
    EVAL_JUDGE_USED = EVAL_JUDGE_ENDPOINT
    print("independent judge probe:", EVAL_JUDGE_ENDPOINT, "->", _probe[:40])
except Exception as e:  # noqa: BLE001
    print(f"independent judge {EVAL_JUDGE_ENDPOINT} unavailable ({type(e).__name__}); using {JUDGE_ENDPOINT}")
    _eval_judge_raw, EVAL_JUDGE_USED = _judge, JUDGE_ENDPOINT
eval_judge = Counted(_eval_judge_raw)
EVAL_JUDGE_INDEPENDENT = EVAL_JUDGE_USED != LLM_ENDPOINT

eval_set = json.load(open(f"{CODE}/eval/{EVAL_FILE}", encoding="utf-8"))["questions"]
first = spark.sql(f"SELECT source, page, text FROM {CATALOG}.{SCHEMA}.manual_chunks WHERE chunk_idx = 0").collect()
kinds = rag.page_kind_map([r.asDict() for r in first])
KNOWN_PAGES = set(kinds)          # every (source, page) that exists in the index
print(len(eval_set), f"questions ({DATASET}) |", len(kinds), "pages classified | configs:", CONFIGS,
      "| generator:", LLM_ENDPOINT, "| pipeline judge:", JUDGE_ENDPOINT, "| independent judge:", EVAL_JUDGE_USED)

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


_NEGATION = re.compile(r"\b(not|no|without|never|n't|nor|sin|ningun[ao]?|nunca|ne|pas|aucun[e]?|jamais|does not|do not)\b", re.I)


def shown_text(a, shown):
    """Everything the user would read: summary, shown claims, gaps and manufacturer note."""
    return "\n".join([a.summary or ""] + [c["text"] for c in shown] + [a.gaps or "", a.manufacturer_note or ""])


def deterministic_checks(q, a, shown):
    """Checks that need no LLM. Returns a dict of per-question fields."""
    gold = gold_set(q)
    text = shown_text(a, shown)
    # must_not: forbidden patterns (numbers for manufacturer values, 'HPC procedure' wording, injected text)
    must_not_hits = []
    for pat in q.get("must_not") or []:
        m = re.search(pat, text, re.I)
        if m:
            # the sentence around the hit; a negated mention ("does not contain 450 in-lb") is reported apart
            sent = [x for x in re.split(r"(?<=[.!?])\s+|\n+", text) if m.group(0) in x]
            negated = bool(sent and _NEGATION.search(sent[0]))
            must_not_hits.append({"pattern": pat, "match": m.group(0)[:80], "negated": negated,
                                  "sentence": (sent[0] if sent else "")[:200]})
    # citations: cited chunk exists in the index, and its text lexically supports the claim
    cit_total = cit_valid_page = cit_lexical = cit_gold = cit_precise = 0
    claim_lexical_pass = 0
    for c in shown:
        texts = []
        for n in c.get("valid_citations") or c.get("citations") or []:
            if not (1 <= n <= len(a.chunks)):
                continue
            ch = a.chunks[n - 1]
            cit_total += 1
            texts.append(ch["text"])
            page_ok = (ch["source"], ch["page"]) in KNOWN_PAGES
            lex = rag.lexical_support(c["text"], ch["text"])
            in_gold = (ch["source"], ch["page"]) in gold
            cit_valid_page += page_ok
            cit_lexical += lex >= LEXICAL_THRESHOLD
            cit_gold += in_gold
            cit_precise += page_ok and (in_gold or lex >= LEXICAL_THRESHOLD)
        c["lexical_all_cited"] = rag.lexical_support(c["text"], " ".join(texts)) if texts else 0.0
        claim_lexical_pass += c["lexical_all_cited"] >= LEXICAL_THRESHOLD
    # required qualifier: when a cited chunk states the condition, the claim must keep it
    rq = q.get("required_qualifier")
    qualifier_checked = qualifier_violations = qualifier_unannotated = 0
    qualifier_details = []
    if rq:
        for c in shown:
            if not re.search(rq["trigger"], c["text"], re.I):
                continue
            cited = " ".join(a.chunks[n - 1]["text"] for n in (c.get("valid_citations") or []) if 1 <= n <= len(a.chunks))
            if re.search(rq["qualifier"], cited, re.I):
                qualifier_checked += 1
                if not re.search(rq["qualifier"], c["text"], re.I):
                    qualifier_violations += 1          # the claim text itself drops the condition
                    qualifier_details.append(c["text"][:160])
                    # rag.py may have flagged it (verdict PARTIAL + missing_qualifiers); if not, nothing warns the user
                    if not (c.get("missing_qualifiers") and c.get("verdict") == "PARTIAL"):
                        qualifier_unannotated += 1
    accepted = set(q["accepted_status"])
    abstention_expected = "answered" not in accepted
    abstained = a.status != "answered"
    return {
        "must_not_violations": len(must_not_hits), "must_not_hits": must_not_hits,
        "must_not_negated": sum(1 for h in must_not_hits if h["negated"]),
        "citations_shown": cit_total, "citations_valid_page": cit_valid_page,
        "citations_lexical_pass": cit_lexical, "citations_in_gold": cit_gold, "citations_precise": cit_precise,
        "claims_lexical_pass": claim_lexical_pass,
        "qualifier_checked": qualifier_checked, "qualifier_violations": qualifier_violations,
        "qualifier_unannotated": qualifier_unannotated, "qualifier_details": qualifier_details,
        "abstention_expected": abstention_expected, "abstained": abstained,
        "abstention_ok": (abstained if abstention_expected else None),
        "false_abstention": (abstained and accepted == {"answered"}),
        "has_shown_claims": bool(shown),
    }


def independent_verdicts(shown, chunks):
    """Re-grades the shown claims with the independent judge and compares with the pipeline verdict."""
    if not shown:
        return [], 0, 0
    graded = rag.verify_claims([{"text": c["text"], "citations": c.get("valid_citations") or c.get("citations") or []}
                                for c in shown], chunks, eval_judge)
    agree = disagree = 0
    for c, g in zip(shown, graded):
        c["eval_verdict"], c["eval_reason"] = g["verdict"], g.get("reason", "")
        if c.get("verdict") in ("SUPPORTED", "PARTIAL", "NOT_SUPPORTED") and g["verdict"] in ("SUPPORTED", "PARTIAL", "NOT_SUPPORTED"):
            if c["verdict"] == g["verdict"]:
                agree += 1
            else:
                disagree += 1
    return [g["verdict"] for g in graded], agree, disagree


rows = []
for cfg in CONFIGS:
    for q in eval_set:
        t0 = time.time()
        llm.reset(); judge.reset(); eval_judge.reset()
        a = rag.answer(q["question"], llm, search, cfg, judge=judge, kinds=kinds)
        pipeline_seconds = round(time.time() - t0, 1)
        # Grounding is scored on what the user would see. v2 already verified its claims;
        # v1 claims are judged here only for scoring (its answer is not modified).
        shown = a.claims if rag.CONFIGS[cfg]["verify"] else rag.verify_claims(a.claims, a.chunks, judge)
        generated = shown + a.removed_claims
        verdicts = [c.get("verdict") for c in shown]
        gen_verdicts = [c.get("verdict") for c in generated]
        pipeline_calls, pipeline_judge_calls = llm.calls + judge.calls, judge.calls
        det = deterministic_checks(q, a, shown)
        ev, ev_agree, ev_disagree = independent_verdicts(shown, a.chunks)
        rows.append({
            "label": LABEL, "config": cfg, "id": q["id"], "question": q["question"],
            "dataset": DATASET, "language": q.get("language"), "category": q.get("category"),
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
            "coverage": a.coverage, "adjustments": a.adjustments,
            "raw": a.raw, "seconds": round(time.time() - t0, 1),
            "pipeline_seconds": pipeline_seconds, "llm_calls": pipeline_calls,
            "pipeline_judge_calls": pipeline_judge_calls, "llm_seconds": round(llm.seconds + judge.seconds, 1),
            "llm_errors": llm.errors + judge.errors,
            "eval_judge_calls": eval_judge.calls,
            "eval_supported": ev.count("SUPPORTED"), "eval_partial": ev.count("PARTIAL"),
            "eval_not_supported": ev.count("NOT_SUPPORTED"), "eval_unverified": ev.count("UNVERIFIED"),
            "judge_agree": ev_agree, "judge_disagree": ev_disagree,
            **det,
        })
        r = rows[-1]
        print(f"{cfg:12s} {q['id']:28s} status={a.status:36s} ok={r['status_ok']!s:5s} hit={r['hit']!s:5s} "
              f"claims={len(shown)} unsupported_shown={verdicts.count('NOT_SUPPORTED')} eval_ns={r['eval_not_supported']} "
              f"must_not={r['must_not_violations']} qual={r['qualifier_violations']} calls={r['llm_calls']} s={r['seconds']}")

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
        # --- added: deterministic checks, independent judge, latency ---
        "recall_at_k": round(sum(r["gold_page_recall"] for r in ans) / max(1, len(ans)), 3),
        "citations_shown": sum(r["citations_shown"] for r in rs),
        "citation_page_validity": _rate(sum(r["citations_valid_page"] for r in rs), sum(r["citations_shown"] for r in rs)),
        "citation_lexical_pass_rate": _rate(sum(r["citations_lexical_pass"] for r in rs), sum(r["citations_shown"] for r in rs)),
        "citation_in_gold_rate": _rate(sum(r["citations_in_gold"] for r in rs), sum(r["citations_shown"] for r in rs)),
        "citation_precision": _rate(sum(r["citations_precise"] for r in rs), sum(r["citations_shown"] for r in rs)),
        "claims_lexical_pass_rate": _rate(sum(r["claims_lexical_pass"] for r in rs), n_claims),
        "must_not_questions": sum(1 for r in rs if r["id"] in MUST_NOT_IDS),
        "must_not_violations": sum(r["must_not_violations"] for r in rs),
        "must_not_violation_questions": sum(1 for r in rs if r["must_not_violations"]),
        "must_not_negated_hits": sum(r["must_not_negated"] for r in rs),
        "must_not_affirmative_hits": sum(r["must_not_violations"] - r["must_not_negated"] for r in rs),
        "qualifier_checked": sum(r["qualifier_checked"] for r in rs),
        "qualifier_violations": sum(r["qualifier_violations"] for r in rs),
        "qualifier_unannotated": sum(r["qualifier_unannotated"] for r in rs),
        "abstention_expected": sum(1 for r in rs if r["abstention_expected"]),
        "appropriate_abstention_rate": _rate(sum(1 for r in rs if r["abstention_ok"]), sum(1 for r in rs if r["abstention_expected"])),
        "false_abstention_rate": _rate(sum(1 for r in rs if r["false_abstention"]), sum(1 for r in rs if set(r["accepted_status"]) == {"answered"})),
        "coverage": _rate(sum(1 for r in rs if r["has_shown_claims"] and "answered" in r["accepted_status"]),
                          sum(1 for r in rs if "answered" in r["accepted_status"])),
        "eval_judge": EVAL_JUDGE_USED, "eval_judge_independent": EVAL_JUDGE_INDEPENDENT,
        "eval_supported_rate": _rate(sum(r["eval_supported"] for r in rs), n_claims),
        "eval_partial_rate": _rate(sum(r["eval_partial"] for r in rs), n_claims),
        "eval_not_supported_rate": _rate(sum(r["eval_not_supported"] for r in rs), n_claims),
        "eval_unverified": sum(r["eval_unverified"] for r in rs),
        "judge_disagreement_rate": _rate(sum(r["judge_disagree"] for r in rs), sum(r["judge_agree"] + r["judge_disagree"] for r in rs)),
        "llm_calls_mean": round(sum(r["llm_calls"] for r in rs) / len(rs), 2),
        "llm_errors": sum(r["llm_errors"] for r in rs),
        "latency_mean_s": round(sum(r["pipeline_seconds"] for r in rs) / len(rs), 1),
        "latency_p50_s": _pct([r["pipeline_seconds"] for r in rs], 0.5),
        "latency_p90_s": _pct([r["pipeline_seconds"] for r in rs], 0.9),
        "latency_max_s": max(r["pipeline_seconds"] for r in rs),
    }


def _rate(num, den):
    return round(num / den, 3) if den else None


def _pct(values, p):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None


MUST_NOT_IDS = {q["id"] for q in eval_set if q.get("must_not")}


summary = {cfg: summarize([r for r in rows if r["config"] == cfg]) for cfg in CONFIGS}
print(json.dumps(summary, indent=2))
out = {"label": LABEL, "dataset": DATASET, "eval_file": EVAL_FILE, "llm_endpoint": LLM_ENDPOINT,
       "judge_endpoint": JUDGE_ENDPOINT, "eval_judge_endpoint": EVAL_JUDGE_USED,
       "eval_judge_independent": EVAL_JUDGE_INDEPENDENT, "lexical_threshold": LEXICAL_THRESHOLD,
       "index": INDEX, "summary": summary, "rows": rows}
path = f"{CODE}/eval/results_{LABEL}.json"
with open(path, "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
print("Saved", path)
