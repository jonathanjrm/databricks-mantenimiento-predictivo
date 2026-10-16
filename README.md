# Predictive turbine maintenance with Databricks

Industrial engineering portfolio project: from turbofan sensor data (NASA C-MAPSS FD001) to a web control
room that shows the remaining useful life (RUL) of every engine, its state, an advisory recommendation and an
assistant that answers with verified citations from technical manuals in PDF.

Built for **Databricks Free Edition** (October 2026): everything is serverless, there are no classic clusters
and nothing is downloaded from the internet at run time except through `pip`. Non-commercial use, public data,
zero cost.

> **Disclaimer.** This is a prototype on simulation data. It is not certified for real aircraft maintenance nor
> for any operational decision. Every recommendation in the app carries the text "Demo estimate; requires
> engineering review".

## Results (as stated in the audit)

The figures come from `audit/` and each one has its evidence in `audit/evidence/`. There is no figure in this
README that is not there.

**RUL model** (`HistGradientBoostingRegressor`, 85 features, version 1 with the `champion` alias in Unity Catalog):

| Measure | Value | Source |
|---|---|---|
| RMSE on the last cycle of the 100 FD001 test engines (workspace, scikit-learn 1.7.2) | **18.4 cycles** (MAE 13.5; PHM08 655; R2 0.80) | `audit/02_MODEL_VALIDATION.md` B2.2, `evidence/champion_v1_metrics_from_gold_predicciones.json` |
| Same measure locally (scikit-learn 1.9.1, pandas features) | 19.10 cycles (PHM08 727) | `scripts/results_local.json`, reproducible with `python scripts/train_local.py` |
| Baselines on the same test set | constant mean 43.07; Ridge 21.80 | `audit/02_MODEL_VALIDATION.md` B2.2 |
| Engines near failure (actual RUL <= 30, n = 25) | RMSE 14.3; bias +5.5 cycles (tends to overestimate) | `audit/02_MODEL_VALIDATION.md` B2.2 |
| Validation by engine on train only (GroupKFold 5, random cut per engine), local | RMSE 19.21 (fold-to-fold deviation 3.14); Ridge 22.11 | `audit/02_MODEL_VALIDATION.md` B2.5, `evidence/validation_protocol_train_only.json` |
| Same validation computed by the training job in the workspace (scikit-learn 1.7.2, Spark features), 16 October | RMSE 19.72 (fold-to-fold deviation 2.05) | `gold_model_metrics` of schema `maintenance`; `audit/FINAL_READINESS_REPORT.md` §5 |
| Re-training in the English schema `maintenance` (16 October, same data, code and seed) | RMSE 18.441849882005343, MAE 13.478, PHM08 655.02; 100/100 predictions identical to the audited table | `audit/FINAL_READINESS_REPORT.md` §5 |

Two caveats the audit requires to be stated:

- **The FD001 test set is a seen set, not an independent one.** The historical champion promotion rule compared
  the test RMSE, and that figure was published and compared across environments. The 18.4 therefore describes the
  behaviour of the frozen model on a known set and is not an independent estimate of future error. The app says so
  on screen (`uncertainty.note` in `/api/overview`) and declares the uncertainty as not calibrated.
- **18.4 versus 19.10.** The workspace model was reproduced bit for bit (RMSE 18.441849882005343) with scikit-learn
  1.7.2 on `gold_features`. The difference with the local measure is due to the scikit-learn version (1.9.1) and the
  ~1e-9 numerical noise between the features computed with pandas and with Spark; perturbing the features with that
  noise, the RMSE moves between 18.29 and 18.98. Neither figure is "better": quoting "18.4 to 19.1 cycles" is the
  honest statement.

