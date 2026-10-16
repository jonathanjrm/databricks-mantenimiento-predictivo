# 01. Project baseline (Phase A)

Audit of 13 to 15 October 2026. Local working branch `validacion` in the GitHub clone (formerly `validation`), no commits or push; deployments only to the `dev` target. Every command
and result in this document was actually executed; whatever could not be executed is marked `NOT_VERIFIED`.

> Names in this document are those of the deployment audited (Spanish schema `mantenimiento`, tables such as `gold_predicciones`, app `mantenimiento-predictivo-dev`). The current code uses English names; the mapping is in the README, section "Deployed names".

## 1. Version and environment

| Element | Value | How it was obtained |
|---|---|---|
| Base commit | `cd81b7a358a3bbebe3d468c98265616c25e64231` ("Informe de evaluación del asistente RAG...") | `git rev-parse HEAD` |
| Branch | `validacion` (uncommitted changes in the working tree; the PC copy is not a Git repository) | `git rev-parse --abbrev-ref HEAD` |
| History | 6 commits, one author (Jonathan Rincon) | `git log --oneline` |
| Local audit Python | 3.13.16 | `python3 --version` |
| Local libraries | scikit-learn 1.9.1, pandas 3.0.5, numpy 2.5.3, pyspark 4.2.0, pyarrow 25.0.1, fastapi 0.143.0, httpx 0.28.1, pytest 9.1.1, mlflow 3.17.1 | `pip list` |
| Java | OpenJDK 21.0.12.1 (needed by the Spark test) | `java -version` |
| Databricks CLI | v1.20.0 | `databricks --version` (queue A, `B01_version`) |
| Serverless environment where the model was trained | `databricks_runtime: client.6`, Python 3.12.3, scikit-learn **1.7.2**, pandas 2.3.3, pyarrow 21.0.0, MLflow 3.12.0, cloudpickle 3.1.1 | `MLmodel` and `requirements.txt` artifacts of the registered model |
| Workspace | Databricks Free Edition (serverless only), catalog `workspace`, schema `mantenimiento` | Brief + queries |

Relevant difference: the local environment (scikit-learn 1.9.1, pandas 3.0.5) is not the workspace one (1.7.2, 2.3.3). It is a partial cause
of the 19.10 versus 18.44 RMSE difference (see `02_MODEL_VALIDATION.md`, section B2).

## 2. Resources actually deployed (listed with the CLI)

