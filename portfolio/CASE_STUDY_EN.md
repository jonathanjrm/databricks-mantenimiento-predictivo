# Case study: AI-powered predictive maintenance on Databricks Free Edition

Project `databricks-mantenimiento-predictivo`. Dataset NASA C-MAPSS FD001. Audited 13 to 15 October 2026.
Every number in this document is traced to an audit report or evidence file in `portfolio/SOURCES.md`.

This is a portfolio-grade, audited prototype built on a public simulation dataset. It is not a certified aeronautical
maintenance system and must not be used for real maintenance decisions.

## 1. The industrial problem

Turbofan engines do not fail on a calendar. They degrade: efficiency and flow margins of the compressor and turbine
modules drift with cycles until a limit is crossed. Fixed-interval maintenance replaces healthy parts and still misses the
unit that is about to fail. The question a maintenance engineer actually asks is "how many cycles are left on this unit,
and how much can I trust the number?", followed immediately by "what does the manual say I should inspect?".

Two things are usually missing from demos of this problem: an honest statement of the model's uncertainty, and a
documentation assistant that refuses to invent procedures. This project tries to deliver both.

## 2. Objective

Build, on Databricks Free Edition (serverless only, no paid resources), a complete and reproducible chain:

- a medallion data pipeline from the raw NASA text files to model features;
- a remaining-useful-life (RUL) regressor registered in MLflow and served on an endpoint;
- a control-room web application for the 100 test engines, with uncertainty, traceability and service health visible;
- a retrieval-augmented assistant over two PDFs (the FAA Powerplant Handbook, FAA-H-8083-32B, and Saxena et al. 2008,
  the paper that describes C-MAPSS) that cites page numbers and withholds what it cannot support;
- an independent validation of all of the above, with evidence files.

## 3. Architecture

Everything is declared as a Databricks Asset Bundle (`databricks.yml`, `resources/*.yml`) and deployed to the `dev`
target. Five stages:

1. Sources: Unity Catalog volumes for the raw C-MAPSS files, the two PDFs and the shared library code.
2. Lakeflow Declarative Pipeline (serverless): bronze (Auto Loader ingestion of 33,727 readings), silver (validated,
   constant sensors dropped), gold (85 features from backward-looking windows, the test ground truth and a health curve).
3. Jobs: `02_train` (HistGradientBoosting, MLflow run, Unity Catalog registry, alias `champion`), `03_score`
   (batch scoring of the last observed cycle of the 100 test engines with occlusion-based sensor contributions),
   `04_rag_index` (PDF chunking into 720 chunks and an AI Search hybrid index), `07_eval_assistant` (RAG evaluation job).
4. Serving: a CPU Small scale-to-zero model serving endpoint for the RUL model; Foundation Model APIs
   (`databricks-gpt-oss-120b` for rewriting, answering and claim verification; `databricks-meta-llama-3-3-70b-instruct`
   as an independent evaluation judge); a serverless SQL warehouse.
5. Databricks App: FastAPI backend plus a dependency-free SVG front end. Endpoints `/api/overview`, `/api/engines`,
   `/api/engines/{unit}/health`, `/api/ask`, `/api/health`.

A design decision worth stating explicitly: the app does not call the serving endpoint. It reads the batch scoring table
`gold_predicciones` and shows the model version and scoring time on screen. The endpoint is an independent inference path
that the audit used to cross-check every value the dashboard displays.

## 4. Dataset: NASA C-MAPSS FD001

FD001 is the simplest of the four C-MAPSS subsets: one operating condition, one failure mode (HPC degradation).
Verified counts: 20,631 training rows for 100 run-to-failure engines, 13,096 test rows for 100 truncated engines,
one RUL value per test engine, no nulls, contiguous cycles from 1. Engine identifiers repeat across train and test
(they are different engines), so every key is (`split`, `unit`); no sensor row is shared between the two splits.
Provenance is a public GitHub mirror of the NASA Ames repository; byte-for-byte identity with the NASA original was not
verifiable from the environment (recorded as NOT_VERIFIED), while the official counts and internal properties were.

## 5. Data engineering

- Bronze: the raw text files land as 33,727 readings via Auto Loader; the RUL file is kept as a single text row and
  unpacked in silver.
- Silver: expectations and type validation; seven sensors are dropped (six with zero variance plus `s6`, which only
  takes two quantised values) along with the constant `setting3`; 14 sensors remain.
- Gold: for each sensor, the value, mean and standard deviation over 5 and 10 cycles and the slope over 10 cycles,
  plus the cycle itself: 85 features. The RUL label is `max_cycle - cycle` clipped at 125, the standard practice for
  C-MAPSS; the audit found 0 discrepancies across the 20,631 training rows.
- Leakage checks: windows look backward only (40 truncation tests, 0 differences); the health indicator parameters are
  fitted on train only; the test RUL is never used in features.
- pandas and Spark produce the same features: 170 column sums agree to a maximum relative difference of 2.4e-9 and the
  last-cycle rows of all 100 test engines agree to 3.7e-10. That tiny difference matters later (section 6).

