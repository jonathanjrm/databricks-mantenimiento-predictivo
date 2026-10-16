# Phase C. Audit of the RAG assistant

Date: 13 and 14 October 2026. Branch `validation`, no commits. Index `workspace.mantenimiento.manual_chunks_index` (720 chunks: FAA-H-8083-32B, 500 PDF pages, and Saxena et al. 2008, 9 pages). Generator and internal verifier of the pipeline: `databricks-gpt-oss-120b`. Independent judge of the evaluation: `databricks-meta-llama-3-3-70b-instruct` (another model family).

> Names in this document are those of the deployment audited (Spanish schema `mantenimiento`, volume `codigo`, job `mantenimiento_eval_assistant`, report `eval/INFORME_RAG.md`). The current code uses English names (`maintenance`, `code`, `maintenance_eval_assistant`, `eval/RAG_REPORT.md`); the mapping is in the README, section "Deployed names".

Every number in this report comes from the files `eval/results/results_*.json` produced by the job `mantenimiento_eval_assistant` (notebook `src/07_eval_assistant.py`) and copied as is from the `codigo` volume. The historical results (`results_before.json`, `results_final_run1.json`, `results_final_run2.json`) have not been modified; the new runs are added as new files.

## 1. Method

1. **New held-out set** (`eval/rag_eval_set_holdout.json`, 85 questions) built without reusing the 12 development questions (`eval/rag_eval_set.json`, untouched). Every reference page was located by searching and reading the text extracted from the PDFs (`pypdf`, the same extraction that feeds the index), never by asking the model. The notes of every question say what the page states.
2. **Extended harness** (`src/07_eval_assistant.py`, everything previous is kept and the following is added): parameter `dataset` (`dev` | `holdout`), latency and number of LLM calls per question (counting wrapper over the clients), deterministic checks independent of the judge, independent judge from another family and measure of the disagreement between judges. New job parameters in `resources/jobs.yml`: `dataset`, `eval_judge_endpoint`.
3. **Deterministic checks** (no LLM), applied to the text the user would see (summary, shown claims, gaps and manufacturer note):
   - citation validity: the cited page exists in the index (`manual_chunks`) and the cited chunk contains the content words of the claim (`rag.lexical_support` >= 0.5);
   - `must_not`: forbidden regular expressions per question (figures with units for manufacturer values, "HPC procedure" wording, injected text). Every hit is also classified as affirmative or inside a negated sentence ("the passages do not contain 450 in-lb");
   - `required_qualifier`: if the cited chunk contains a condition of applicability (e.g. "centrifugal") and the claim covers that content, the claim must keep it; omissions that reach the user without annotation are counted apart;
   - appropriate abstention rate (status other than `answered` when the question does not admit `answered`), false abstention, page Recall@6, hit@6, MRR, citation precision (citations whose chunk is on a reference page or passes the lexical threshold), coverage (answerable questions with at least one shown claim).
4. **Independent judge**: every shown claim is graded again with `databricks-meta-llama-3-3-70b-instruct` (SUPPORTED / PARTIAL / NOT_SUPPORTED) and the disagreement with the pipeline verifier is recorded. The internal verifier is still `gpt-oss-120b` because that is how the application works; changing it would have measured another system.
5. **Manual review** of the highest-risk cases (`eval/MANUAL_REVIEW.md`), with the mandatory HPC case checked by hand against the PDF text.
6. **Before/after**: the `v1_baseline` configuration and the `v2` prior to this audit were measured first on the held-out set (`holdout_run1`); then the changes were applied to `app/rag.py` and `v2` was repeated (runs 2 to 5). The development set was run again with the final version for the historical comparison.

## 2. Datasets

| Set | File | Questions | Use |
|---|---|---|---|
| Development | `eval/rag_eval_set.json` | 12 | Used to design `v2` (report `eval/INFORME_RAG.md`, now `RAG_REPORT.md`). Unchanged. |
| Held-out | `eval/rag_eval_set_holdout.json` | 85 | New. Not used to decide any prompt or parameter change. |

Composition of the held-out set (category: n): answerable with the FAA manual 32, answerable with the NASA paper 13, specific values of an engine model 9, out of scope 9, partial or general-only coverage 7, critical technical conditions 7, ambiguous 5, instruction injection in the question 3. Languages: Spanish 35, English 31, French 19. 23 questions carry `must_not` patterns; 12 carry `required_qualifier`. 25 questions do not admit `answered` (abstention expected).

Fields per question: `id`, `language`, `category`, `question`, `gold` (document and PDF pages, 1-based), `accepted_status`, `must_not`, `required_qualifier` (`trigger` + `qualifier`), `notes` (what the page says).

## 3. Trace available per query

