# 02. AI model validation (Phase B)

Scope: B1 data integrity, B2 training and evaluation, B3 MLflow and serving, B4 negative tests, B5 operational recommendations.
Reproducible evidence in `audit/evidence/` (`.py` scripts, `.json`/`.csv` results, SQL queries saved under `workspace/`).
Labels: PASS / FAIL / NOT_VERIFIED. Nothing that follows is an industrial certification: it is a demonstration prototype on a simulation dataset.

> Names in this document are those of the deployment audited (Spanish schema `mantenimiento`, tables such as `gold_predicciones`, columns `estado`/`accion`, function `rmse_de_version`, script `entrenar_local.py`). The current code uses English names (`gold_predictions`, `state`/`action`, `validation_rmse_of_version`, `train_local.py`); the mapping is in the README, section "Deployed names". Test names quoted below are those of the audited tree; they were later translated to English.

## Summary

| Item | Status | Key result |
|---|---|---|
| B1 Data integrity | PASS | 20,631 / 13,096 rows and 100 + 100 engines (match official FD001); correct RUL (0 discrepancies); 170 Spark versus pandas feature sums with a maximum relative difference of 2.4e-9; 40 truncation tests without future leakage |
| B1 Predictions come from real data | PASS | `gold_predicciones` = model output on `gold_features` (100 engines). Verified with the endpoint: exact match on all 100 |
| B2 Reproduction | PASS with reservations | HGB reproduced **bit for bit** (RMSE 18.441849882005343) with scikit-learn 1.7.2 on the workspace `gold_features`; the local 19.10 is explained by the scikit-learn version and numerical feature noise (see below) |
| B2 Absence of leakage | PASS in features; **FAIL in the historical protocol, fixed in the code** | No leakage in windows and labels. The FD001 test set is already a "seen" set: the champion promotion rule used it (experimental contamination, section B2.4). `02_train.py` now promotes by train-only validation by engine; the test figure is still not independent |
| B3 MLflow and serving | PASS (revalidated on 15 October) | v1, `champion` alias, 85-input signature, endpoint actually invoked; endpoint output = `gold_predicciones` on the 100 engines. The endpoint had disappeared from the workspace between 14 and 15 October and was recreated with `bundle deploy -t dev` (B3.5) |
| B3 Endpoint and app correspondence | PASS with clarification | The app **does not call the endpoint**: it reads `gold_predicciones`. The values match (rounding difference at 2 decimals) |
| B3 No silent fallback | PASS in the app after correction; findings in the endpoint | The original app replaced a null RUL with 0 and showed metrics of another version; fixed. The endpoint accepts nulls without error (D-02) |
| B4 Negative tests | PASS (after correction) | 25 tests; 10 failed against the original `app.py` |
| B5 Recommendations | PASS (after correction) | Advisory texts; no shutdown or replacement orders; uncertainty declared as not calibrated |

---

## B1. Data integrity

Script: `evidence/compare_workspace_vs_local.py`. Result: `evidence/b1_data_integrity.json`.

### Provenance and counts

The declared provenance (`data/cmapss/FUENTE.md`, now `SOURCE.md`) is a GitHub mirror (`edwardzjl/CMAPSSData`) of the NASA Ames repository;
there is no cryptographic verification against the NASA origin (the environment has no access). `NOT_VERIFIED` the byte-for-byte identity
with the original NASA file; the official counts and internal properties are verified:

| Check | Expected (official FD001) | Observed | Status |
|---|---|---|---|
| Train rows | 20,631 | 20,631 (file and `silver_lecturas` split train) | PASS |
| Test rows | 13,096 | 13,096 | PASS |
| Train / test engines | 100 / 100 | 100 / 100, identifiers 1 to 100 | PASS |
| RUL values | 100 | 100; `silver_rul_test` matches the file | PASS |
| Contiguous cycles from 1 per engine | yes | yes (train and test) | PASS |
| Null values | 0 | 0 | PASS |

### Train and test separation

- Different source files per split (`silver_source_files.json`): train comes only from `train_FD001.txt`, test only from `test_FD001.txt`.
- Engine identifiers 1 to 100 repeat in both files (they are different engines; that is how FD001 is): the model and the queries always
  use the key (`split`, `unit`). It was checked that **no sensor row** is identical between train and test (0 matches).
- The RUL exists only in train: in `gold_features` the 13,096 test rows have null `rul` and `rul_raw` (0 rows with a value).

### RUL label

- `rul_raw = max_cycle_of_engine - cycle` (SQL: 0 discrepancies in the 20,631 train rows); `rul = min(rul_raw, 125)` (0 discrepancies).
  The last cycle of every engine has RUL 0. 38.9 % of the train rows are clipped at 125 (standard practice in C-MAPSS).
