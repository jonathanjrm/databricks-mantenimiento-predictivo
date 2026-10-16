# Portfolio readiness handoff (2026-10-17)

This branch is deliberately **not** deployed to Databricks. Do not merge before CI and human review.

## Already changed
- `app/health_projection.py`: crossing beyond `max_ahead` now returns no estimated crossing rather than a false crossing at the display horizon.
- `tests/test_app_api.py`: two regression tests for within-horizon and out-of-horizon crossings.

## Outstanding work (priority order)
1. **Serving validation** — DONE at the code/local-verification level; the live endpoint step still needs authorization. `src/lib/serving.py` adds `ValidatedRULModel`, an `mlflow.pyfunc.PythonModel` that wraps the champion sklearn regressor and validates every request before scoring: exact feature set (no missing/duplicate/unexpected columns), numeric and finite values (no null/NaN/Inf), and the one physically-grounded bound that applies here (`cycle >= 1`). No per-sensor physical range is enforced — C-MAPSS is a simulated benchmark with no documented operational sensor limits, and inventing one would be an unfounded claim; this gap is declared, not silent. `tests/test_serving.py` (14 tests) covers all-null, one-null, NaN, Inf, missing columns, extra columns, duplicate columns, non-numeric column, `cycle < 1`, empty frame, column-order independence, and — critically — two **real MLflow save/load round trips** (`mlflow.pyfunc.save_model` → `load_model`, and the `log_validated_model` helper against a local sqlite tracking store), so the tests exercise the same code path a serving endpoint runs, not just the Python class in memory. `src/08_register_validated_model.py` is the notebook that would register this wrapper as a **new, separate model version** in Unity Catalog (never moves `champion`) and immediately re-verifies it with real inference (valid input matches the champion exactly; an all-NaN row raises). It has not been run against the workspace — needs the authorization/credentials this environment does not have (see Phase 6). Once run, the still-open step is testing the **actual deployed REST endpoint**, not just a local `mlflow.pyfunc.load_model`.
2. **Methodology**: preserve the disclosure that FD001 test data influenced historical development. Verify train-only grouped selection and frozen hyperparameters. Add a clearly labelled independent evaluation (e.g. genuinely untouched external benchmark with appropriate regime handling) only if scientifically valid. Never claim a fresh independent score from an already seen test set.
3. **CI/CD**: the 2026-10-16 GitHub Actions run `38978657182` passed `test`; `validate` and `deploy-dev` were skipped. Investigate `DEPLOY_ENABLED`, environment protections, service principal permissions and action pinning. Never print or commit credentials. Run the full validation/deploy jobs only after explicit authorization.
4. **Deployment**: Databricks Free Edition is not always available; app uses batch `gold_predictions`, not the serving endpoint. Verify actual model version, endpoint and app behavior after deployment. Check that `src/lib` is copied to the `code` volume where required. Capture fresh evidence without secrets.
5. **Security**: evaluate CSP/security headers, LLM quota/rate limits, sanitized logs and dependency pinning. Test regressions and document residual risk.

## Release gates
- `ruff check .`
- `ruff format --check .`
- `python -m pytest -q -rs` (no unexpected skips)
- GitHub Actions tests green on branch/PR
- independent review of diff and tests
- no new secrets, no inflated claims, no changes to main or production without approval
- update README, audit report and portfolio numbers only after independently verified results

## Honest claim boundaries
- NASA C-MAPSS FD001 is a simulation benchmark, not operational aircraft telemetry.
- FD001 RMSE 18.44 is documented but the historical test set was seen.
- The dashboard shows batch scoring, not online endpoint inference.
- The RAG final held-out evaluation was one run with limited human review.
- A GitHub workflow green status does not prove deployment if deployment jobs were skipped.

## Session closeout (17 October 2026)

Worked entirely on `fix/portfolio-readiness-2026-10` (PR #1), never on `main`; no merge, no
deploy, no champion/alias change, no Unity Catalog write. Five commits pushed this session:
`a227270`, `489dad8`, `87895d6`, `faa80bd`, `5b398e1`. 144 -> 165 tests passing, 0 skipped; `ruff
check` and `ruff format --check` clean throughout.

Per phase:
1. **PR verification**: fixed a real gap the existing tests missed -- `health_projection.py` and
   `/api/engines/{unit}/health` could leak `NaN`/`Inf` into the JSON response from a corrupt
   sensor reading. 6 regression tests added.
2. **Serving validation (D-02)**: `src/lib/serving.py` (`ValidatedRULModel`) and
   `src/08_register_validated_model.py`, verified with 14 tests including two real MLflow
   save/load round trips. Registering it against the live workspace and testing the actual REST
   endpoint is NOT done -- needs Databricks credentials this environment does not have.
3. **Model validity**: `GroupKFold`/train-only protocol re-confirmed with the existing tests
   (already solid). Executed the previously-proposed-but-not-run independent holdout evaluation
   (Option A): RMSE 23.97 on 20 untouched engines, reported as worse than the seen-test and
   validation figures, not spun. Option B (FD002-4) explicitly not run (data not available, no
   network access to NASA's repository) and not presented as equivalent to FD001.
4. **RAG**: broke the already-collected 85-question held-out run down by language for the first
   time (es 1.000, en 0.968, fr 0.947 status accuracy); traced the 2 flagged `must_not_violations`
   to their exact sentences and showed they are negated-sentence false positives in the eval
   harness, not unsafe claims. No new live queries were run (no Databricks access).
5. **CI/CD and security**: confirmed the `validate`/`deploy-dev` skip is the intended
   `DEPLOY_ENABLED` gate, not a bug. Pinned the three GitHub Actions to verified commit SHAs
   (resolved via `git ls-remote`, not guessed). Closed D-06 (security-headers middleware, with a
   CSP that had to allow inline scripts/styles after actually reading `index.html` -- it has no
   build step and the whole frontend is one inline `<script>` block). Re-verified D-11 (oauthlib)
   independently: `databricks-sql-connector` 4.6.0 is still latest and still caps
   `oauthlib<4.0.0`, and the app's own code has no OAuth/PKCE handling of its own to be vulnerable.
6. **Live Databricks validation**: not done. This environment has no Databricks CLI credentials.
   Needs to be run by the owner, or in a session with those credentials, following this document's
   outstanding-work list and `src/08_register_validated_model.py`'s own printed next steps.
7. **Professional materials**: README updated with the test count (165), the D-02/D-06 status, and
   the new independent-holdout result -- no claim was strengthened, two were added with their
   actual (unflattering) numbers attached.

GO/NO-GO: unchanged from `FINAL_READINESS_REPORT.md`, **`PUBLICATION_READY`** with the same
caveats, none of which this session's commits close on their own (they still need the live
Databricks step above and a merge decision, both pending the owner's authorization).