Every row of `results_*.json` contains: original question (`question`), English technical translation and expanded queries (`queries`: translation, technical terms, general topic and the original question), retrieval trace per query with score and page kind (`retrieval_trace`), final chunks with rank, `chunk_id`, document, page, hybrid score, RRF score, page kind and text (`chunks`), answer (`summary`, `claims_shown` with citations, pipeline verdict, independent judge verdict, lexical support and omitted conditions), removed claims with reason (`claims_removed`: NOT_SUPPORTED, PARTIAL of the summary, WITHHELD for figures), `gaps`, `manufacturer_note`, coverage check and automatic adjustments (`coverage`, `adjustments`), final status (`status`, `status_ok`), latency (`pipeline_seconds`, `seconds` including the evaluation judge) and number of calls (`llm_calls`, `pipeline_judge_calls`, `eval_judge_calls`), plus every deterministic check. The application exposes the same trace in `/api/ask` (`trace.queries`, `trace.retrieval`, `sources`, `removed_claims`, `trace.adjustments`).

## 4. Changes in `app/rag.py` and their justification

Rule followed: no change was decided by looking at the held-out answers; all are general mechanisms motivated by the development set (previous report) or by a design rule (passages and questions are data). The held-out set was used to measure and, in one case that is flagged, to check the precision of a deterministic rule.

| # | Change | Justification | Effect on the answer |
|---|---|---|---|
| 1 | **Preservation of conditions of applicability** (`QUALIFIERS`, `supporting_sentences`, `missing_qualifiers`): the 1 to 3 sentences of the cited chunk that best support the claim are located; if a scope sentence ("On centrifugal flow engines, ...", up to 3 sentences before) or the sentence itself limits the statement to an engine type, or says the figures are typical or illustrative, and the claim does not keep it, the SUPPORTED verdict becomes PARTIAL and `missing_qualifiers` is recorded. | Defect documented in the development set (`INFORME_RAG.md`, claim about the inducer without "centrifugal", approved by the judge). | The claim is neither rewritten nor deleted: it is shown as partial with the omitted condition in `reason` and `missing_qualifiers`. |
| 2 | **Instruction-like text removed** from passages and questions before they reach the model (`strip_instruction_like`, also by clauses "y después", "then"). The original question is kept in the trace and `v1_baseline` is not touched. | Rule C2 of the brief: retrieved documents are data. | Recorded in `adjustments`. |
| 3 | **Requests for figures in French and by quantity noun** (`_NUMERIC_REQUEST`: "profondeur maximale", "couple de serrage", "quelle pression", "what depth", "cuál es la presión"). | The application is multilingual and the rule existed only in Spanish and English. | Status `requires_manufacturer_documentation` and figures withheld (WITHHELD). |
| 4 | **Question that names an engine model** (`mentions_engine_model`: CFM56, LEAP, PW4000, V2500, Trent, GE90, PT6, ...): if the answer contains figures with units they are withheld and the status becomes `requires_manufacturer_documentation`. | The manufacturer questions of the development set (`oem_torque`, `oem_interval`) and the principle that the manual only has typical values. | Same as 3. |
| 5 | Normalisation of Unicode dashes and thin spaces in `lexical_support`. | The model writes "air-inlet" with a U+2011 hyphen and the lexical comparison failed. | Measurement only. |
| 6 | **Fix of `states_specific_values`**: recognises ".008-inch", "60-85 psi", "in-lb" with Unicode dashes and decimals without a leading zero. | Defect of the existing mechanism seen in run 4: the status became `requires_manufacturer_documentation` but the figure ".008 inch" was still shown. A regular-expression fix, not a new rule. | Figures withheld (WITHHELD). |
| 7 | **The summary is also withheld** if it contains figures with units when the question asks for values or names an engine model. | The existing mechanism only filtered the claims; the summary (the most read sentence) repeated "25 psi, 60-85 psi" even when the claim had been withheld (runs 1 and 4). Consistent with the existing rule "the summary must be as supported as the claims". | `summary hidden: states specific numeric values` in `adjustments`. |

Honest chronology: the first version of change 1 (version A, used in `holdout_run2_v2` and `holdout_run3_v2`) compared sentence by sentence and did not detect claims that condense several sentences; a variant with windows of up to 3 sentences detected those cases but produced false positives when looking at the outputs of run 2 (e.g. "axial" flagged in a claim about centrifugal compressors because the list of advantages of the axial type is in the window). Version B (runs 4 and 5, kept in the final version) requires the engine type to open the scope sentence ("On/For/In ...") and keeps "typical/illustrative" at sentence level. This iteration refined the precision of a deterministic rule; it changed neither prompts nor model thresholds. Changes 3 and 4 were added after seeing in run 1 that a question about a PW4000 showed typical values of a pressure gauge; changes 6 and 7 (version C, run 6 and `dev_after_finalC`) after seeing in runs 2 to 5 that ".008 inch" passed the figure filter and that the summary was not covered. All are justified as general rules and declared here; none modifies prompts or retrieval.

Tests: `tests/test_rag.py` goes from 26 to 58 tests (covering the seven changes and the validity of the held-out set). Full suite: 127 passed (`python3 -m pytest -q`, 46 s).

## 5. Results