**Current promotion protocol (train only).** Since 15 October `src/02_train.py` no longer compares the test RMSE to
move the alias: it computes `rmse_val_truncated` with `GroupKFold` by engine on train (every held-out engine is cut at
a seeded cycle and evaluated like the test set) and promotes only if it improves by more than `std_folds / sqrt(k)`.
If the champion has no such metric (the case of v1), the new version stays as `challenger` and the decision is manual.
It is implemented and tested (`tests/test_modeling_selection.py`) and was exercised in the workspace on 16 October,
when the stack was re-created under the English names: version 1 of `workspace.maintenance.rul_model` was promoted by
the "no champion yet" rule with `rmse_val_truncated` 19.72 and `std_folds` 2.05 recorded in `gold_model_metrics`.

**RAG assistant v2** (`app/rag.py`), measured on a held-out set of 85 questions in Spanish, English and French that
was never used to decide any change (`eval/rag_eval_set_holdout.json`), with an independent judge from another model
family (`databricks-meta-llama-3-3-70b-instruct`):

| Metric (held-out, final version C, run 6) | v1 | v2 final |
|---|---|---|
| Correct status (answers or abstains when it should) | 0.788 | 0.976 |
| hit@6 / MRR / page recall@6 | 0.826 / 0.653 / 0.741 | 0.971 / 0.842 / 0.888 |
| Shown claims supported (independent judge) | 0.358 | 0.940 (partial 0.060; not supported 0.000) |
| Cited page exists in the index | 1.000 | 1.000 |
| Citation precision | 0.685 | 0.989 |
| Affirmative `must_not` violations (manufacturer figures, invented procedures, injected text) | n/a | 0 |

Details, limitations and the honest chronology of the changes are in `audit/03_RAG_VALIDATION.md`; the manual
review of the mandatory case (high-pressure compressor inspection) is in `eval/MANUAL_REVIEW.md`. The final version C
has a single run on the held-out set; at temperature 0.1 some questions change status between runs, so the rates
have a resolution of 1/85.

## What it demonstrates (map to the specialisation)

"Enterprise AI and Data Engineering with Databricks" specialisation, five courses:

| Course | Where it shows in the project |
|---|---|
| 1. Lakehouse fundamentals | Medallion architecture, Unity Catalog (`catalog.schema.table`, volumes `raw`, `manuals` and `code`), serverless compute, Free Edition |
| 2. Data engineering with Delta Lake | Lakeflow Spark Declarative Pipelines (`pyspark.pipelines`), Auto Loader, expectations, streaming tables and materialized views (`src/pipeline/`) |
| 3. ML with MLflow | Training with baselines, MLflow 3, signature and input example, model in Unity Catalog with the `champion` alias, promotion by train-only validation (`src/02_train.py`, `src/03_score.py`) |
| 4. Generative AI and LLMs | RAG with AI Search (Delta Sync index, managed embeddings), Foundation Model APIs LLM, claim and citation verification, held-out evaluation with an independent judge (`src/04_rag_index.py`, `src/05_eval_rag.py`, `src/07_eval_assistant.py`) |
| 5. Governance and MLOps | Declarative Automation Bundles (`databricks.yml`), CI/CD with GitHub Actions and OAuth M2M, least-privilege permissions for the app, 142 tests, audit with evidence |

## Architecture

```mermaid
flowchart LR
  subgraph Volumes
    RAW[(raw/cmapss<br/>train, test, RUL)]
    MAN[(manuals<br/>PDF)]
    COD[(code<br/>copy of src/lib)]
  end
  RAW -->|Auto Loader| B[bronze_cmapss_readings<br/>bronze_cmapss_rul]
  B --> S[silver_readings<br/>silver_rul_test]
  S --> G[gold_features<br/>gold_test_truth<br/>gold_health_curve]
  G --> T[02_train<br/>MLflow 3]
  T --> M[(rul_model@champion<br/>Unity Catalog)]
  M --> P[03_score<br/>gold_predictions]
  M --> E[Serving endpoint<br/>CPU Small, scale to zero]
  T --> GM[gold_model_metrics]
  MAN -->|pypdf, chunking| C[manual_chunks<br/>Delta + CDF]
  C --> I[AI Search<br/>Delta Sync index]
  P --> APP[Databricks App<br/>FastAPI + SQL warehouse]
  GM --> APP
  G --> APP
  I --> APP
  APP --> LLM[Foundation Model APIs<br/>gpt-oss-120b]
  I --> EV[07_eval_assistant<br/>held-out + judge]
```