- Test: the actual RUL is that of `RUL_FD001.txt`, evaluated at the last observed cycle; `gold_test_verdad` and `silver_rul_test` match the file.
  Note: the test ground truth is not clipped while the model is trained with clipped RUL; the mismatch penalises engines with a long RUL (see B2).

### Removed features

Checked against the data (`constant_columns` in the JSON):

| Feature | Evidence | Justification |
|---|---|---|
| `setting3`, `s1`, `s5`, `s10`, `s16`, `s18`, `s19` | Standard deviation exactly 0 in train and test | No information: correct to remove them |
| `s6` | 2 distinct values (21.60 and 21.61), deviation 0.0015 | Quantisation noise: reasonable decision |
| Kept sensors (14) | Minimum deviation in train 0.0375 | Real variation |
| `setting1`, `setting2` | Deviations 0.0022 and 0.0003 | Kept in silver but not model inputs (single regime in FD001) |

### Windows without future information

- Definition: means, standard deviations and slope over backward windows (including the current cycle), per engine, `min_periods=1`.
- Test (40 checks over 5 engines and 8 cut cycles each): the series are truncated at cycle c, the features recomputed and compared with
  those of cycle c computed with the full series. **0 discrepancies**, that is, the features of a cycle do not depend on later cycles.
- The health index and its parameters are fitted on train only (`fit_health_params`).
- Observation (not leakage): `cycle` is a model feature. In test it equals the observed age of the truncated engine; it is legitimate, but it makes age
  contribute to the prediction (the linear baseline with `cycle` alone obtains RMSE 33.3 against 43.1 for the mean).

### pandas and Spark equivalence

1. Automatic test `tests/test_spark_equivalence.py` (6 real engines, 6 tests): **6 passed** in this environment (Java 21, PySpark 4.2.0).
2. Against the real workspace tables (every row):
   - 170 column sums (85 features x 2 splits) of `gold_features` against the pandas computation: maximum relative difference **2.37e-9**
     (floating-point accumulation order), all below 1e-8. `evidence/features_sums_pandas_vs_workspace.csv`.
   - The 85 features of the last cycle of the 100 test engines, row by row: maximum absolute difference **3.7e-10**.
   - Sums of `rul` and `rul_raw` in train: identical (1,791,375 and 2,224,184).
   - Sensor sums of `silver_lecturas` against the files: differences of the order of 1e-8 (rounding).
   Status: PASS. The equality is numerical, not bit for bit; that ~1e-9 difference has consequences in training (B2.3).

### The predictions of the 100 engines come from real data

Code path verified end to end:

`C-MAPSS files in the raw volume` -> `bronze_cmapss_lecturas` (Auto Loader) -> `silver_lecturas` -> `gold_features` (Spark windows) ->
`src/03_score.py`: loads `models:/workspace.mantenimiento.rul_model@champion`, scores the last cycle of every test engine, computes occlusion
contributions and writes `gold_predicciones` -> `app/app.py` `q_engines()` runs `SELECT ... FROM gold_predicciones` on the SQL warehouse -> `/api/engines` -> `index.html`.

Proof that there are no static or simulated values in the frontend or the API:
- `grep` of `app.py` without `serving_endpoints`, `/invocations`, `.predict(` or numeric defaults (test `test_app_no_llama_al_endpoint_de_serving_ni_tiene_fallback_numerico`).
- The 100 `rul_pred` values of the table match the real endpoint output for the same 100 input rows (B3); 99 distinct values, range 3.12 to 124.98.
- `rul_real` of the table matches the official file; `error = rul_pred - rul_real`; `cycle` = last cycle of every test engine.
- The query of the deployed app returned the same rows (engines 34, 60, 85, see B3).

Honest scope: "prediction" in the dashboard means a RUL estimate **at the last observed cycle of the 100 engines of the test set**, computed in batch
(`scored_at = 2026-10-13T20:26:50Z`), not live inference on telemetry.

---

## B2. Training and evaluation

### B2.1 Reproduced configuration

`src/lib/modeling.py`: `HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0,
early_stopping=False, random_state=42)`; training RUL clipped at 125; 85 features (cycle + 14 sensors x 6); 20,631 training rows.
Evaluation: last cycle of each of the 100 test engines against `RUL_FD001.txt`, predictions clipped at >= 0.
Seed: with `early_stopping=False` and fewer than 200,000 rows, `random_state` plays no role: identical RMSE with seeds 0, 1, 42 and 123
(`rmse_by_seed_hgb`). The model is deterministic for given data and library version.

### B2.2 Reference metrics (workspace model, v1)

Computed from `gold_predicciones` (`evidence/champion_v1_metrics_from_gold_predicciones.json`, `champion_v1_per_engine.csv`) and matching MLflow:

| Metric (100 engines, last cycle) | Value |
|---|---|
| RMSE | **18.44** cycles (MLflow `rmse_test` 18.441849882005343) |
| MAE | 13.48 |
| R2 | 0.803 |
| PHM08 score | 655.0 (MLflow 655.0239) |
| Mean bias (pred minus actual) | -1.01; 54 % of the predictions are late (pred > actual) |
| Absolute error: median / p90 / maximum | 9.5 / 30.6 / 64.4 cycles |
| Engines with absolute error <= 10 / <= 20 cycles | 52 % / 77 % |

Baselines on the same test set (same library version as the champion; see B2.3):

| Model | RMSE | MAE | R2 | PHM08 |
|---|---|---|---|---|
| Constant mean | 43.07 | 35.90 | -0.074 | 33,629 |
| `cycle` only, linear regression (baseline added in the audit) | 33.34 | 27.59 | 0.356 | 5,319 |
| Ridge (85 features) | 21.80 | 16.81 | 0.725 | 1,073 |
| **HGB (champion v1)** | **18.44** | 13.48 | 0.803 | 655 |

HGB improves on Ridge by 3.4 RMSE cycles (16 %). Given the B2.3 uncertainty (±0.4 from numerical variations) the advantage is real but moderate.

Errors per actual RUL band (champion v1; `by_bin` in the JSON):

| Actual RUL | n | RMSE | MAE | Bias (pred - actual) |
|---|---|---|---|---|
| 0 to 30 | 25 | 14.31 | 8.53 | +5.5 |
| 31 to 60 | 14 | 8.50 | 7.14 | +3.0 |
| 61 to 90 | 15 | 18.95 | 16.26 | +5.7 |
| 91 to 125 | 35 | 17.77 | 13.62 | -1.5 |
| 126 or more | 11 | 32.25 | 28.55 | -28.6 |

Reading: the error is largest when the actual RUL exceeds 125, by construction (the model cannot predict more than 125 after training with clipped RUL); it is not a model
failure but the difference between the training label and the test ground truth.

**Engines near failure (actual RUL <= 30, n = 25):** RMSE 14.31, MAE 8.53, bias +5.5 (the model tends to overestimate the remaining life, the dangerous direction),
PHM08 152.5. State classification (demo thresholds 20 and 35 cycles) against the state by actual RUL:

| Actual state (by actual RUL) | Engines | Predicted critical | Predicted watch | Predicted healthy |
|---|---|---|---|---|
| critical (actual RUL <= 20) | 16 | 14 | 1 | 1 |
| watch (21 to 35) | 10 | 0 | 5 | 5 |
| healthy (> 35) | 74 | 0 | 1 | 73 |

Among the 26 engines with actual RUL <= 35, **6 appear as `healthy`** (engines 41, 18, 37, 61, 64, 77; engine 41 has actual RUL 18 and is predicted 49.6) and 1 of the 16 really critical ones stays in `watch`. With 100 engines
there is no precision to refine these rates (wide intervals), but it illustrates why the state cannot be treated as an operational decision.
Worst engines by absolute error: 74 (-66.9 with the local model), 45, 89, 18, 55, 37 (`worst_10`).

### B2.3 Explanation of 19.10 versus 18.44 RMSE

Measured facts:

| Experiment (HGB with the same hyperparameters) | scikit-learn | Training data | Test RMSE |
|---|---|---|---|
| Documented in the README (`scripts/resultado_local.json`) | 1.9.1 | local pandas features | **19.102** |
| Audit reproduction | 1.9.1 | local pandas | 19.102 (identical) |
| Audit reproduction | 1.7.2 | local pandas | 18.597 |
| Audit reproduction | 1.9.1 | Spark `gold_features` (workspace) | 18.505 |
| Audit reproduction | **1.7.2** | **Spark `gold_features` (workspace)** | **18.441849882005343** (PHM08 655.0239471190539, MAE 13.478027648560577) |
| Workspace (MLflow v1) | 1.7.2 | `gold_features` | 18.441849882005343 |

Conclusion: row 5 reproduces **exactly** (every decimal of RMSE, PHM08 and MAE) the model registered in MLflow. Therefore:
- There are no differences of code, seed, cross-validation or feature set between local and workspace. The baselines (mean and Ridge) match in both environments to the ninth decimal (43.0670 and 21.7963), which rules out data or label differences.
- The 19.10 to 18.44 difference (0.66 cycles) is due to **the scikit-learn version** (1.9.1 versus 1.7.2) **and to the ~1e-9 numerical noise** between the features computed with pandas and with Spark. Both factors rearrange some tree splits.
  With workspace data and 1.9.1 the RMSE is 18.505; with pandas data and 1.7.2, 18.597; with both changes, 19.102.
- Measured sensitivity: perturbing the training features with Gaussian relative noise of 1e-9 (the order of the pandas/Spark difference) the test RMSE goes from **18.29 to 18.98** (10 retrainings, two scikit-learn versions). That is, the
  uncertainty of the point metric from numerical instability is about ±0.4 cycles, the same size as the difference between 18.44 and 19.10.