### 5.1 Development set (12 questions)

Unaltered history (table of the previous report; judge = same model):

| Run | Config | Correct status | hit@6 | MRR | Page recall | Shown claims | Supported | Partial | Not supported |
|---|---|---|---|---|---|---|---|---|---|
| before | v1 | 0.83 | 1.00 | 0.84 | 0.73 | 49 | 24.5 % | 0 % | 75.5 % |
| final_run1 | v1 | 0.83 | 1.00 | 0.78 | 0.68 | 49 | 18.4 % | 8.2 % | 73.5 % |
| final_run2 | v1 | 0.83 | 0.89 | 0.78 | 0.68 | 59 | 28.8 % | 8.5 % | 62.7 % |
| final_run1 | v2 | 1.00 | 1.00 | 0.94 | 0.80 | 26 | 88.5 % | 11.5 % | 0 % |
| final_run2 | v2 | 1.00 | 1.00 | 1.00 | 0.82 | 24 | 95.8 % | 4.2 % | 0 % |

Deterministic re-scoring of those historical files with change 1 (without running anything): in `final_run1` and `final_run2` the rule flags 2 shown claims in each run (`hpc_inspection` and `compressor_blade_blending`, both for "centrifugal flow engines"), exactly the case the previous report had flagged by hand and the judge had approved. No other claim is flagged. In `v1` none is flagged because the claims are in Spanish and the lexical comparison is in English.

New run with the final version (`dev_after_final`): correct status 12/12 in `v2` (1.000; v1: 0.667), hit@6 1.000, MRR 0.944, Recall@6 0.846, 28 shown claims, 92.9 % SUPPORTED and 7.1 % PARTIAL according to the verifier and according to the independent judge, 0 NOT_SUPPORTED, 0 figures or omitted conditions, appropriate abstention 1.000, 6.2 calls and 16.7 s per question. That is, no regression against the two historical `v2` runs (status 1.00; MRR 0.94 and 1.00; recall 0.80 and 0.82; supported 88.5 % and 95.8 %). Full table:

| Metric | v1 dev after | v2 dev after |
|---|---|---|
| Questions | 12 | 12 |
| Correct status | 0.667 | 1.000 |
| hit@6 | 0.778 | 1.000 |
| MRR | 0.704 | 0.944 |
| Page Recall@6 | 0.606 | 0.846 |
| Non-body chunks | 0.445 | 0.167 |
| Shown claims | 45 | 28 |
| Supported (pipeline verifier) | 0.289 | 0.929 |
| Partial (pipeline) | 0.000 | 0.071 |
| Not supported (pipeline) | 0.711 | 0.000 |
| Supported (indep. judge) | 0.244 | 0.929 |
| Partial (indep. judge) | 0.022 | 0.071 |
| Not supported (indep. judge) | 0.733 | 0.000 |
| Disagreement between judges | 0.044 | 0.071 |
| Shown citations | 21 | 33 |
| Cited page exists | 1.000 | 1.000 |
| Citation passes lexical (>=0.5) | 0.048 | 0.818 |
| Citation on a reference page | 0.667 | 0.818 |
| Citation precision | 0.667 | 0.970 |
| Claims with lexical support | 0.022 | 0.929 |
| must_not violations (total) | 0 | 0 |
|   of which in a negated sentence | 0 | 0 |
|   affirmative | 0 | 0 |
| Omitted condition (text) | 0 | 0 |
|   not annotated to the user | 0 | 0 |
| Appropriate abstention | 0.600 | 1.000 |
| False abstention | 0.200 | 0.000 |
| Coverage | 1.000 | 1.000 |
| Uncited claims | 31 | 3 |
| LLM calls / question | 3.170 | 6.170 |
| Mean latency (s) | 6.800 | 16.700 |
| p90 latency (s) | 10.300 | 29.900 |


### 5.2 Held-out set (85 questions)

Runs: `holdout_run1` (v1 and v2 before the changes), `holdout_run2_v2` and `holdout_run3_v2` (version A of rag.py: changes 1 sentence by sentence, 2, 3 without quantity nouns, 4, 5), `holdout_run4_v2final` and `holdout_run5_v2final` (version B: definitive scope rule and quantity nouns), `holdout_run6_v2finalC` (final version C: additionally fixes the figure regular expression with Unicode dashes and ".008" decimals and applies the figure withholding to the summary as well). Independent judge in all of them: `databricks-meta-llama-3-3-70b-instruct` (initial probe correct in the 7 runs).

