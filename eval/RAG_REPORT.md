# Report: accuracy and traceability of the RAG assistant

Date: 13 October 2026. Index `workspace.mantenimiento.manual_chunks_index` (720 chunks: 703 from the FAA Powerplant Handbook FAA-H-8083-32B and 17 from the NASA paper by Saxena et al., 2008). LLM and judge: `databricks-gpt-oss-120b`. Names are those of the first deployment (Spanish schema); see "Deployed names" in the README.

## 1. Case analysed: "How is the high-pressure compressor inspected?"

### What the manual really contains (verified in the text extracted from the PDF, not with the model)

| Search in the text | PDF pages |
|---|---|
| "HPC" (acronym) | none |
| "high-pressure compressor" near "inspect" | none |
| "high-pressure compressor" in any context | 57, 59, 71, 285 (description of shafts and sections), 472 (glossary: N2) |
| "compressor blade" near "inspect" | 413, 418 |
| "compressor section" near "inspect" | 415, 499 (index) |
| "borescope" | 462 (glossary) |

Conclusion: **the manual has no inspection procedure specific to the high-pressure compressor.** It covers the inspection of the compressor section in general (PDF pages 413 to 415, printed pages 10-45 to 10-47):

- p.413: removal of minor blade damage is admissible only within the manufacturer's limits; after repair, magnetic particle or fluorescent penetrant inspection; no crack is tolerated; on centrifugal flow engines the inlet screen is removed and the inducer is inspected with a strong light.
- p.414: figure 10-57 with *typical* repair limits (the text extracted from the figure is garbled).
- p.415: inspection of guide vanes and swirl vanes, and the sentence "Any inspection and repair of turbine compressor section components require that the technician always use the specific manufacturer's current information for evaluation and limits of repairs".

### Before (configuration `v1_baseline`, the one in production)

- **Generated English query:** "high pressure compressor inspection borescope visual examination HP compressor maintenance FAA Powerplant Handbook NASA turbine degradation article synonyms: compressor stage inspection, engine health monitoring, turbine wear analysis". The query drags document names and generic synonyms that add noise.
- **Retrieved chunks (top 6, hybrid score):**
  - p.329, 0.984: spectrometric oil analysis.
  - p.499, 0.976: book index.
  - NASA p.5, 0.933: degradation formula.
  - p.415, 0.926: the only relevant one.
  - p.475, 0.902: glossary.
  - p.460, 0.873: glossary.

  In total, 1 of 6 chunks was useful.
- **Answer:** "To inspect the high-pressure compressor these basic steps must be followed" and 4 steps taken from p.415.
  - The cited sentences exist on p.415, but they are presented as the HPC procedure, which the manual does not say.
  - 6 of the 10 sentences had no citation.
  - The implicit status was "answered".
- **Instability:** in two other identical runs the same question returned "I cannot find it in the provided manuals" and an answer citing p.415. There is no way to tell "not found" from "only general information".

### After (configuration `v2`)

- **Generated queries:** "How is the high-pressure compressor inspected?", "HPC inspection procedure" or "How to inspect the HPC (high-pressure compressor)?", "compressor section inspection", plus the original question.
- **Chunks:**
  - p.413 and p.415 in positions 1 and 2 in both runs.
  - Index and front matter removed.
  - Chunks of little use still get in (glossary p.475 and p.477, NASA p.2 and p.3, p.391) in positions 3 to 6.
- **Status:** `insufficient_evidence` in both runs.
  - In the first one the model returned "answered" and the coverage check corrected it (`general_only`). The adjustment is recorded in the trace.
  - Declared gaps: "The passages describe general compressor inspection... not specifically the high-pressure compressor".
  - Manufacturer note: quotes the sentence of p.415.
- **Shown claims:** 2 or 3, all verified against p.413 and p.415.

### Do the cited pages support the answer? (manual review)