- The workspace figure (18.44) is not "better" than the local one (19.10): both are samples of the same noise distribution. Neither should be quoted with more than one decimal nor as proof that one environment version is superior.

Recommendation for the README: state "RMSE of 18.4 to 19.1 cycles depending on the scikit-learn version and the numerical precision of the features" and record versions in every run (`02_train.py` did not log the scikit-learn version as a parameter; the `MLmodel` artifact does).

### B2.4 Test set contamination and honest protocol

Facts about the use of the test set, as they were in the code:

1. **`src/02_train.py` promoted the champion by comparing the test RMSE.** The function `rmse_de_version` read `rmse_test` from `gold_model_metrics` and the `champion` alias
   moved only if `cand["rmse_test"] < rmse_champ`. That is model selection on the test set, even if the notebook itself acknowledged it ("quality gate, not an independent estimate").
   In practice there is only one version (v1), assigned because no champion existed, so the rule had not been exercised yet; but the design was contaminated from the first repetition.
2. **Hyperparameters:** according to the comments, they were chosen with `GroupKFold` by engine on train only (`scripts/entrenar_local.py --tune`). The audit repeated that search (8 combinations, 5 folds by engine, train only; `repro_sklearn172.json`, `cv_grid_train_only`).
   The best combination in this repetition (`learning_rate 0.05, max_leaf_nodes 15, min_samples_leaf 100`, CV RMSE 15.76) **is not** the deployed configuration (`max_leaf_nodes 31, min_samples_leaf 40`, CV 15.80). The CV differences are 0.04, within noise; there is no evidence of test use,
   but the parameter set in `HGB_PARAMS` is not the optimum of the reproduced search. It cannot be proven what was done before the single commit of the model: the Git history only contains one state of the training code (`NOT_VERIFIED` for earlier decisions).
3. **Other design decisions** (clip at 125, windows 5 and 10, slope over 10, including `cycle`, removing `s6`, choosing HGB over Ridge): they are justified with the literature in the comments, but there is no record of whether test metrics were looked at when deciding.
4. **The FD001 test set is already a seen set.** Its RMSE has been published in the README, compared with baselines and compared across environments. Any new figure computed on that same test set is not independent and is not presented here as such. The B2.2 figures are a **description of the behaviour of the frozen model on a known set**, not an independent estimate of future error.

Proposed protocol (steps 1, 2 and 6 are implemented in the code since 15 October, see B2.5; step 5, Option A, was executed on 17 October, see B2.6):

| Step | What to do | Rule |
|---|---|---|
| 1. Selection | Choose features, clip, hyperparameters and champion only with validation by engine inside train (`GroupKFold` 5 folds, or 20 fixed validation engines with a recorded seed). Record scikit-learn version and seed | Reading test metrics to decide is forbidden |
| 2. Promotion gate | Change `02_train.py` to compare the validation RMSE by engine (not the test one) and require an improvement larger than the fold-to-fold deviation | The test set does not take part |
| 3. Freeze | Train with all of train, register the version and fix it | |
| 4. Test report | Evaluate **once** on FD001 test, labelling it "seen reference set" | Do not iterate afterwards |
| 5. Fresh evaluation | Option A: hold out 20 train engines completely (training, selection and tuning) and cut them at random cycles like the test set. Option B: FD003 (same condition, two fault modes) to measure out-of-distribution generalisation; requires retraining and declaring it is another problem | Only then can independence be claimed |
| 6. Uncertainty | Report the variability (fold-to-fold deviation, bootstrap intervals by engine) instead of a single RMSE | |

Development estimate executed (`evidence/dev_protocol_heldout_train.py`, `dev_protocol_heldout_train.json`): 5 folds by engine on train only; every held-out engine is cut at a cycle with the actual RUL drawn
from the marginal RUL distribution of the test set (without using the per-engine labels nor the test model); unclipped actual RUL; 200 repetitions.
Mean RMSE **18.04**, deviation across repetitions **1.24**, percentiles 5 and 95: 15.9 and 19.8 (scikit-learn 1.9.1, pandas features). OOF RMSE over every cycle with the clipped label: 16.12.
This figure **is not an independent evaluation** (the hyperparameters were chosen with the same train set) but shows that the expected typical error is about 16 to 20 cycles and that the range from different cuts alone (±1.2) is larger than the difference between environments.

### B2.6 Option A executed: fresh evaluation on untouched engines (17 October 2026)

Script: `scripts/independent_holdout_eval.py`. Evidence: `audit/evidence/independent_holdout_eval.json`.

20 of the 100 FD001 train engines (fixed seed 2026, listed in the evidence file) were set aside
**before** anything else touched the data: no fitting, no `GroupKFold` selection, no
hyperparameter tuning ever saw them, in this run or any earlier one (hyperparameters are the
already-frozen `HGB_PARAMS`, imported unchanged — nothing is re-tuned here). The model was
trained on the remaining 80 engines only, then each of the 20 held-out engines was truncated at
a randomly chosen cycle (same convention as the official test set and as
`validation_rmse_by_engine`) and scored once at that cut.