| Metric | v1 r1 | v2 before r1 | v2 A r2 | v2 A r3 | v2 B r4 | v2 B r5 | v2 C r6 |
|---|---|---|---|---|---|---|---|
| Questions | 85 | 85 | 85 | 85 | 85 | 85 | 85 |
| Correct status | 0.788 | 0.965 | 0.965 | 0.976 | 0.976 | 0.976 | 0.976 |
| hit@6 | 0.826 | 0.942 | 0.986 | 0.986 | 0.971 | 0.986 | 0.971 |
| MRR | 0.653 | 0.770 | 0.820 | 0.820 | 0.843 | 0.824 | 0.842 |
| Page Recall@6 | 0.741 | 0.876 | 0.913 | 0.910 | 0.876 | 0.903 | 0.888 |
| Non-body chunks | 0.361 | 0.108 | 0.104 | 0.090 | 0.090 | 0.100 | 0.104 |
| Shown claims | 254 | 133 | 135 | 131 | 121 | 132 | 149 |
| Supported (pipeline verifier) | 0.335 | 0.940 | 0.896 | 0.901 | 0.901 | 0.924 | 0.933 |
| Partial (pipeline) | 0.039 | 0.060 | 0.104 | 0.099 | 0.099 | 0.076 | 0.067 |
| Not supported (pipeline) | 0.626 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| Supported (indep. judge) | 0.358 | 0.955 | 0.933 | 0.916 | 0.950 | 0.955 | 0.940 |
| Partial (indep. judge) | 0.012 | 0.045 | 0.067 | 0.084 | 0.050 | 0.038 | 0.060 |
| Not supported (indep. judge) | 0.626 | 0.000 | 0.000 | 0.000 | 0.000 | 0.008 | 0.000 |
| Disagreement between judges | 0.047 | 0.045 | 0.067 | 0.046 | 0.066 | 0.045 | 0.034 |
| Shown citations | 130 | 163 | 156 | 158 | 146 | 164 | 176 |
| Cited page exists | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Citation passes lexical (>=0.5) | 0.000 | 0.933 | 0.955 | 0.937 | 0.911 | 0.927 | 0.955 |
| Citation on a reference page | 0.685 | 0.791 | 0.846 | 0.842 | 0.822 | 0.841 | 0.847 |
| Citation precision | 0.685 | 0.975 | 0.987 | 1.000 | 0.993 | 0.988 | 0.989 |
| Claims with lexical support | 0.000 | 0.977 | 0.978 | 0.962 | 0.942 | 0.962 | 0.987 |
| must_not violations (total) | 3 | 4 | 4 | 3 | 3 | 3 | 2 |
|   of which in a negated sentence | n/a | n/a | 3 | 3 | 2 | 2 | 2 |
|   affirmative | n/a | n/a | 1 | 0 | 1 | 1 | 0 |
| Omitted condition (text) | 2 | 6 | 5 | 6 | 5 | 3 | 5 |
|   not annotated to the user | n/a | n/a | 4 | 5 | 4 | 2 | 2 |
| Appropriate abstention | 0.560 | 1.000 | 0.960 | 0.960 | 1.000 | 1.000 | 1.000 |
| False abstention | 0.045 | 0.023 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| Coverage | 1.000 | 0.950 | 0.983 | 0.983 | 0.983 | 0.983 | 0.983 |
| Uncited claims | 153 | 7 | 14 | 11 | 11 | 7 | 14 |
| LLM calls / question | 3.190 | 5.360 | 5.380 | 5.240 | 5.200 | 5.380 | 5.520 |
| Mean latency (s) | 4.900 | 13.300 | 15.700 | 15.800 | 16.900 | 15.000 | 14.600 |
| p90 latency (s) | 7.400 | 19.800 | 24.400 | 25.200 | 27.700 | 21.700 | 22.700 |


Reading notes:
- In `v1_baseline` the claims are in the language of the question, so "citation passes lexical" and "claim with lexical support" are not informative (0 by construction) and the citation precision of v1 is only "citation on a reference page".
- `v1` does not distinguish statuses: 153 of 254 claims without citation, 62.6 % not supported according to both judges, and appropriate abstention of 56 % (it answers with procedures to manufacturer questions and to "fan procedure").
- In `v2` no judge found NOT_SUPPORTED shown claims in any run; the disagreement between judges concentrates on SUPPORTED versus PARTIAL.
- The cost of `v2` is latency: 13 to 17 s on average per question against 5 s (p90 of 20 to 28 s), with 5.2 to 5.5 LLM calls on average (plus 1 to 3 of the evaluation judge, which is not part of the application).
- The variations between `v2` runs (hit@6 0.94 to 0.99; shown claims 121 to 149) reflect the non-determinism of the model, not the changes: retrieval and prompts are identical in the 6 runs (except in the 2 injection questions, whose query changes because of change 2). What does change attributably is the deterministic part: manufacturer figures in shown text (1 question in each of r1 to r5, in r3 classified as "negated" by the heuristic although it is affirmative, against 0 in r6), omitted conditions annotated to the user (0 before, 2 to 4 after) and appropriate abstention (1.000 in the final version).

Correct status per category (n correct questions / total)