## 6. The predictive model

`HistGradientBoostingRegressor` (scikit-learn 1.7.2 in the serverless job): 300 iterations, learning rate 0.05,
31 leaves, 40 samples per leaf, L2 1.0, no early stopping, deterministic for given data and library version.

Results on the FD001 test set (last observed cycle of 100 engines):

| Model | RMSE (cycles) | MAE | R2 | PHM08 score |
|---|---|---|---|---|
| Constant mean | 43.07 | 35.90 | -0.074 | 33,629 |
| Cycle only, linear | 33.34 | 27.59 | 0.356 | 5,319 |
| Ridge, 85 features | 21.80 | 16.81 | 0.725 | 1,073 |
| HistGradientBoosting v1 (champion) | 18.44 | 13.48 | 0.803 | 655 |

Error by actual RUL band: 0 to 30 cycles (25 engines) RMSE 14.3 with a bias of +5.5; 31 to 60 (14) RMSE 8.5;
61 to 90 (15) RMSE 19.0; 91 to 125 (35) RMSE 17.8; 126 or more (11) RMSE 32.2 with a bias of -28.6, a direct
consequence of training with a label clipped at 125.

Near failure the model overestimates life. Among the 26 engines with an actual RUL of 35 or less, 6 are displayed as
`healthy` by the demo thresholds (critical <= 20, watch <= 35). This is the unsafe direction and is the main reason
every recommendation in the app is worded as "Demo estimate; requires engineering review".

Reproducibility: the champion was reproduced bit for bit (RMSE 18.441849882005343, identical PHM08 and MAE) by
retraining with scikit-learn 1.7.2 on the workspace's `gold_features`. The 19.10 documented in the original README came
from scikit-learn 1.9.1 on pandas-computed features; perturbing the features by 1e-9 relative noise moves the test RMSE
between 18.29 and 18.98. The honest statement is "RMSE 18.4 to 19.1 cycles depending on library version and numerical
precision", not a single number with four decimals.

## 7. MLflow and MLOps

- Registry: `workspace.mantenimiento.rul_model` version 1, status READY, alias `champion`; parameters `n_features 85`,
  `n_train_rows 20631`, `rul_clip 125`; metrics `rmse_test`, `mae_test`, `phm08_test`, `rmse_mejor_baseline`;
  signature of 85 required double inputs to a float64 vector; artefacts `MLmodel`, `model.pkl`, environment files.
- Serving: endpoint `dev_<user>_rul-model-dev`, CPU Small, scale to zero, pinned to version 1 (it does not follow the
  alias; promoting a new version requires changing one variable).
- End-to-end trace for engine 34 (actual RUL 7): `gold_features` row at cycle 203 -> endpoint `3.118922691130997`
  -> `gold_predicciones` 3.12 -> `/api/engines` 3.12 -> dashboard "3 cycles, critical". Repeated for all 100 engines in
  one call: 0 mismatches after rounding to two decimals, and the endpoint's RMSE against the ground truth equals the MLflow
  metric to the last decimal.
- Negative tests: the app returns an explicit 503 when the warehouse fails, keeps a null prediction null (no invented
  zero), returns 404 for an unknown engine and 422 for invalid input, and never calls the model. The endpoint rejects
  missing columns and non-numeric values but accepts nulls (an all-null row returns 120.66): finding D-02, open, medium.
- Promotion protocol: the original `02_train.py` compared `rmse_test` to decide the champion. That makes the FD001 test
  set a seen set (section 11). The training script now promotes by a by-engine GroupKFold validation computed on train
  only, with a margin of `std_folds / sqrt(k)`, and records the scikit-learn version. The new protocol is unit-tested and
  evaluated locally (HGB 19.21 vs Ridge 22.11 on the truncated validation) but has not yet been exercised in the workspace.
- CI: GitHub Actions validates and deploys the bundle with an OAuth service principal; the workflow was tightened so
  secrets reach only the jobs that deploy. Test suite: 142 tests, 0 skipped (67 on the published `main`).

## 8. The RAG assistant

Pipeline (`app/rag.py`, configuration `v2`): the question (Spanish, English or French) is rewritten into an English
technical query plus up to three expansions; hybrid retrieval on AI Search with reciprocal-rank fusion; a structured
answer (status, claims with citations, gaps, manufacturer note); a verification pass that re-grades every claim against
its cited chunk and removes unsupported ones; deterministic rules that withhold manufacturer-specific numeric values,
strip instruction-like text from passages and questions, and flag claims that drop an applicability condition such as
"on centrifugal flow engines".

Evaluation: the 12 development questions were kept intact and a new held-out set of 85 questions in three languages was
written by locating the reference pages in the extracted PDF text by hand (answerable, out of scope, ambiguous,
manufacturer-specific, partial coverage, critical conditions, prompt injection). An independent judge model from another
family re-grades every shown claim; deterministic checks do not depend on any LLM.