Result of this single run: **RMSE 23.97, MAE 19.18, PHM08 277.2, mean bias −0.82** (no
systematic early/late skew). This is **worse** than both the seen-test-set RMSE (18.44) and the
train-only validation RMSE (19.72, B2.5) — expected and reported as-is, not adjusted: this run
trains on 20 fewer engines than the champion, draws a single random 20-engine holdout (so it
carries real sampling variance; the B2.5 development estimate above shows a ±1.2 to ±1.9 spread
from cut-seed variance alone on the *same* training set, and here the training set itself also
changes), and is a single run, not averaged over repetitions the way B2.5 is.

What this does and does not show:
- It **does** show the model generalises, in a scientifically meaningful sense, to engines that
  were never in any training, tuning, or selection step of this exercise, within the same FD001
  operating regime and fault mode. The errors are not degenerate (no negative or wildly
  implausible RUL predictions in the per-engine evidence) and the bias is near zero.
- It does **not** supersede or invalidate the published champion metrics: the champion itself
  (trained on all 100 engines) was never retrained or replaced by this exercise, and this
  holdout figure is not comparable cycle-for-cycle to the test RMSE.
- It is a single run. A fair uncertainty estimate would repeat this with several holdout seeds,
  the way B2.5 repeats the cut seed 200 times; that repetition was not done here and is listed
  as an open follow-up, not claimed as done.

Option B (FD002/FD003/FD004) was **not executed**: those subsets are not present in this
project's local `data/cmapss/` (only FD001 is), and this environment's network access does not
reach NASA's data repository to download them. Even if it did, FD002 to FD004 introduce multiple
operating conditions and, for FD003/FD004, a second fault mode — running the FD001-trained
champion on them would not be a like-for-like independent evaluation of the same problem, and
presenting such a number without that caveat would be exactly the kind of misleading equivalence
the mission brief warns against. If this is wanted, it requires: downloading the official
FD002–FD004 files, deciding and declaring whether to retrain per subset or test cross-regime
generalisation explicitly, and reporting regime/fault-mode differences alongside any RMSE. None
of that was done here; this paragraph is the proposal, not a result.

### B2.5 Change applied in the code: promotion without the test set (15 October 2026)

Files: `src/lib/modeling.py` (`validation_rmse_by_engine`, `promotion_decision`), `src/02_train.py`, `tests/test_modeling_selection.py` (4 tests).
Local evidence: `evidence/validation_protocol_train_only.py` and `.json`.

- `validation_rmse_by_engine`: 5-fold `GroupKFold` by `unit` **on train only**. Every held-out engine is cut at a cycle fixed by
  a seed (actual RUL between 0 and 145, without using the test distribution) and evaluated as in the test set (last observed cycle, unclipped RUL):
  `rmse_val_truncated`. The out-of-fold RMSE over every cycle (`rmse_val_all_cycles`) and the fold-to-fold deviation (`std_folds`) are also computed.
- `promotion_decision`: without a champion the first version is promoted; if the champion has no recorded `rmse_val_truncated` (the case of the current v1),
  the new version stays as `challenger` and the decision is manual; otherwise it is promoted only if it improves by more than `std_folds / sqrt(k)`.
  **The test RMSE does not take part** (a test checks that `02_train.py` no longer contains the comparison `cand["rmse_test"] <`).
- `02_train.py` also records `sklearn_version`, `selection_metric` and the validation metrics in MLflow, in the version tags and in
  `gold_model_metrics` (new columns `rmse_val_truncated`, `rmse_val_all_cycles`, `std_folds`, with `mergeSchema`).

Local result with the champion's hyperparameters (scikit-learn 1.9.1, pandas features, 20,631 rows, 100 engines):

| Model | `rmse_val_truncated` | `rmse_val_all_cycles` | `std_folds` |
|---|---|---|---|
| Ridge (baseline) | 22.11 | 19.36 | 2.04 |
| HGB (candidate) | 19.21 | 16.12 | 3.14 |

With this rule HGB would have been promoted over Ridge (improvement 2.9 > margin 1.4) without looking at the test set. The figure 19.21 is **a
development estimate** (the hyperparameters were chosen with the same train set) and matches in order of magnitude the 18.4 to 19.1 of the test.

**Training was not run in the workspace** with the new code during the audit: doing so would create version 2 of the model (which would stay as
`challenger` because v1 has no validation metric) without adding anything to this audit. The new code was deployed in the bundle
(`A_52`) and in the `codigo` volume (`A_50`); the champion and `gold_predicciones` remained those of v1. Status at the audit: **protocol implemented,
verified with unit tests and local evidence; not exercised in the workspace (NOT_VERIFIED in Databricks)**.

---

## B3. MLflow and serving