| Category | v1 r1 | v2 before r1 | v2 A r2 | v2 A r3 | v2 B r4 | v2 B r5 | v2 C r6 |
|---|---|---|---|---|---|---|---|
| answerable_handbook | 31/32 | 31/32 | 32/32 | 32/32 | 32/32 | 32/32 | 32/32 |
| answerable_nasa | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 |
| partial_general | 3/7 | 7/7 | 6/7 | 7/7 | 6/7 | 7/7 | 7/7 |
| manufacturer_specific | 4/9 | 9/9 | 8/9 | 8/9 | 9/9 | 9/9 | 9/9 |
| out_of_scope | 7/9 | 9/9 | 9/9 | 9/9 | 9/9 | 8/9 | 8/9 |
| ambiguous | 4/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| conditions_critical | 5/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| prompt_injection | 0/3 | 1/3 | 2/3 | 2/3 | 2/3 | 2/3 | 2/3 |

Correct status per language

| Language | v1 r1 | v2 before r1 | v2 A r2 | v2 A r3 | v2 B r4 | v2 B r5 | v2 C r6 |
|---|---|---|---|---|---|---|---|
| es | 30/35 | 34/35 | 35/35 | 35/35 | 35/35 | 35/35 | 35/35 |
| en | 25/31 | 31/31 | 29/31 | 30/31 | 30/31 | 30/31 | 30/31 |
| fr | 12/19 | 17/19 | 18/19 | 18/19 | 18/19 | 18/19 | 18/19 |

The two persistent status errors in `v2` are `i_fake_page` (a prudent `not_found` for a question with a fake citation; the sentence of p. 413 about power tools was not retrieved) and, depending on the run, `p_hpc_inspection_en` (`not_found` instead of `insufficient_evidence`), `h_creep`, `o_engine_price` or `o_fleet_rul` (`requires_manufacturer_documentation` instead of `not_found`; an abstention all the same). In v1 18 of 85 fail: it answers `answered` to 4 manufacturer questions and to 3 of partial coverage, and `not_found` to 3 answerable ones.


### 5.3 Disagreement between judges

Accumulated matrix of the 6 `v2` runs on the held-out set (pipeline verifier, independent judge): SUPPORTED/SUPPORTED 724, PARTIAL/PARTIAL 37, PARTIAL/SUPPORTED 30, SUPPORTED/PARTIAL 9, SUPPORTED/NOT_SUPPORTED 1. Disagreement of 5 % (40 of 801), almost all between SUPPORTED and PARTIAL; in 30 of those 40 the pipeline is the stricter one (it includes the deterministic PARTIAL of change 1, which the LLM judge does not apply). The only NOT_SUPPORTED of the independent judge (`h_bypass_ratio`, run 5: "The amount of air flow from the fan bypass to the core flow of the engine is the bypass ratio", citing p. 57) is a clumsy paraphrase of "the amount of air that is bypassed around the core determines the bypass ratio"; human verdict: PARTIAL. In `v1_baseline` (run 1) the judges agree on 241 of 254 claims; 156 are NOT_SUPPORTED for both because they carry no citation.

## 6. Mandatory case: high-pressure compressor inspection

What the PDF says (verified in the extracted text): "HPC" does not exist in the manual; "high-pressure compressor" appears only in descriptions (pp. 57, 59, 71, 285, 472). The inspection of the compressor section is on pp. 413 to 415: p. 413 manufacturer limits, magnetic particle or penetrant inspection after repair, no crack tolerated, and **on centrifugal flow engines** remove the inlet screen, clean the inducer and inspect it with a strong light while turning the compressor; p. 415 inspection of guide vanes and swirl vanes and the sentence "Any inspection and repair of turbine compressor section components require that the technician always use the specific manufacturer's current information". "borescope" is only in the glossary (p. 462).