Held-out results, final version (run 6): correct status 97.6 % (v1 baseline 78.8 %); hit@6 0.97; MRR 0.84;
page Recall@6 0.89; citation precision 98.9 %; 0 shown claims rated NOT_SUPPORTED by either judge (v1: 62.6 %);
appropriate abstention 100 %; false abstention 0 %; coverage 98.3 %; 5.5 LLM calls and 14.6 s mean latency per
question (p90 22.7 s). Judge disagreement is 5 % and almost entirely SUPPORTED versus PARTIAL.

Mandatory test, high-pressure compressor inspection: the handbook contains no HPC-specific procedure, only generic
compressor-section inspection on pp. 413 to 415. In 9 of 9 reviewed answers the assistant did not turn that generic text
into an HPC procedure. Re-asked on the live app on 2026-10-15 it returned `not_found` for the Spanish and English
wordings of the question and `insufficient_evidence`, with the omitted "centrifugal flow engines" condition flagged in
the UI, for the preset question. The non-determinism between `not_found` and `insufficient_evidence` is a documented
limitation, not a regression.

## 9. The web application

A control room for the 100 test engines: KPI cards (engines, critical, watch, mean estimated RUL, test RMSE labelled
"seen set"), a lineage strip (bronze, silver, gold, model, predictions), an uncertainty strip (typical error, near-failure
bias, traceability to table, model version and scoring time), an isometric fleet where height is the estimated RUL and
colour the demo state, filters by criticality, an engine card with the prediction, the test ground truth, the advisory
text and the three sensors that most change the estimate (occlusion sensitivity, explicitly "not SHAP"), the health
curve with a clearly labelled illustrative projection, and the manuals assistant with status badges, per-claim
verdicts, citations, gaps, removed statements and a retrieval trace. Loading and error states are explicit; service
chips (SQL warehouse, index, LLM) are checked every minute; the layout is responsive down to 375 px; unauthenticated
requests get 401.

## 10. Verified results (summary)

- Data: counts, labels, windows and split separation verified against the official files; Spark and pandas agree.
- Model: champion v1 reproduced bit for bit; metrics backed by MLflow and by the scoring table; baselines measured.
- Serving: endpoint really invoked; 100 of 100 engines identical to the dashboard; one-row trace through every link.
- App: 25 negative tests pass; no silent fallback; advisory wording in 100 of 100 rows.
- RAG: 85-question held-out set, two judges, deterministic checks, manual review of the mandatory case, live recheck.
- Quality and security: 142 tests pass; no secrets in tree or history; pip-audit and bandit without critical findings;
  least-privilege service principal (USE SCHEMA, SELECT only).

## 11. Limitations (read these before the numbers)

- Test-set contamination. The FD001 test RMSE has been published, compared with baselines and used by the original
  champion rule. It is a seen set. 18.44 describes a frozen model on a known set; it is not an independent estimate of
  future error. A by-engine protocol on train only gives about 18.0 with a spread of 1.2 across 200 random truncations,
  and the new train-only promotion gate is implemented and tested but not yet run in the workspace.
- Uncertainty is not calibrated. RMSE is quoted as a typical error, never as a prediction interval. No interval was invented.
- Batch, not live. Predictions are batch scores at the last observed cycle of the test engines (`scored_at` shown in the
  app). There is no telemetry stream and no live inference; the serving endpoint exists but is not wired to the app.
- Free Edition constraints. Serverless only, no paid resources. The app auto-stops after idle time and the serving
  endpoint disappeared from the workspace between the two audit days (recreated with one `bundle deploy`). Continuous
  availability is not part of what was verified; a demo must start with `/api/health`.
- RAG limits. Only two PDFs are indexed; answers take 10 to 30 s; the generator and the verifier are the same model
  family (an independent judge is used only in evaluation); the condition check is lexical and English-only; the human
  verdict column of the manual review is filled in only for the HPC case; prompt injection inside an indexed passage was
  tested with unit tests, not against the production index.
- The endpoint accepts nulls (D-02). It only matters for direct callers; the scoring job validates inputs first.
- Not certified. This is a demonstration on simulation data and must not be used for maintenance decisions.

## 12. Future work

Run the train-only promotion protocol in the workspace and retire the test-set comparison entirely; calibrate
uncertainty (quantile or conformal regression on by-engine validation); wrap the served model in a `pyfunc` that
validates inputs; add FD003 as an out-of-distribution check; surface the omitted-condition text directly in every claim;
add rate limiting and CSP headers to the app; evaluate a second RAG generator family; record a demo video.

## 13. Author's role

Jonathan Rincon, industrial engineer, defined the architecture, the technical parameters (dataset, features, label
clipping, model family, thresholds, evaluation requirements, security and documentation rules) and the implementation
sequence, and reviewed each phase of the work: the code, the pipelines, the application, the evaluation harness, the
tests, the audit and these materials. The specifications, the acceptance criteria and the decisions were his.

Repository: `github.com/jonathanjrm/databricks-mantenimiento-predictivo`. The audited changes live in the reviewed
branch and are published with the author's commit and push; `main` holds the pre-audit version until then.