The app **does not call the serving endpoint**: it reads `gold_predictions` (batch scoring) and says so in
`/api/overview` (`model.source`). The endpoint exists for direct integrations and was verified to return exactly the
same values as the table for the 100 engines.

Design decisions:

- **Bronze and silver are streaming tables**: the files land in the volume and are only appended; Auto Loader
  processes only what is new. The silver cleaning is row by row, so it needs no state.
- **Gold tables are materialized views**: the rolling windows, the reference statistics and the join with the test
  ground truth need the full history of every engine.
- **RUL label**: `RUL = min(remaining cycles, 125)` in train. The clip at 125 is standard practice in the C-MAPSS
  literature (Heimes, 2008; Li, Ding and Sun, 2018). The evaluation uses the actual test RUL, without clipping.
- **Sensors dropped in silver**: `setting3`, `s1`, `s5`, `s10`, `s16`, `s18`, `s19` have a standard deviation of
  exactly 0 in train and test; `s6` takes only two values. `tests/test_cmapss.py` checks it against the real data.
  14 sensors remain.
- **Features**: value, rolling mean and standard deviation over windows of 5 and 10 cycles and slope over 10 cycles
  per sensor, plus the cycle (85 features). Backward-looking windows only; the audit checked the absence of future
  leakage with 40 truncation tests.
- **One definition, two implementations**: `src/lib/cmapss.py` (pandas) is the reference testable without Spark and
  `src/lib/spark_features.py` implements it with Spark window functions. `tests/test_spark_equivalence.py` checks
  that both give the same numbers (maximum relative difference 2.4e-9 over 170 column sums); skipped without Java.
- **Top sensors per engine**: local occlusion sensitivity. They are not SHAP values.
- **Health index** (curve in the app): linear combination of z-scores against the first 30 cycles. It is a summary
  view, not a prediction.
- **Advisory recommendations**: the `critical`, `watch` and `healthy` texts never order shutdowns or replacements and
  always start with "Demo estimate; requires engineering review". One test checks that `lib` and `app` generate
  exactly the same text and another forbids words such as `shutdown`, `replace`, `immediately`.
- **Naming convention**: Free Edition has a single working schema, so the layers are identified by prefix
  (`bronze_`, `silver_`, `gold_`).

### RAG assistant v2: how it answers

1. The question (ES, EN or FR) is rewritten into an English technical query and expanded into several queries;
   retrieval is hybrid with RRF fusion and k = 6.
2. Instruction-like sentences are removed from the passages and from the question before they reach the model:
   documents are data, not orders.
3. The LLM returns structured JSON with a summary, cited claims (document and page), gaps and a manufacturer note.
4. **Citation verification**: every claim is checked against the cited chunk (verdict `SUPPORTED`, `PARTIAL` or
   `NOT_SUPPORTED`, plus lexical support); unsupported claims are removed and listed in `removed_claims`. If the chunk
   limits the statement to an engine type ("On centrifugal flow engines") and the claim omits it, it becomes partial
   with the condition annotated.
5. **Statuses**: `answered`, `insufficient_evidence`, `requires_manufacturer_documentation` (the question asks for
   figures or names an engine model: values with units are withheld, also in the summary) and `not_found`.
6. The interface shows the status, the sources with page, the removed claims and the retrieval trace. Everything
   comes through `/api/ask`.

## Structure