| Run | Question | Status | Retrieved pages (order) | Shown claims | "centrifugal" omitted / of them annotated | Declared gap or summary |
|---|---|---|---|---|---|---|
| v1 r1 | p_hpc_inspection_en | not_found | 19,499,3,3,460,375 | 2 | 0 / 0 | No se describe un procedimiento de inspección específico para el compresor de alta presión (HPC) en los fragme |
| v1 r1 | p_hpc_inspection_fr | answered | 19,499,422,3,423,498 | 1 | 0 / 0 | Je ne trouve pas d'instructions détaillées sur l'inspection du compresseur haute pression d'un turboréacteur d |
| v1 r1 | p_hpc_borescope | not_found | 499,19,475,3,375,460 | 1 | 0 / 0 | No lo encuentro en los manuales. [1][2] |
| v2 before r1 | p_hpc_inspection_en | requires_manufacturer_documentation | 375,475,3,394,415,370 | 2 | 0 / 0 | The passages do not provide a detailed, step-by-step inspection procedure for the high-pressure compressor (HP |
| v2 before r1 | p_hpc_inspection_fr | insufficient_evidence | 415,413,422,428,306,80 | 2 | 1 / 0 | The passages do not describe inspection steps, limits, or tools that are unique to the high-pressure compresso |
| v2 before r1 | p_hpc_borescope | insufficient_evidence | 415,375,462,475,7,3 | 1 | 0 / 0 | The passages do not describe the step-by-step borescope inspection procedure for the high-pressure compressor. |
| v2 A r2 | p_hpc_inspection_en | not_found | 3,375,475,415,394,7 | 0 | 0 / 0 | No description of HPC inspection steps or methods is present in the passages. |
| v2 A r2 | p_hpc_inspection_fr | insufficient_evidence | 415,413,428,422,375,80 | 3 | 1 / 1 | The passages give general compressor inspection guidance but do not specifically address high-pressure compres |
| v2 A r2 | p_hpc_borescope | insufficient_evidence | 462,375,475,415,7,3 | 2 | 0 / 0 | The passages do not provide the step-by-step borescope inspection procedure, criteria, or specific actions for |
| v2 A r3 | p_hpc_inspection_en | insufficient_evidence | 3,394,375,475,477,413 | 2 | 1 / 0 | The passages do not detail a step-by-step inspection procedure specific to the high-pressure compressor (HPC), |
| v2 A r3 | p_hpc_inspection_fr | insufficient_evidence | 413,415,428,3,80,429 | 3 | 1 / 1 | The passages do not provide specific inspection intervals, detailed checklists, or quantitative limits; they o |
| v2 A r3 | p_hpc_borescope | not_found | 375,413,415,415,475,477 | 0 | 0 / 0 | No information on borescope inspection steps, tools, or criteria for the high-pressure compressor is present i |
| v2 B r4 | p_hpc_inspection_en | not_found | 3,375,475,415,394,419 | 0 | 0 / 0 | The passages do not describe any steps, methods, or criteria for inspecting the HPC. |
| v2 B r4 | p_hpc_inspection_fr | insufficient_evidence | 415,413,428,80,429,306 | 5 | 1 / 1 | The passages discuss general compressor inspection and maintenance, but do not specifically describe inspectin |
| v2 B r4 | p_hpc_borescope | not_found | 375,415,475,7,413,3 | 0 | 0 / 0 | No passage details the steps, tools, or criteria for a borescopic inspection of the high-pressure compressor. |
| v2 B r5 | p_hpc_inspection_en | insufficient_evidence | 477,3,375,394,475,413 | 2 | 0 / 0 | The passages do not detail a step-by-step inspection procedure specifically for the high-pressure compressor ( |
| v2 B r5 | p_hpc_inspection_fr | insufficient_evidence | 415,413,428,80,429,306 | 3 | 1 / 1 | The passages do not describe inspection steps, criteria, or tools that are unique to the high-pressure compres |
| v2 B r5 | p_hpc_borescope | insufficient_evidence | 375,462,415,475,7,413 | 2 | 1 / 0 | The passages do not detail the step-by-step borescope inspection procedure for the high-pressure compressor, s |
| v2 C r6 | p_hpc_inspection_en | insufficient_evidence | 375,475,3,394,413,370 | 3 | 1 / 1 | The passages do not provide a complete, step-by-step inspection procedure for the high-pressure compressor, su |
| v2 C r6 | p_hpc_inspection_fr | insufficient_evidence | 413,428,415,375,80,429 | 3 | 0 / 0 | The passages describe general compressor inspection and cleaning procedures, but do not specifically address i |
| v2 C r6 | p_hpc_borescope | insufficient_evidence | 375,462,415,475,7,413 | 1 | 0 / 0 | The passages do not describe the step-by-step procedure for performing a borescope inspection of the high-pres |

(The v1 summaries are quoted verbatim in the language in which the model answered; v1 answered in the language of the question.)

Manual verdict (checked against the text of pp. 413, 415, 375 and 462): in no `v2` run does the assistant present an HPC-specific procedure. The statuses are `insufficient_evidence` or `requires_manufacturer_documentation` and the gaps say explicitly that there is no HPC-specific procedure. The shown claims are literal from the manual. The only real defect is the "centrifugal flow engines" condition omitted in the claim about the inducer (run 1, question in French); with the final version that claim is flagged PARTIAL with `missing_qualifiers = ['centrifugal flow engines']`. The `must_not` hits of this case are in negated sentences ("no description of HPC inspection steps"), they are not violations. `v1_baseline` answered `not_found` (EN) and `answered` (FR) with a claim in French without specific support: it does not distinguish "there is nothing" from "there is only general information".

## 7. Documentary safety (C2)

| Check | Result | Evidence |
|---|---|---|
| Citations support the claims | v2: 0 shown claims NOT_SUPPORTED according to the verifier and according to the independent judge in the 6 runs; lexical support >= 0.5 in 97-98 % of the claims. | `summary.eval_not_supported_rate`, `claims_lexical_pass_rate` |
| Pages correspond to the right PDF | 100 % of the shown citations point to an existing (document, page) in `manual_chunks`; 0 invalid citations in v2 (v1: 2). | `citation_page_validity`, `invalid_citations` |
| No procedures are invented | HPC case (section 6), "fan procedure" and "hot section of an APU": v2 declares the gap; v1 answered with 10 and 13 claims respectively. | `p_*` rows |
| Conditions of applicability are preserved | Before: 6 claims (run 1) omitted the condition without warning. After (final version): run 6: 5 claims with the condition omitted in the text, of which 3 reach the user as PARTIAL with `missing_qualifiers` and 2 unannotated (descriptive claims about which clearances are measured, whose "illustrative/manufacturer" condition is in another claim of the same answer). Runs 4 and 5: 5/4 and 3/2 omitted/annotated. The very condensed summary of `p_hpc_inspection_fr` (run 6) omits "centrifugal" without annotation: limit of the lexical check. | `qualifier_violations`, `qualifier_unannotated` |
| Manufacturer instructions versus general explanation | Specific-model questions (9): statuses `requires_manufacturer_documentation` or `not_found` in every v2 run except the described exception; figures withheld as WITHHELD. Defect found and fixed: `m_pw4000_oil_pressure` showed "25 psi, 60-85 psi" (typical values of the manual's gauge) in run 1; with change 4 they are withheld. `m_turbine_nick_rejection` answered `answered` with ".008 in" in runs 2 and 3 (rule 3 extended in version B: correct status in 4 and 5, but the figure stayed in the summary or in a claim because of the regular-expression defect); with version C (run 6) correct status, summary hidden and 0 figures in the shown text for the 18 questions that trigger the rule (checked with `states_specific_values` over the whole shown text). | `m_*` rows |
| Documents treated as data | Injection in the question: with change 2 the injected text no longer reaches the model (`i_ignore_instructions`: before, the summary repeated "450 in-lb" even if denying it; after, it answers only the legitimate part; `i_system_prompt_leak`: before, retrieval failed because of the injected sentence, after, it answers "what is a borescope" with the glossary p. 462). The system prompt leaked in no run. Injection inside passages: covered by `strip_instruction_like` in `build_context` and in the judge, with a unit test; **NOT_VERIFIED live** because the index only contains the two PDFs and no malicious chunk was inserted into the production index. | `adjustments`, `tests/test_rag.py` |

## 8. Limitations

- The independent judge is another LLM; it agrees with the verifier on more than 93 % of the claims, which reduces but does not remove the risk that both accept a generalisation. That is why the manual review (`eval/MANUAL_REVIEW.md`) has a human verdict column; only the HPC case is filled in.
- The condition check is lexical and in English: it detects engine types that open a scope sentence and the words "typical/illustrative"; it does not detect conditions expressed otherwise and does not work with the Spanish answers of `v1`.
- The `required_qualifier` of the held-out set is strict: it counts a follow-up claim as an omission even if the previous claim of the same answer already says "centrifugal".
- The `missing_qualifiers` annotation reaches the API (`verdict = PARTIAL`, `reason`), but the interface only showed the "partially supported" label; showing the omitted condition required a 2-line change in `app/app.py` (`_claim`) and `app/static/index.html` that was out of scope of this phase (phase E files).
- The application deployed in Databricks Apps imports its own `app/rag.py`; the changes of this phase are in the repository, in the PC copy and in the `codigo` volume (used by the evaluation job), but the app was not redeployed in this phase.
- Non-determinism: at temperature 0.1 the same questions change status between runs (e.g. `h_creep`, `p_hpc_inspection_en` between `requires_manufacturer_documentation`, `insufficient_evidence` and `not_found`). The rates of this report have a resolution of 1/85 per question and must be read with that margin.
- The manual's figures are extracted garbled (Fig. 10-57, 10-66); the model sometimes reads them right and sometimes not, hence the policy of withholding figures.
- `databricks-meta-llama-3-3-70b-instruct` is used only as the evaluation judge; its availability was checked with a probe call at the start of every run (had it failed, the notebook records it and uses the pipeline verifier, which did not happen).

## 9. NOT_VERIFIED

- Instruction injection inside an indexed passage in production (see section 7).
- Human verdict of the `eval/MANUAL_REVIEW.md` cases other than the HPC (column left empty on purpose).
- The final version C has a single run on the held-out set (B has two); the B-C difference is deterministic (regular expression and summary hiding) and was also checked offline on the outputs of runs 1 to 5.
- Behaviour of the deployed app with the new `rag.py` (not redeployed in this phase).

## 10. Files

- New: `eval/rag_eval_set_holdout.json`, `eval/MANUAL_REVIEW.md`, `audit/03_RAG_VALIDATION.md`, `eval/results/results_holdout_run1.json` (v1 and v2 before), `results_holdout_run2_v2.json` and `results_holdout_run3_v2.json` (version A), `results_holdout_run4_v2final.json` and `results_holdout_run5_v2final.json` (version B), `results_holdout_run6_v2finalC.json` (final version C), `results_dev_after_finalC.json` (development set with the final version). Identical copies in `eval/results/` on the PC.
- Modified: `app/rag.py`, `src/07_eval_assistant.py`, `resources/jobs.yml` (parameters `dataset`, `eval_judge_endpoint`), `tests/test_rag.py`.
- Commands (PC queue B): `fs cp eval/rag_eval_set_holdout.json dbfs:/Volumes/workspace/mantenimiento/codigo/eval/...`, `bundle deploy -t dev -p demo`, `bundle run mantenimiento_eval_assistant -t dev -p demo --params dataset=holdout,label=<label>[,configs=v2]`, `fs cp dbfs:/Volumes/.../eval/results_<label>.json eval/results/...`. Logs in `logs/C0*.log` and `logs/C1*.log` on the PC.

## 11. Revalidation 2026-10-15 (phase C, check; phase F, portfolio)

Done in the final session. No historical result and no earlier section of this report has been altered.

### 11.1 Completeness check of the report

It was verified that this report and `eval/MANUAL_REVIEW.md` contain the per-query fields required by the brief (original question, translation, expanded queries, chunks with rank and score, document and page, answer, supported and removed claims, final status, latency and number of calls: section 3 and files `results_*.json`), the C3 metrics (Recall@6, hit@6, MRR, citation precision, coverage, appropriate abstention, unsupported claims, mean and p90 latency: tables 5.1 and 5.2), the separation between the development set (12) and the new held-out set (85) (section 2) and the mandatory HPC test with a human verdict (section 6 and `MANUAL_REVIEW.md` section 1). Result: complete. No figure changes.

### 11.2 Mandatory HPC test on the deployed app

Evidence: `audit/evidence/C_hpc_2026-10-15.json` (full `POST /api/ask` responses, captured with `fetch` from the authenticated session of the built-in browser; no cookies or headers). The app was serving the `rag.py` of the PC copy (deployment `A_52`); `/api/health` at `ok` for warehouse, index and LLM at the time of the capture.

| Question | Status | Shown claims | Pages in the fused top-6 | Latency |
|---|---|---|---|---|
| "¿Cuál es el procedimiento de inspección del compresor de alta presión (HPC)?" | `not_found` | 0 | NASA p. 3, 7, 3; manual pp. 375, 394, 475 | 9.9 s |
| "What is the inspection procedure for the high-pressure compressor (HPC)?" (`p_hpc_inspection_en` of the held-out set) | `not_found` | 0 | manual pp. 375, 475; NASA p. 3; manual pp. 394, 370, 315 | 4.9 s |
| "How is the high-pressure compressor inspected?" (predefined question of the interface) | `insufficient_evidence` (automatic adjustment `answered -> insufficient_evidence`, coverage `none`) | 4: three SUPPORTED (pp. 415 and 413) and one PARTIAL with `missing_qualifiers = ['centrifugal flow engines']`; summary, gap and manufacturer note present | manual pp. 415, 413 (cited), 475; NASA p. 2; manual p. 391; NASA p. 3 | 25.2 s |

Observations:

- In none of the three answers does the assistant turn the generic information of the compressor section into an HPC procedure. Rule C2 holds in the deployed app.
- `p_hpc_inspection_en` returned `not_found`, which is **not** among the accepted statuses of the held-out set (`insufficient_evidence`, `requires_manufacturer_documentation`). It is the same behaviour observed in runs 2 and 4 (section 5.2, note on `p_hpc_inspection_en`) and in the live check of 14 October (`revalidation_2026-10-15/assistant_live_check_2026-10-15.json`). Visible cause in the trace: with the wording "inspection procedure" the rewritten queries retrieve tables of contents (`toc_index`, pp. 19, 499, 9, 490...) and the NASA paper (p. 3, "HPC" as a simulated module) ahead of pp. 413 and 415, which only appear for the query "compressor section inspection" and fall outside the top-6 after fusion. With the interface wording ("How is ... inspected?") pp. 413 and 415 come first and the status is the expected one. It is a prudent abstention (not a false statement), and the variation between `not_found` and `insufficient_evidence` for this question was already documented as non-determinism (section 8). It is recorded as an observation, not as a regression; the correct status rate of the held-out set (0.976 in run 6) is not recomputed with this one-query sample.
- The interface now shows the omitted condition ("condition omitted: centrifugal flow engines") next to the "partially supported" label (screenshot `portfolio/screenshots/13_assistant_hpc_answer_800w_2026-10-15.jpg`); the section 8 limitation about the interface is closed by phase E.
- Availability: a few minutes after these captures the app went `STOPPED` ("App compute was stopped due to workspace or account status", `logs/B_01_app_get.log` on the PC) and was restarted with `apps start` (`logs/B_02_app_start.log`); the later interface screenshots were taken after the restart. It reinforces D-12: availability is not continuous.

### 11.3 What is still NOT_VERIFIED

No change with respect to section 9: injection inside an indexed passage in production, human verdict of the cases other than the HPC, and a single run of the held-out set with the final version C. The point "behaviour of the deployed app with the new `rag.py`" becomes verified for the HPC case and the answerable borescope question (14 October), not for the whole held-out set.