### Registry and artifacts (Unity Catalog API, `evidence/workspace/uc_model_versions.json`)

| Element | Value |
|---|---|
| Model | `workspace.mantenimiento.rul_model`, version **1**, state READY (only version) |
| Alias | `champion` = v1 (query `.../aliases/champion` returns version 1) |
| Run | `rul_entrenamiento` (job 134653962208443, notebook `02_train`), FINISHED |
| Logged parameters | `n_features 85`, `n_train_rows 20631`, `rul_clip 125` |
| Logged metrics | `rmse_test 18.441849882005343`, `mae_test 13.478027648560577`, `phm08_test 655.0239471190539`, `rmse_mejor_baseline 21.796289834949274` |
| Version tags | `dataset=C-MAPSS FD001`, `rmse_test=18.4418` |
| Signature | inputs: 85 mandatory `double` columns (`cycle`, 14 sensors, 5 statistics per sensor); output: `float64` tensor of shape (-1) |
| Artifacts | `MLmodel`, `conda.yaml`, `input_example.json`, `model.pkl` (1.2 MB, cloudpickle), `python_env.yaml`, `requirements.txt`, `serving_input_example.json` |
| Environment | Python 3.12.3, scikit-learn 1.7.2, MLflow 3.12.0, pandas 2.3.3 |

Observations: the metrics are called `*_test` and were logged on the test set (see B2.4). The model stores the scikit-learn version in the artifact but not as an MLflow parameter.

### Endpoint

Real endpoint: `dev_jjrm199867_rul-model-dev` (id `f83f37b7...`), entity `rul-model` -> `workspace.mantenimiento.rul_model` v1, CPU Small, scale to zero, `READY`.
The served version is **1, fixed by variable** (`model_version` in `databricks.yml`); the endpoint does not follow the alias. If another version is promoted without changing the variable, `champion` and the endpoint will diverge.
Permissions: `CAN_MANAGE` only owner and `admins`.

### End-to-end test (engine 34)

Data of one engine -> features -> model -> endpoint -> API -> dashboard, with the same values at every link:

| Link | Evidence | Value for engine 34 |
|---|---|---|
| Data and features | `gold_features` (split test, cycle 203 = last), 85 features | `cycle 203`, `s2 643.74`, `s4 1427.49`, `s11 48.13`, `s4_mean5 1427.362`, `s11_mean10 48.064`, `s11_slope10 0.023515` (full row in `workspace/sql_gold_features_test_last_cycle_85feats.json`) |
| Model | `rul_model` v1 @champion | Actual test RUL: 7 |
| Endpoint | `POST /serving-endpoints/dev_jjrm199867_rul-model-dev/invocations` with `dataframe_split` of that row | **3.118922691130997** |
| Scoring table | `gold_predicciones` (`scored_at 2026-10-13T20:26:50Z`, `model_version 1`) | 3.12 |
| Deployed API | `GET /api/engines` from the authenticated app session | `rul_pred 3.12`, `estado critical`, `model_version 1` |
| Dashboard | Same response as the API (rendered by `index.html`) | 3 estimated cycles, state critical |

Two other engines: engine 60 (endpoint 82.26835521034641, table/API 82.27, actual 100) and engine 85 (124.90846155881731; 124.91; actual 118).
Same test for the **100 engines** in a single call: maximum difference between `round(endpoint, 2)` and `gold_predicciones.rul_pred` = **0.00**; 0 discrepancies; endpoint RMSE against the actual RUL = 18.441849882005343, identical to the MLflow `rmse_test`
(`evidence/b3_endpoint_vs_gold_all100.json`). The CLI query (`serving-endpoints query`) returned the same value as the REST call.

**Mandatory clarification:** the app does **not** invoke the endpoint. It reads `gold_predicciones` through SQL, written by `03_score.py` with the same model. The app
service principal has no permission on the endpoint (nor needs it). What is checked is that both paths give the same. The endpoint exists as
an independent inference interface, but the dashboard does not depend on it; if the endpoint is down, the dashboard keeps showing the last batch scoring with its `scored_at`. The interface did not yet show the scoring
date (the API returns it; the field `model.scored_at` is added to the overview).

### Silent fallback

Path reviewed (`app/app.py`): there is no call to the model and no generation of values. Defects found and fixed:
- A row with a null `rul_pred` was turned into RUL 0 and received the action text of a critical engine (`e.get("rul_pred") or 0.0`). Now it stays `rul_pred: null`, state `unknown`, and the text "No prediction available for this engine."; it is excluded from the KPIs.
- If there were no metrics for the model version, the app showed the metrics of the last training of **another** version. Now it shows "--" (`rmse: null`).
- Any SQL failure was returned as `ready: false` with HTTP 200, indistinguishable from "no data yet". Now: 503 `data_unavailable` (B4).
- The last pipeline update used `DESCRIBE DETAIL` on a materialized view and always failed (deployed overview: `missing: ["gold_features"]`, `last_update: null`). Replaced by `MAX(_ingest_ts)`.