```
databricks.yml              bundle: variables, dev and prod targets
resources/                  pipelines.yml, jobs.yml, serving.yml, app.yml
src/00_setup.py             schema, volumes (raw, manuals, code), data copy
src/pipeline/               bronze.py, silver.py, gold.py (Lakeflow SDP)
src/02_train.py             training, train-only validation by engine, registration and promotion
src/03_score.py             feature validation and scoring of the 100 engines (gold_predictions)
src/04_rag_index.py         PDF to chunks, table with CDF, AI Search index
src/05_eval_rag.py          recall@k and MRR of the retrieval
src/06_app_permissions.py   read GRANTs to the app service principal
src/07_eval_assistant.py    assistant evaluation: dev and held-out, deterministic checks, independent judge
src/lib/                    pure, testable logic (no Spark except spark_*.py)
app/                        Databricks App: app.yaml, requirements.txt, app.py, rag.py, health_projection.py, static/index.html
tests/                      pytest (142 tests: pure functions, Spark equivalence, API, 25 negative, RAG, model selection)
scripts/                    train_local.py (local RMSE), demo_local.py (app without Databricks)
data/cmapss/                NASA data and SOURCE.md
eval/                       RAG evaluation sets, results per run (results/), MANUAL_REVIEW.md, RAG_REPORT.md
audit/                      01_BASELINE, 02_MODEL_VALIDATION, 03_RAG_VALIDATION, 04_SECURITY_QA, FINAL_READINESS_REPORT and evidence/
portfolio/                  case study, real screenshots, carousel (HTML, PDF, PNG), posts, demo script, SOURCES.md
.github/workflows/          deploy.yml
ruff.toml                   lint and format configuration (line length 110)
```

## Deployed names

The code uses English Unity Catalog names: schema `maintenance`, volumes `raw`, `code` and `manuals`, tables
`bronze_cmapss_readings`, `bronze_cmapss_rul`, `silver_readings`, `silver_rul_test`, `gold_features`,
`gold_test_truth`, `gold_health_params`, `gold_health_curve`, `gold_quality_control`, `gold_model_metrics`,
`gold_predictions` (columns `state`, `action`, `critical_threshold`, `watch_threshold`), `manual_chunks`,
`manual_chunks_index` and `rag_eval_questions`; bundle `predictive-maintenance` with jobs `maintenance_end_to_end`,
`maintenance_rag`, `maintenance_eval_assistant`, pipeline `maintenance_pipeline` and app `predictive-maintenance-<target>`.

The audit (`audit/`, 13 to 15 October) and the evaluation results (`eval/results/`) were produced on the first
deployment, which used Spanish names: schema `mantenimiento`, volumes `codigo` and `manuales`, tables
`bronze_cmapss_lecturas`, `silver_lecturas`, `gold_test_verdad`, `gold_salud_parametros`, `gold_salud_curva`,
`gold_control_calidad`, `gold_predicciones` (columns `estado`, `accion`, `umbral_critico`, `umbral_vigilar`),
`rag_eval_preguntas`, bundle `mantenimiento-predictivo`, app `mantenimiento-predictivo-dev`, AI Search endpoint
`mantenimiento_ai_search` and MLflow experiment `mantenimiento_predictivo`. Those documents and the raw files under
`audit/evidence/` and `eval/results/` keep the names as captured. On 16 October the stack was re-created under the
English names (schema, volumes, jobs, pipeline, app, AI Search endpoint `maintenance_ai_search`, serving endpoint
pointing to `workspace.maintenance.rul_model` v1), the results were checked against the audited ones
(`audit/FINAL_READINESS_REPORT.md` §5) and the Spanish objects were deleted. The GitHub repository keeps its
original name `databricks-mantenimiento-predictivo` so that published links keep working.

## How to deploy

**With the CLI (recommended)**

```bash
databricks auth login --host https://<your-workspace>
databricks bundle validate -t dev --var warehouse_id=<warehouse-id>
databricks bundle deploy   -t dev --var warehouse_id=<warehouse-id>
```

The workspace host is not written in the bundle: it goes in a CLI profile or in `DATABRICKS_HOST`. `warehouse_id`
is the 2X-Small SQL warehouse of Free Edition (Compute, SQL warehouses, Connection details).

**Workspace files read workaround.** On recent serverless runtimes, reading plain workspace files from a notebook
can fail with `OSError: [Errno 5] Input/output error`. The notebooks therefore look for the `lib` package first in the
`code` volume, and the data is uploaded to the `raw` volume with the CLI instead of being copied from the repository:

```bash
databricks fs cp -r src/lib dbfs:/Volumes/workspace/maintenance/code/lib --overwrite
databricks fs cp app/rag.py dbfs:/Volumes/workspace/maintenance/code/rag.py --overwrite
databricks fs cp -r eval dbfs:/Volumes/workspace/maintenance/code/eval --overwrite
databricks fs cp -r data/cmapss dbfs:/Volumes/workspace/maintenance/raw/cmapss --overwrite
```

The first copy must be repeated whenever `src/lib` changes (the bundle deploys the code, but the notebooks load the
copy in the volume). `00_setup.py` creates the volumes and, if the data is already in `raw`, does not read it from
the workspace again. On a brand-new workspace create the schema and the volumes first
(`databricks schemas create maintenance workspace`, `databricks volumes create workspace maintenance raw MANAGED`,
same for `code` and `manuals`) so that the copies above have a destination.

**Run order**

1. Deploy the bundle. On the first deployment comment out the `resources/serving.yml` line of `databricks.yml`:
   the endpoint needs a model version that does not exist yet.
2. Upload `src/lib`, `app/rag.py`, `eval/` and the data to the volumes (commands above).
3. Upload the public manual PDFs to the `manuals` volume. The PDFs are not in the repository.
4. Job `maintenance_end_to_end`: `00_setup`, bronze, silver and gold pipeline, `02_train` and `03_score`.
5. Job `maintenance_rag`: `04_rag_index` and `05_eval_rag`. Creates the AI Search endpoint the first time (takes
   several minutes) and indexes the manuals (about 40 minutes for the 215 MB handbook).
6. App: `databricks bundle run maintenance_app -t dev` or from the UI. Then run `src/06_app_permissions.py` (or the
   equivalent `GRANT` statements) to give read access to the app service principal.
7. Serving (optional): uncomment `resources/serving.yml`, set `model_version` to the champion version and deploy
   again. The endpoint points to a specific version, it does not follow the alias. In `mode: development` the real
   name carries a user prefix (`dev_<user>_rul-model-dev`).
8. Assistant evaluation (optional): job `maintenance_eval_assistant` with parameters `dataset` (`dev` or `holdout`),
   `configs` and `eval_judge_endpoint`. The results are written to the `code` volume and copied to `eval/results/`.

**Before a demo**: `databricks apps start predictive-maintenance-dev`, check `/api/health` (warehouse, index and LLM
at `ok`) and, if the serving endpoint does not exist, `databricks bundle deploy -t dev`.

**CI/CD:** `.github/workflows/deploy.yml` runs ruff and the tests on every PR without access to secrets, validates the
bundle on PRs and deploys to `dev` on every push to `main` (environment `dev`, for which a manual approval rule is
advisable). Repository secrets: `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` (OAuth M2M).
There are never tokens in the repository. The `prod` target exists as a reference; Free Edition has no account-level
service principals for a separate `run_as`.

## Free Edition limits and what was observed

| Limit | How it is respected or what happened |
|---|---|
| Serverless only, no clusters | Pipeline `serverless: true`; jobs with serverless `environments`; no cluster defined |
| Internet restricted to trusted domains | The data travels in the repository and is uploaded to the volume; only `pip` is used at run time (`pypdf`, `databricks-ai-search`) |
| 1 SQL warehouse 2X-Small | The app queries with that warehouse, declared as a resource with `CAN_USE`, with a 30 s cache. On 15 October it could not start for a few minutes (transient platform error) |
| 1 AI Search endpoint of 1 unit | Reused if it exists; `TRIGGERED` index (no continuous sync) |
| Apps stop on their own | The app stopped after 24 h and also minutes after being used on 15 October: it must be started before every demo. **Availability is not continuous** |
| Serving endpoint persistence | The endpoint **disappeared from the workspace** between 14 and 15 October without an accessible record; it was recreated with `bundle deploy -t dev`. It does not affect the dashboard (it reads the batch table) |
| 1 active pipeline per type | A single pipeline in triggered mode, started from the job |
| Max. 5 concurrent job tasks | Sequential jobs, `max_concurrent_runs: 1` |
| Serving without GPU or provisioned throughput | CPU `Small` endpoint with scale to zero; the LLM is a pay-per-token Foundation Model APIs endpoint |
| No account console | No OIDC federation: the CI uses OAuth M2M with a workspace service principal |