| Claim (v2) | Page | Manual verdict |
|---|---|---|
| Repaired parts: magnetic particle or fluorescent penetrant | 413 | Literally supported |
| Follow the manufacturer's current information for evaluation and limits | 415 | Literally supported |
| Remove the screen, clean the inducer, strong light, turn the compressor and look for cracks | 413 | **Partial.** The manual says it for centrifugal flow engines and the claim omits that condition. The automatic judge accepted it as supported even after tightening its rule. This is a known limitation. |

## 2. Metrics: before and after

Set of 12 questions (`eval/rag_eval_set.json`) with reference pages checked in the PDF text and accepted statuses. `v1_baseline` was repeated in every run because the model is not deterministic.

| Run | Config | Correct status | hit@6 | MRR | Page recall | Shown claims | Supported | Partial | Not supported | Uncited |
|---|---|---|---|---|---|---|---|---|---|---|
| before | v1 | 0.83 | 1.00 | 0.84 | 0.73 | 49 | 24.5 % | 0 % | 75.5 % | 26 |
| final_run1 | v1 | 0.83 | 1.00 | 0.78 | 0.68 | 49 | 18.4 % | 8.2 % | 73.5 % | 35 |
| final_run2 | v1 | 0.83 | 0.89 | 0.78 | 0.68 | 59 | 28.8 % | 8.5 % | 62.7 % | 37 |
| final_run1 | **v2** | **1.00** | 1.00 | **0.94** | **0.80** | 26 | **88.5 %** | 11.5 % | **0 %** | 2* |
| final_run2 | **v2** | **1.00** | 1.00 | **1.00** | **0.82** | 24 | **95.8 %** | 4.2 % | **0 %** | 3* |

\* In v2 the "uncited" ones are removed summaries that appear in the removed list, not shown claims.

Uncited sentences count as not supported. Among the cited sentences of v1 alone, the judge supports 64 % and 77 % in the two final runs.

The manufacturer and out-of-scope questions:
- **v1:** always answered "I cannot find it", both when there was nothing and when the manual defers to the manufacturer. The repair limits question came out as "answered" in the 3 runs and the HPC one in 2 of 3.
- **v2:** distinguishes `not_found` (price of a blade), `requires_manufacturer_documentation` (CFM56-7B torque, PW1100G borescope interval, exact limits of figure 10-57) and `insufficient_evidence` (HPC inspection).

## 3. What changed (file `app/rag.py`)

1. **Queries:** 3 English queries (translation, technical terms and general topic) plus the original, fused with Reciprocal Rank Fusion.
2. **Page filter:** index, table of contents and front matter are discarded. Pages are classified from their first chunk; the glossary is kept.
3. **Structured answer:** `status`, `summary`, `claims` with citations, `gaps` and `manufacturer_note`, always in English.
4. **Verification of every claim and of the summary** with a judge. Whatever is not supported or not verifiable is removed and listed apart; it is never rewritten.
5. **Coverage check** (specific / general only / none). If the model says "answered" with general coverage, the status becomes `insufficient_evidence` and it is recorded.
6. **Numeric values:** if the question asks for limits, torques or intervals, the status is `requires_manufacturer_documentation` and the figures are withheld (verdict `WITHHELD`). The manual's figures are typical and the text of its figures is extracted garbled.
7. **Traceability in the app:** queries, chunks with score and page kind, verdict per claim, removed claims, coverage check and automatic adjustments.

Tests: `tests/test_rag.py` (26 tests with fake clients) and the Databricks harness `src/07_eval_assistant.py` (job `maintenance_eval_assistant`, formerly `mantenimiento_eval_assistant`). Full results, with every chunk and verdict, in `eval/resultados/`.

## 4. Limitations

- The judge is the same model that answers. It is a filter, not ground truth. The omitted-condition case ("centrifugal flow engines") shows that it can approve claims that generalise.
- 12 questions and 2 runs per configuration: the percentages are indicative, not statistically solid.
- Positions 3 to 6 still bring chunks of little use. The `gte-large-en` embedding model is English only and some figure text is garbled.
- v2 makes more LLM calls (about 5 to 10 per question), so it is slower.
