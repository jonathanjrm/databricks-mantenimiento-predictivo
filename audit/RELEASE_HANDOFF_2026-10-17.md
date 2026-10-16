# Portfolio readiness handoff (2026-10-17)

This branch is deliberately **not** deployed to Databricks. Do not merge before CI and human review.

## Already changed
- `app/health_projection.py`: crossing beyond `max_ahead` now returns no estimated crossing rather than a false crossing at the display horizon.
- `tests/test_app_api.py`: two regression tests for within-horizon and out-of-horizon crossings.

## Outstanding work (priority order)
1. **Serving validation**: build a validated MLflow pyfunc wrapper that checks exact expected feature schema, numeric finite values, nulls and defensible domain bounds; reject invalid input with explicit errors. Preserve the current model and champion alias until a new model version has been validated. Add positive/negative tests (all-null, one-null, NaN, Inf, missing/extra columns, impossible sensor values, valid 85-feature frame), and test the *actual served endpoint* after an authorized deployment. A pre-scoring check in `03_score.py` is not enough for external callers.
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