## Security

Summary of `audit/04_SECURITY_QA.md` (22 controls, revalidated on 15 October):

- No secrets in the tree or in the Git history; the workspace host and the warehouse and app identifiers were
  replaced by placeholders in `audit/evidence/`.
- The app requires a workspace session (`/api/*` answers 401 without credentials; `/healthz` answers 200 without
  data). `CAN_MANAGE` only for the owner and `admins`.
- The app service principal has `USE CATALOG`, `USE SCHEMA` and `SELECT` on the schema, `CAN_USE` on the warehouse
  and nothing else (no `MODIFY`, no `EXECUTE` on the model, no `CAN_QUERY` on the endpoint).
- SQL with bound parameters and names validated by regex; interface without `innerHTML` (the LLM output is shown
  as text); logs without user questions or tokens.
- Explicit errors: a SQL failure returns 503 `data_unavailable`, never an empty state with 200; a null RUL is not
  replaced by 0 (25 negative tests).
- Prompt injection: passages and questions are treated as data, instruction-like sentences are filtered (EN, ES,
  FR) and every shown claim is verified. Injection inside an indexed passage was not tested live.
- `pip-audit` with no warnings on the direct dependencies; one warning in transitive `oauthlib` (not exploitable:
  the app is an OAuth client, not a server).

Open findings (none blocking for a demo): the serving endpoint accepts nulls and absurd values without error (D-02;
`03_score.py` validates before scoring and the app does not use it), no rate limit on the LLM for workspace users
(D-04), no CSP headers (D-06), open ranges in `requirements-dev.txt` (D-07).

## Tests and local demo

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
python -m pytest -q -rs                # 142 tests; the Spark equivalence needs Java (skipped if missing)
python scripts/train_local.py          # RMSE on the real FD001 test set (--tune searches hyperparameters with CV on train)
python scripts/demo_local.py           # the app with local data, no Databricks (the assistant answers from a fake LLM)
```

## Audit and portfolio

- `audit/FINAL_READINESS_REPORT.md`: acceptance criteria, what really works and open issues.
- `audit/01_BASELINE.md`: deployed resources, inventory and revalidation of 15 October.
- `audit/02_MODEL_VALIDATION.md`: data integrity, bit-for-bit reproduction, 19.10 versus 18.44 explained, test
  contamination and the new protocol, endpoint, negative tests.
- `audit/03_RAG_VALIDATION.md`: method, 85-question held-out set, changes in `rag.py` and their justification, the
  HPC case, limitations.
- `audit/04_SECURITY_QA.md`: controls, open findings and revalidation.
- `portfolio/CASE_STUDY_EN.md`, `portfolio/carousel/`, `portfolio/screenshots/` (real screenshots of the deployed
  app, untouched), `portfolio/SOURCES.md` (where every figure comes from).

## Working method

Jonathan Rincon defined the architecture, the parameters, the work sequence and the acceptance criteria, and
reviewed the results of every phase. The figures in this README were cross-checked against the files in `audit/`.

## Zero cost and data

Everything runs on Free Edition, within its fair-use quotas. There are no paid services and no external keys.

**Data:** NASA C-MAPSS FD001 (Saxena et al., 2008, NASA Ames Prognostics Data Repository), public domain.
Provenance and checks in `data/cmapss/SOURCE.md`. The RAG manuals used in the evaluation are public
(FAA-H-8083-32B and Saxena et al., 2008); they are not in the repository. If you use others, check their licence.
The code is MIT (`LICENSE`).