| Resource | Verified state | Command |
|---|---|---|
| Pipeline `[dev ...] mantenimiento_pipeline_dev` (id `f0ca97c9-...`) | Exists in the bundle summary | `bundle summary -t dev` |
| Jobs: `mantenimiento_end_to_end_dev` (134653962208443), `mantenimiento_rag_dev` (394502178342467), `mantenimiento_eval_assistant_dev` (616581869043888) | Listed | `jobs list` |
| UC model `workspace.mantenimiento.rul_model` | 1 version (v1, state READY), alias `champion` points to v1 | UC API `models/.../versions` and `.../aliases/champion` |
| Serving endpoint | **Real name: `dev_jjrm199867_rul-model-dev`** (not `rul-model-dev`: the bundle's `development` mode prepends `dev_<user>_`). Entity `workspace.mantenimiento.rul_model` v1, CPU Small, scale to zero, state READY, "Scaled to zero" at the start of the audit | `serving-endpoints get`, `list` |
| App `mantenimiento-predictivo-dev` | `RUNNING`, deployment `SUCCEEDED` (SNAPSHOT), single declared resource: SQL warehouse `CAN_USE` | `apps get` |
| Tables | `bronze_cmapss_lecturas` 33,727, `bronze_cmapss_rul` 1, `silver_lecturas` 33,727, `silver_rul_test` 100, `gold_features` 33,727, `gold_test_verdad` 100, `gold_salud_curva` 33,727, `gold_predicciones` 100, `gold_model_metrics` 3, `gold_control_calidad` 2 | SQL (`audit/evidence/workspace/sql_counts.json`) |

Observations:
- `bundle summary` shows the app as "(not deployed)" although `apps get` returns it RUNNING (the summary does not reflect the SNAPSHOT deployment). Not a defect of the project.
- The endpoint name with the user prefix must be taken into account in any documentation or script that uses the literal `rul-model-dev`.
- `bronze_cmapss_rul` stores the file `RUL_FD001.txt` as a single text row (100 values in a `value` field); `silver_rul_test` splits it into 100 rows.

## 3. Inventory of reviewed files

Read and analysed: `README.md`, `databricks.yml`, `resources/{app,jobs,pipelines,serving}.yml`, `src/pipeline/`, `src/lib/*`,
`src/02_train.py`, `src/03_score.py`, `src/04_rag_index.py`, `src/05_eval_rag.py`, `src/07_eval_assistant.py`, `app/app.py`, `app/rag.py`,
`app/health_projection.py`, `app/static/index.html`, `eval/`, `tests/`, `.github/workflows/deploy.yml`. The detailed RAG audit
belongs to phase C (`03_RAG_VALIDATION.md`).

## 4. Test suite and the "61 + 1 skipped" versus 67 discrepancy

Initial state (commit `cd81b7a`, before any change of this audit):

```
python3 -m pytest -q          ->  67 passed, 187 warnings in 52.06 s
```

Count per file (`pytest --collect-only`): `test_app_api` 10, `test_chunking` 7, `test_cmapss` 15, `test_rag` 26,
`test_retrieval` 3, `test_spark_equivalence` 6. Total 67.

**Explanation of "61 passed and one skipped":** `tests/test_spark_equivalence.py` (6 tests) has a
`pytest.importorskip("pyspark")` and a module-level `pytest.skip(..., allow_module_level=True)` at the start of the module.
If PySpark **or** Java is missing, **the whole module** is skipped and pytest counts it as **one** skip, not six. 67 - 6 = 61
passed + 1 skipped. It is exactly the "61 + 1 skipped" pattern; it is consistent with a copy without Java or without PySpark (the environment of that copy could not be inspected: `NOT_VERIFIED`).

Controlled reproduction: Java was hidden from `PATH` (only `python3` and `git`) and the suite of the current working tree
(extended with new tests, see below) was run. Result: `SKIPPED [1] tests/test_spark_equivalence.py:15: Java not available`, 61 tests passed plus
those of the skipped module (`101 passed, 1 skipped` in the current extended suite; the Spark module counts as one skip). Conclusion: **there are no failed or
removed tests**; the difference is the pandas/Spark equivalence test, which only runs with Java. That is the test that
backs the statement "pandas and Spark give the same", so the copy without Java **does not verify it**; here it did run
(6 passed) and it was also checked against the real workspace tables (see `02_MODEL_VALIDATION.md`, B1).

Final state of the suite after the changes of phases A, B and D (full run in this environment, with Java):

```
python3 -m pytest -q          ->  107 passed, 187 warnings in 74.66 s
```

(The figure includes the new B4/B5 tests and the extensions of the RAG phase, phase C.)
With Java hidden the suite gives `101 passed, 1 skipped`.

Caveats:
- The app tests (`test_app_api.py`, `test_negative_api.py`) use `TestClient` with simulated SQL queries; they do not touch
  Databricks. They verify the API contract, not the real connection.
- `tests/test_spark_equivalence.py` uses 6 engines, not the 100. The comparison of the 100 engines is done separately with the
  workspace tables (B1).

## 5. Workspace state at the start

- `champion` alias on v1. `gold_predicciones` with 100 rows, `model_version = 1`, `scored_at = 2026-10-13T20:26:50Z`.
- `gold_model_metrics`: 3 rows (mean baseline, Ridge baseline, HGB v1), a single training run (`trained_at 2026-10-13T18:46Z`).
- The deployed `gold_predicciones` table still keeps the `accion` column with the old text ("Schedule shutdown and replacement..."),
  which the app recomputes; it will be corrected when `03_score` runs again after deploying the new text (see B5).
- Unity Catalog permissions of the app service principal (`d19896a0-...`): `USE CATALOG`, `USE SCHEMA`, `SELECT` on the schema. Nothing else.

## 6. Revalidation of 15 October 2026 (final session)

Evidence: `audit/evidence/revalidation_2026-10-15/A_*.log` and `audit/evidence/A_worklog.md`.

### 6.1 PC copy versus GitHub `main`

The user's working copy (`C:\Users\...\databricks-mantenimiento-predictivo`, source of truth) was compared by md5 with the
GitHub clone (`main` = `cd81b7a`). Result: **12 code files differ from `main`** and **13 files or folders are new**
(not counting `audit/` and `portfolio/`). The PC copy matches file by file the working tree audited in the container
except `.gitignore` (adds `herramientas/`, `logs/`, `*.bat`; in this session `cola/`, `cola2/`, `consultas/` are added) and
`databricks.yml` (identical except comments; same `warehouse_id`, `model_version 1`, `serverless_env_version 6`).

| File | State versus `main` | Origin of the change |
|---|---|---|
| `.github/workflows/deploy.yml` | modified (+26/-x) | D-01: secrets only in the jobs that deploy; `pytest -rs` |
| `app/app.py` | modified (+279) | B3/B4/D16: explicit 503, null RUL, SQL timeout, `/api/health`, thresholds, traceability |
| `app/rag.py`, `src/07_eval_assistant.py`, `tests/test_rag.py`, `resources/jobs.yml` | modified | Phase C |
| `app/static/index.html` | modified (+192) | Phase E: loading and error states, prediction/actual/indicator labels, filters, service chips |
| `src/02_train.py`, `src/lib/modeling.py` | modified (this session) | B2.4: champion promotion by train-only validation by engine |
| `src/03_score.py`, `src/lib/cmapss.py` | modified | B4/B5: `validate_feature_frame`, advisory text |
| `tests/test_app_api.py`, `tests/test_cmapss.py` | modified | tests of the above |
| New | `tests/test_negative_api.py` (25), `tests/test_phase_e_api.py` (11), `tests/test_modeling_selection.py` (4), `eval/MANUAL_REVIEW.md`, `eval/rag_eval_set_holdout.json`, 7 files `eval/resultados/results_*.json` | B4, E, B2.4, phase C |

None of these changes is on GitHub: the public repository is still at `cd81b7a`. **Publication requires a commit and push that
only the user must do** after reviewing the diff (the `.bat`, `cola/`, `logs/`, `consultas/`, `herramientas/` are excluded by `.gitignore`).

### 6.2 Test suite (PC tree, container with Java 21)

```
python3 -m pytest -q -rs   ->  138 passed, 0 skipped, 187 warnings in 78.74 s   (before adding tests/test_modeling_selection.py)
python3 -m pytest -q -rs   ->  142 passed, 0 skipped, 187 warnings in 49.59 s   (final state of the PC tree)
```

Count per file: `test_app_api` 11, `test_chunking` 7, `test_cmapss` 17, `test_modeling_selection` 4, `test_negative_api` 25,
`test_phase_e_api` 11, `test_rag` 58, `test_retrieval` 3, `test_spark_equivalence` 6. On `main` (`cd81b7a`) the collection is 67
(10+7+15+26+3+6), which confirms the explanation of section 4: 61 + 1 skipped = 67 without the Spark module. The 187 warnings are
`FutureWarning`/`Pandas4Warning` of PySpark 4.2 with pandas 3.0; they do not affect the result.

### 6.3 Real state of the workspace on 15 October (CLI, queue `A_`)

| Resource | State at 22:1x UTC | Action | State afterwards |
|---|---|---|---|
| App `mantenimiento-predictivo-dev` | `STOPPED`: "App compute was stopped due to workspace or account status" (automatic stop of Free Edition after ~24 h) | `apps start` (`A_12`) | `RUNNING`, deployment `SUCCEEDED` at 22:16:04Z (`A_32`) |
| SQL warehouse `<warehouse-id>` | `STOPPED`; `warehouses start` and the SQL queries failed with "Cannot start warehouse ... Serverless Compute ... disabled in global warehouse config" (`A_20`..`A_24`, `A_34`) | None: the app started it when serving `/api/overview` | `RUNNING` at 22:22 (`A_40`); every later query correct (`A_41`..`A_46`). The initial error was transient, but it shows that availability in Free Edition is not guaranteed |
| Endpoint `dev_jjrm199867_rul-model-dev` | **Does not exist** (`A_13`, `A_25`, `A_29`, `A_36`; `A_30/A_33` only list Foundation Model endpoints). On 14 October at 00:5x it answered (B3). Cause `NOT_VERIFIED`: there is no accessible audit record; most likely a Free Edition clean-up of inactive scale-to-zero endpoints, or a manual deletion | `bundle deploy -t dev` (`A_52`) recreates the resource declared in `resources/serving.yml` | see `02_MODEL_VALIDATION.md`, B3 (revalidation) |
| Jobs | 3 jobs listed; last two runs of `mantenimiento_end_to_end_dev` `SUCCESS` (`A_38`) | | |
| Pipeline | `IDLE`, last update `COMPLETED` 2026-10-13T18:43Z (`A_11`) | | |
| Model `rul_model` | v1 `READY`, `run_id 4688ee42...`, created 2026-10-13T18:46Z (`A_06`, `A_08`). `registered-models get-by-alias` does not exist in CLI v1.20.0; the alias was verified by API in the previous session (`workspace/uc_model_versions.json`) | | |
| Tables | `bronze_cmapss_lecturas` 33,727, `silver_lecturas` 20,631 train + 13,096 test (100 + 100 engines), `silver_rul_test` 100, `gold_features` 33,727, `gold_test_verdad` 100, `gold_predicciones` 100, `gold_model_metrics` 3 (`A_41`) | | |
| App permissions | `CAN_MANAGE` owner and `admins` (`A_10`); UC grants of the SP: `USE SCHEMA`, `SELECT` on the schema (`A_46`) | | |

Baseline conclusion: the pipeline, the tables, the model and the app are still the audited ones; the only resource that had
disappeared is the serving endpoint, which the bundle creates again. Continuous availability is **not** part of what is certified.
