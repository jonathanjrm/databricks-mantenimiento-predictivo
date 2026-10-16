# Sources for every number used in portfolio/

Every figure in `CASE_STUDY_EN.md`, the LinkedIn posts, `DEMO_SCRIPT_EN.md` and the carousel comes from the audit
reports or the evidence files listed here. Nothing was measured for the portfolio itself except the live checks of
2026-10-15 recorded in `audit/evidence/C_hpc_2026-10-15.json`. Paths are relative to the project root.

| Figure | Value | Source |
|---|---|---|
| Train rows / engines | 20,631 / 100 | `audit/02_MODEL_VALIDATION.md` B1 (table "Procedencia y conteos"); `audit/evidence/b1_data_integrity.json` |
| Test rows / engines | 13,096 / 100 | same |
| Bronze, silver, gold rows | 33,727 each | `audit/01_BASELINE.md` section 2 and 6.3; `audit/evidence/workspace/sql_counts.json` |
| Sensors dropped / kept | 7 of 21 dropped (s1, s5, s10, s16, s18, s19 with zero variance; s6 quantisation noise) plus constant setting3; 14 kept | `02_MODEL_VALIDATION.md` B1 "Variables eliminadas" |
| Features | 85 (cycle + 14 sensors x 6 statistics) | `02_MODEL_VALIDATION.md` B2.1; `audit/evidence/workspace/uc_model_versions.json` (`n_features 85`) |
| RUL label rule | max cycle minus cycle, clipped at 125; 0 discrepancies | `02_MODEL_VALIDATION.md` B1 "Etiqueta RUL"; `revalidation_2026-10-15/A_42_rulcheck.log` |
| Backward-window leak test | 40 checks, 0 discrepancies | `02_MODEL_VALIDATION.md` B1 "Ventanas sin informacion futura" |
| pandas vs Spark | 170 column sums, max relative diff 2.37e-9; last-cycle rows max abs diff 3.7e-10 | `02_MODEL_VALIDATION.md` B1; `audit/evidence/features_sums_pandas_vs_workspace.csv` |
| Model | HistGradientBoostingRegressor, max_iter 300, lr 0.05, 31 leaves, min_samples_leaf 40, l2 1.0 | `02_MODEL_VALIDATION.md` B2.1; `src/lib/modeling.py` |
| RMSE / MAE / R2 / PHM08 (champion v1, FD001 test, last cycle) | 18.44 / 13.48 / 0.803 / 655 | `02_MODEL_VALIDATION.md` B2.2; `audit/evidence/champion_v1_metrics_from_gold_predicciones.json`; MLflow `rmse_test 18.441849882005343` |
| Baselines RMSE | constant mean 43.07; cycle-only linear 33.34; Ridge 21.80 | `02_MODEL_VALIDATION.md` B2.2 baselines table |
| Error by RUL band | 0-30: n 25, RMSE 14.31, bias +5.5; 31-60: 14, 8.50, +3.0; 61-90: 15, 18.95, +5.7; 91-125: 35, 17.77, -1.5; 126+: 11, 32.25, -28.6 | `02_MODEL_VALIDATION.md` B2.2 "Errores por intervalos" |
| Near failure | 25 engines with actual RUL <= 30: RMSE 14.31, MAE 8.53, bias +5.5; 6 of 26 engines with RUL <= 35 shown as healthy | `02_MODEL_VALIDATION.md` B2.2 |
| Bit-for-bit reproduction | RMSE 18.441849882005343 with scikit-learn 1.7.2 on `gold_features` | `02_MODEL_VALIDATION.md` B2.3; `audit/evidence/ws_retrain_sklearn172.json` |
| Local 19.10 vs workspace 18.44 | explained by scikit-learn version (1.9.1 vs 1.7.2) and ~1e-9 feature noise; sensitivity 18.29 to 18.98 | `02_MODEL_VALIDATION.md` B2.3 |
| Train-only by-engine estimate | RMSE 18.04, spread 1.24 over 200 random cuts (p5 15.9, p95 19.8) | `02_MODEL_VALIDATION.md` B2.4; `audit/evidence/dev_protocol_heldout_train.json` |
| New promotion gate (train only) | HGB `rmse_val_truncated` 19.21 vs Ridge 22.11; std_folds 3.14 and 2.04 | `02_MODEL_VALIDATION.md` B2.5; `audit/evidence/validation_protocol_train_only.json` |
| Test-set contamination | champion rule compared `rmse_test`; the test set is declared "seen" | `02_MODEL_VALIDATION.md` B2.4 |
| MLflow registry | `workspace.mantenimiento.rul_model` v1 READY, alias champion, signature 85 doubles -> float64 | `02_MODEL_VALIDATION.md` B3; `audit/evidence/workspace/uc_model_versions.json` |
| Serving endpoint | `dev_<user>_rul-model-dev`, CPU Small, scale to zero; recreated 2026-10-15 by `bundle deploy` | `02_MODEL_VALIDATION.md` B3 and B3.5; `revalidation_2026-10-15/A_52`, `A_53` |
| Engine 34 trace | endpoint 3.118922691130997; table and API 3.12; dashboard 3 cycles; actual RUL 7 | `02_MODEL_VALIDATION.md` B3 and B3.5; `audit/evidence/b3_trace_engine34_and_endpoint_negatives.json`; `revalidation_2026-10-15/app_api_capture_2026-10-15.json` |
| Endpoint vs table, 100 engines | 0 mismatches, max abs diff 0.00 after rounding to 2 decimals; RMSE 18.441849882005343 | `audit/evidence/b3_endpoint_vs_gold_all100.json` and `_2026-10-15.json` |
| Endpoint negative inputs | missing column rejected; string rejected; all-null row accepted (120.66), finding D-02 | `02_MODEL_VALIDATION.md` B4 |
| App negative tests | 25 tests, 10 failed against the original app, 25 pass after the fix | `02_MODEL_VALIDATION.md` B4; `tests/test_negative_api.py` |
| Dashboard KPIs | 100 engines, 14 critical, 7 watch, mean RUL 74.5, thresholds 20 and 35 | `revalidation_2026-10-15/app_api_capture_2026-10-15.json` |
| Advisory wording | "Demo estimate; requires engineering review. ..." in 100 of 100 rows | `02_MODEL_VALIDATION.md` B5; `revalidation_2026-10-15/A_45_pred_summary.log` |
| RAG index | 720 chunks; FAA-H-8083-32B (500 pages) and Saxena et al. 2008 (9 pages) | `audit/03_RAG_VALIDATION.md` header |
| Dev eval set / held-out set | 12 questions / 85 questions (es 35, en 31, fr 19) | `03_RAG_VALIDATION.md` section 2; `eval/rag_eval_set.json`, `eval/rag_eval_set_holdout.json` |
| Held-out, final version C (run 6) | correct status 0.976; hit@6 0.971; MRR 0.842; page Recall@6 0.888; citation precision 0.989; 0 NOT_SUPPORTED shown claims (pipeline verifier and independent judge); appropriate abstention 1.000; false abstention 0.000; coverage 0.983; 5.52 LLM calls and 14.6 s mean (p90 22.7 s) per question | `03_RAG_VALIDATION.md` section 5.2 table, column "v2 C r6"; `eval/results/results_holdout_run6_v2finalC.json` |
| v1 baseline on the same set | correct status 0.788; 62.6 % unsupported shown claims; abstention 0.560 | `03_RAG_VALIDATION.md` section 5.2, column "v1 r1"; `eval/results/results_holdout_run1.json` |
| Dev set after final version | status 1.000; MRR 0.944; Recall@6 0.846; 92.9 % supported, 7.1 % partial | `03_RAG_VALIDATION.md` section 5.1; `eval/results/results_dev_after_finalC.json` |
| Judge disagreement | 5 % (40 of 801 claims), almost all SUPPORTED vs PARTIAL | `03_RAG_VALIDATION.md` section 5.3 |
| HPC mandatory test | 9 of 9 reviewed v2 answers without an invented HPC procedure | `03_RAG_VALIDATION.md` section 6; `eval/MANUAL_REVIEW.md` section 1 |
| HPC live recheck 2026-10-15 | `not_found` (Spanish, 9.9 s; English eval-set wording, 4.9 s); `insufficient_evidence` with `missing_qualifiers` (UI preset, 25.2 s) | `audit/evidence/C_hpc_2026-10-15.json`; `03_RAG_VALIDATION.md` section 11 |
| Test suite | 142 passed, 0 skipped (Java 21, PySpark 4.2); 67 on `main` | `audit/01_BASELINE.md` section 6.2 |
| Security scans | no secrets in tree or history; pip-audit clean on app pins (one transitive `oauthlib` advisory, not exploitable); bandit 0 high-confidence findings | `audit/04_SECURITY_QA.md` sections 1 and 5; `audit/evidence/*_2026-10-15.txt` |
| Workspace availability | app auto-stopped by Free Edition ("App compute was stopped due to workspace or account status"); endpoint had disappeared between 14 and 15 October | `01_BASELINE.md` 6.3; `02_MODEL_VALIDATION.md` B3.5; `logs/B_01_app_get.log`, `logs/B_02_app_start.log` (PC) |
| Code commit baseline | `cd81b7a` on `main`; 13 modified files and new tests only in the working copy (not pushed) | `01_BASELINE.md` section 6.1; `FINAL_READINESS_REPORT.md` G-01 |

Numbers that are deliberately not used: any "independent" test RMSE (none exists, see B2.4), any confidence interval
(uncertainty is not calibrated), any throughput or uptime figure (not measured).