### B3.5 Revalidation of 15 October 2026 (`evidence/revalidation_2026-10-15/`)

- At 22:16 UTC the endpoint `dev_jjrm199867_rul-model-dev` **did not exist** (`A_13`, `A_25`, `A_29`, `A_36`): the invocation returned
  "The given endpoint does not exist". On 14 October at 00:5x it answered. There is no accessible record of who or what deleted it (`NOT_VERIFIED`).
  It was recreated with `bundle deploy -t dev` (`A_52`, "Created model_serving_endpoints.rul_endpoint", new id `dd2a3f66...`), `READY` at 22:27 (`A_53`).
- Real invocations after the recreation (`A_54`..`A_62`), same `gold_features` rows as on 14 October:

| Input | Response | Same as 14 October |
|---|---|---|
| Engine 34 (`api post .../invocations`) | 3.118922691130997 | yes |
| Engine 34 (`serving-endpoints query`) | 3.118922691130997 | yes |
| Engine 60 | 82.26835521034641 | yes |
| Engine 85 | 124.90846155881731 | yes |
| 100 engines in one batch | 100 values; `round(., 2)` = `gold_predicciones.rul_pred` on all 100 (maximum difference 0.00); RMSE against the actual RUL 18.441849882005343 (`b3_endpoint_vs_gold_all100_2026-10-15.json`) | yes |
| `s11` missing | explicit schema error | yes |
| string in `s7` | "Failed to convert column s7 to type float64" | yes |
| 85 `null` features | **120.6590326691026** (prediction without data) | yes: D-02 is still open on the endpoint |

- Table `gold_predicciones` (`A_44`, `A_45`): 100 rows, 99 distinct values, 3.12 to 124.98, RMSE 18.4417 (on values rounded to 2 decimals),
  `scored_at 2026-10-14T00:52:37Z`, `model_version 1`; **100 rows with the new advisory text and 0 with the old one** (the B5 note is closed).
- Deployed app (`app_api_capture_2026-10-15.json`): `/api/engines` engine 34 = 3.12 critical, 60 = 82.27, 85 = 124.91; `/api/overview` shows
  `model.version 1`, `scored_at`, `source: batch scoring table gold_predicciones (no live inference)`, `uncertainty.calibrated false`;
  `/api/health` ok on warehouse, index and LLM; `/api/overview` without credentials: 401. The served `index.html` has the same SHA-256 as the one on the PC.
- Full chain verified that day: `gold_features` (row of engine 34) -> recreated endpoint (3.1189) -> `gold_predicciones` (3.12) -> `/api/engines` (3.12) -> dashboard (3 cycles, critical).
- Permissions of the recreated endpoint (`A_64`): `CAN_MANAGE` owner and `admins`; the app SP has no `CAN_QUERY` (it does not need it).

---

## B4. Negative tests

### In the app (`tests/test_negative_api.py`, 25 tests)

Run against the original `app.py` (copy of the base commit): **10 fail** (`evidence/b4_negative_tests_against_original_app.txt`); against the corrected version: **25 passed**.

| Case | Original behaviour | Corrected behaviour |
|---|---|---|
| SQL error, timeout, unreachable warehouse (3 cases) | 200 with `ready:false` | 503 `{"error":"data_unavailable"}`, without `engines`, `kpis` or `history` |
| Missing table | empty state | explicit empty state with `missing:["gold_predicciones"]` |
| Unconfigured warehouse | empty state | same |
| Connector timeout | no limit | `_socket_timeout` 90 s configurable |
| Row without RUL (`null`/NaN) | `500` or an invented RUL 0 | `rul_pred:null`, `estado:"unknown"`, no action, excluded from KPIs |
| Metrics of another version | shown | `null` |
| Partial failure of auxiliary queries | marked as "missing table" | `errors` list, partial KPIs with `--` |
| Engine 0, negative, 10001 | 422 | 422 |
| Non-numeric engine (`abc`, `1.5`) | 422 | 422 |
| Unknown engine (999) | empty 200 | 404 |
| Question missing, empty, blank (`"   "`), 2 characters, 501 characters, `null`, number, wrong field, invalid JSON | a blank question reached the assistant | 422 without calling the LLM |
| LLM down / index down | 502 / 503 (already covered in `test_app_api.py`) | same |

"Missing sensors" and "incomplete data" are not app inputs (it accepts no telemetry): they are tested on the endpoint and in the scoring. "Model unavailable": the app does not depend on the model; in `03_score.py` `get_model_version_by_alias` fails with a clear error if there is no champion.

### On the endpoint (real invocations, `evidence/b3_trace_engine34_and_endpoint_negatives.json`)

| Input | Response | Assessment |
|---|---|---|
| Column `s11` missing | Schema error (explicit) | PASS |
| String in `s7` | "Failed to convert column s7 to float64" | PASS |
| Empty batch | Model error (0 samples) | PASS |
| `null` in `s7` | 3.118922691130997 (identical to the value with data) | FAIL (accepted without warning) |
| `null` in 11 relevant features (`s4`, `s11`, their means, `cycle`...) | 67.24 instead of 3.12 | FAIL (different prediction without error) |
| 85 `null` features | **120.66** | FAIL (prediction invented from nothing) |
| `s4` and `s11` = -1e9 | 3.82 | FAIL (no range check) |
| `cycle = 0` | 5.04 | FAIL (impossible value accepted) |
| Unknown extra column | Ignored, returns 3.1189 | Weak (permissive contract) |

Cause: `HistGradientBoostingRegressor` handles `NaN` natively and MLflow converts `null` to `NaN`, so the "required" signature does not prevent nulls. Impact: it only
affects whoever calls the endpoint directly (the app does not). Mitigation implemented in the tree: `validate_feature_frame` (`src/lib/cmapss.py`) rejects empty frames, missing columns, `NaN`/`inf` and `cycle < 1`, and `src/03_score.py` runs it before predicting.
Pending (requires a decision): do the same inside the served model itself (`pyfunc` wrapper and new version), or always put a validator in front.
**NOT_VERIFIED:** behaviour under a long cold start or endpoint timeout (the endpoint answered in under 90 s from "Scaled to zero"); an outage could not be forced.

---

## B5. Operational recommendations

Texts reviewed: `src/lib/cmapss.py` (`recommended_action`), `src/03_score.py` (column `accion`), `app/app.py` (duplicate of the former) and the fallback in `index.html`.

Before (categorical orders based on an experimental model):
- critical: "Schedule shutdown and replacement within N cycles. Inspect s4 and s11 now."
- watch: "Increase inspection frequency and plan the intervention within the N-cycle window."
- healthy: "Normal operation. Keep the usual maintenance plan."

Problems: it orders shutdown and replacement with a countdown in cycles without basis; "now" and "within N cycles" suggest a precision the model does not have (typical error of the order of 18 cycles, with 23 % of engines with error > 20); the uncertainty does not appear; "Normal operation" asserts an operational state the model cannot guarantee (in B2.2, 6 of 26 engines with actual RUL <= 35 were predicted `healthy`).

After (identical in lib, app and frontend fallback; a test checks that `lib` and `app` generate exactly the same text):
- critical: "Demo estimate; requires engineering review. Estimated RUL is low (about N cycles): consider prioritising an inspection. Sensors to check first: s4 and s11."
- watch: "Demo estimate; requires engineering review. Estimated RUL is moderate (about N cycles): consider closer monitoring and planning an inspection."
- healthy: "Demo estimate; requires engineering review. No degradation flagged by the model; keep the usual maintenance plan."

Distinctions now reflected:

| Concept | Where |
|---|---|
| Statistical prediction | `rul_pred` (model estimate at the last observed cycle) |
| Uncertainty | `uncertainty` in `/api/overview`: `calibrated: false`, `typical_error_cycles` = test RMSE (18.44), with the note that the test set also influenced the selection and that there are no prediction intervals |
| Demo state | `estado` (thresholds 20 and 35 cycles, parameterisable) |
| Advisory recommendation | `action`, always with "Demo estimate; requires engineering review" |
| Operational decision | Not taken by the system; the texts order neither shutdown nor replacement |

No confidence interval was invented. The RMSE is quoted as a typical error measured on a specific set, not as an interval. The uncertainty was not calibrated (for example with quantile regression or conformal prediction on validation by engine): it remains future work.
Tests updated/added: `test_normalize_state_y_accion`, `test_acciones_son_orientativas_y_sin_ordenes_categoricas` (forbids `shutdown`, `replace`, `now.`, `must`, `immediately`...), `test_texto_de_accion_identico_en_app_y_lib`, `test_estados_antiguos_y_acciones_en_ingles`.
Note: after running the `score` task again (E04, 14 October 00:52 UTC) the `accion` column of `gold_predicciones` contains the new text in all 100 rows (`A_45`: 0 rows with "shutdown" or "replacement").

---

## Code files modified by this phase (for deployment)

- `app/app.py`, `app/static/index.html` (only error states, action text fallback and null RUL handling)
- `src/lib/cmapss.py` (advisory text, `validate_feature_frame`), `src/03_score.py` (prior validation, header text)
- `tests/test_negative_api.py` (new), `tests/test_app_api.py`, `tests/test_cmapss.py`
- `src/lib/modeling.py`, `src/02_train.py`, `tests/test_modeling_selection.py` (B2.5, 15 October)
- Done: `src/lib` copied to the `codigo` volume (E01 and A_50), bundle deployed to dev (E03, E05, A_52), `score` task executed (E04), app restarted (E06, A_12).
