# 04. Security and quality (Phase D)

Scope: Git tree, history, app and workspace configuration, dependencies, linters and CI flow. The review of the
prompt injection handling in `app/rag.py` is read-only (the RAG code is audited in `03_RAG_VALIDATION.md`).
No secrets were printed or stored; the service principal identifiers appear truncated.

> Names in this document are those of the deployment audited (Spanish schema `mantenimiento`, tables such as `gold_predicciones`). The current code uses English names; the mapping is in the README, section "Deployed names".

Summary: **no secrets in the tree or in the history; 0 critical findings.** Revalidation of 15 October 2026 in section 5:
a new warning appears in a transitive dependency (`oauthlib`, not exploitable in this app) and D-01, D-03 and D-09 are closed.
Medium and low severity findings in section 4.

## 1. Evidence per control

| # | Control | Command / method | Result | Status |
|---|---|---|---|---|
| D1 | Secrets in the Git tree | `grep -rnEi "(dapi[0-9a-f]{20,}\|client_secret\|secret\|token\|password\|api[_-]?key)"` over the tracked tree (excluding `audit/`, data and `.git`) | Only matches of the word "tokens" (text chunking) and of `max_tokens`; no credential value. `git ls-files` contains no `.env`, `.pem` or `.key` | PASS |
| D2 | Secrets in the history | `git log --all -p` and `grep -E` with patterns `dapi[0-9a-f]{20,}`, `client_secret = <value>`, `password = "..."`, `BEGIN (RSA\|PRIVATE)`, `ghp_`, `AKIA`, JWT `eyJ...`, `Bearer <token>`, `xox[bp]-` | 0 matches in the 6 commits. Authors: 2 (no third-party emails) | PASS |
| D3 | App environment variables | Reading of `app/app.yaml` | Only `DATABRICKS_WAREHOUSE_ID` (valueFrom of the resource), `CATALOG`, `SCHEMA`, `AI_SEARCH_INDEX`, `LLM_ENDPOINT`, `RAG_K`. No credential; the identity comes from the Databricks Apps environment (SDK `Config()`). `SQL_TIMEOUT_SECONDS` added in the code (optional, default 90) | PASS |
| D4 | App authentication | From the built-in browser session, `fetch` with `credentials: "omit"` to the app URL | `/api/engines` 401, `/api/overview` 401. `/healthz` answers 200 without credentials (returns only `{"status":"ok"}`) | PASS (with note D-10) |
| D5 | Authorisation in the app | Reading of `app/app.py` | The app implements no roles of its own: every user authenticated in the workspace with `CAN_USE` sees the whole fleet and can use the assistant (consumes LLM). There is no personal data and no writes. | PASS for a demo; see D-04 |
| D6 | App permissions | `apps get-permissions` / API `permissions/apps` | `CAN_MANAGE` only for the owner and the `admins` group (inherited). No public access nor whole-workspace access | PASS |
| D7 | Service principal | `apps get`, `SHOW GRANTS` | A single resource: SQL warehouse `CAN_USE`. `effective_user_api_scopes`: `iam.access-control:read`, `iam.current-user:read` | PASS |
| D8 | Unity Catalog grants | `SHOW GRANTS ON CATALOG workspace`, `ON SCHEMA workspace.mantenimiento`, `ON TABLE ...gold_predicciones`, `ON FUNCTION ...rul_model` | Principal `d19896a0-...` (app): `USE CATALOG`, `USE SCHEMA`, `SELECT` on the schema. No `EXECUTE` on the model (the app does not call the model), no `MODIFY`, no `CREATE`. No direct grants on the model. Least privilege met | PASS |
| D9 | Serving endpoint permissions | API `permissions/serving-endpoints/<id>` | `CAN_MANAGE` only owner and `admins`. The app service principal does **not** have `CAN_QUERY` (consistent: it does not use it) | PASS |
| D10 | Endpoint without input validation | 6 direct invocations (see `02_MODEL_VALIDATION.md`, B3/B4) | The endpoint accepts `null` in any feature and also a whole row of nulls (returns 120.66), absurd values (-1e9) and extra columns. It only rejects missing columns, non-numeric strings and an empty batch | FAIL (medium, see D-02) |
| D11 | App dependencies | `pip-audit -r app/requirements.txt --no-deps` (pip-audit 2.10.1, OSV/PyPI database) | "No known vulnerabilities found" (fastapi 0.143.0, uvicorn 0.53.0, databricks-sql-connector 4.6.0, databricks-sdk 0.152.0). Evidence: `evidence/pip_audit_app.txt` | PASS |
| D12 | Development dependencies | `pip-audit` over the resolved versions of the environment (`evidence/requirements-dev.resolved.txt`) | "No known vulnerabilities found". `requirements-dev.txt` declares ranges (`>=`), so the result depends on the environment | PASS (note D-07) |
| D13 | Linter | `ruff 0.16.8 check app src tests scripts --select E9,F,B --ignore F821` | 13 warnings: 6 unused imports, 7 `zip()` without `strict=`. No syntax errors or undefined names. The 114 `F821` are `dbutils`, `spark` and `display` of the Databricks notebooks (expected). Evidence: `evidence/ruff.txt` | PASS (minor quality) |
| D14 | Static security analysis | `bandit 1.9.4 -r app src scripts` | 0 high-confidence findings of real risk. 9 `B608` (SQL by strings, low confidence): false positives, catalog/schema names are validated with a regex at start-up, `q_count` validates the table name and values go as parameters (`:u`, `:v`). 1 `B324` SHA1 in `chunking.py` for chunk identifiers (non-cryptographic use). 1 `B110` (`except: pass`) in `rag.py:419`, bounded retry. 5 `B101` `assert` in notebooks. Evidence: `evidence/bandit.txt` | PASS |
| D15 | SQL injection | Review of `app/app.py` and range test | Bound parameters for `unit` and `version`; `unit` validated as an integer 1 to 10,000; tables limited by regex | PASS |
| D16 | Error handling | Code + 25 negative tests (`tests/test_negative_api.py`) | Before: any SQL failure became `ready:false` with 200 (indistinguishable from "no data yet"). Now: explicit 503 `data_unavailable`; missing table = explicit empty state. Error responses include no traces or server strings | PASS (after correction) |
| D17 | Logs | Reading of `log.*` in `app.py` | Status, number of claims and `source:page` are logged. The user question, tokens and data rows are not logged. The SQL failure log was changed to record only the exception type (before, 300 characters of the message, which could include query fragments). Two `log.warning` remain with 200/300 characters of the LLM/AI Search message | PASS (note D-08) |
| D18 | Prompt injection (review of `rag.py`, reading) | Reading of `V2_SYSTEM_PROMPT` and filters | Mitigations present: the prompt declares that passages are data and that their instructions are not followed; a regular-expression filter removes instruction-like sentences (EN/ES/FR) before passing the context; the output is structured JSON with claim verification and removal of unsupported ones. Limitation: the regular expressions do not cover every variant and the LLM acts as its own verifier; evaluated in `03_RAG_VALIDATION.md` | Partial PASS (effectiveness measured in phase C) |
| D19 | XSS in the interface | `grep` of `innerHTML`, `insertAdjacentHTML`, `eval`, `document.write` in `index.html` | 0 uses. The DOM is built with `createElement`/`textContent`, so the LLM output and the data are shown as text | PASS |
| D20 | Public exposure of technical information | `fetch` without credentials (D4) and content review | Data and assistant are not reachable without authentication. The page loads CSS from Google Fonts (request to a third party, without user data). No CSP/`X-Frame-Options` headers of its own | PASS with note (D-06) |
| D21 | External access without authentication from outside the browser | `curl` from the audit environment and from the PC | Blocked by the egress proxy (403 `blocked-by-allowlist`): inconclusive. The valid check is D4 | NOT_VERIFIED by curl; covered by D4 |
| D22 | GitHub Actions (`deploy.yml`) | Reading | See section 3 | PASS with recommendations |

## 2. Code changes derived from this phase

- `app/app.py`: a data failure other than "missing table" returns an explicit 503 (D16), `_socket_timeout` in the SQL connector (90 s,
  configurable with `SQL_TIMEOUT_SECONDS`), log without SQL message fragments (D17), blank question validation.
- Details and tests in `02_MODEL_VALIDATION.md`, B4.

## 3. Review of `.github/workflows/deploy.yml`

Correct aspects: `permissions: contents: read`; OAuth M2M authentication with a service principal (no personal tokens);
secrets only through `secrets.*`, no values in the repository; the CLI is pinned to a version (`setup-cli@v0.280.0`);
`concurrency` avoids simultaneous deployments; `deploy-dev` only on `push` to `main` and depends on `test`.

Risks and recommendations:
- **Medium.** `DATABRICKS_CLIENT_SECRET` and the rest are exposed as workflow-level environment variables, that is, also
  to the `test` job, which runs `pip install -r requirements-dev.txt` and `pytest` (code and dependencies of the PR). A PR from the same repository
  that modifies the tests could read the secret. Move `env:` to the `validate` and `deploy-dev` jobs and protect the environment with approval.
- **Low.** Actions are referenced by tag (`@v4`, `@v5`, `@v0.280.0`), not by commit SHA. Pin by SHA.
- **Low.** The workflow's own comment says to check the current tag of databricks/setup-cli: the existence of
  `v0.280.0` could not be checked (no GitHub access). `NOT_VERIFIED`. The local CLI used in the audit is v1.20.0; both should be aligned.
- **Low.** The CI installs `pyspark` but the `ubuntu-latest` runner includes Java, so the equivalence test does run in CI (unlike
  the user's local copy). There is no check that it does: add `-rs` to pytest so that skips are visible.
- **Low.** The flow deploys to `dev` with the service principal identity; the created resources are prefixed with `[dev <user>]`, not with the SP identity.
  Review before using it in production.
- The workflow was not executed (no push): `NOT_VERIFIED`.

## 4. Open findings

| ID | Severity | Finding | Recommendation |
|---|---|---|---|
| D-01 | Medium (**fixed in the tree**) | `deploy.yml` exposed deployment secrets to the test job (section 3) | Done: `env` only in `validate` and `deploy-dev`; `pytest -rs`. Pending push and a real run of the workflow (`NOT_VERIFIED`) |
| D-02 | Medium | The endpoint `dev_jjrm199867_rul-model-dev` does not validate inputs: a row of nulls returns a RUL (120.66) and a `null` in important features changes the result (3.12 to 67.24) without error | Wrap the model in a `pyfunc` function that rejects nulls, infinities and out-of-range values, or always validate before calling. `src/03_score.py` already validates with `validate_feature_frame` (new). The app does not call the endpoint, so today it only affects direct consumers |
| D-03 | Medium (**closed**) | SQL errors were presented as an empty 200 state | `app/app.py` fixed and deployed to dev (E05, A_52); verified by `tests/test_negative_api.py` and by the SHA-256 of the served `index.html` |
| D-04 | Low | Any workspace user with `CAN_USE` can consume the LLM without a rate limit | Rate limit or quota if the app is shared; Free Edition has platform quotas |
| D-05 | Low | `/healthz` reachable without authentication | Acceptable (it reveals no data); document it |
| D-06 | Low (**closed 17 October**) | No security headers (CSP, `X-Content-Type-Options`, `X-Frame-Options`) in the app responses; external font from Google Fonts | `app/app.py` now adds a `@app.middleware("http")` that sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, and a `Content-Security-Policy` scoped to exactly what the frontend loads (self, Google Fonts, no third-party scripts, `frame-ancestors 'none'`). The CSP needed `script-src 'self' 'unsafe-inline'`: `index.html`'s entire frontend is one inline `<script>` block with no build step, and a stricter policy would have shipped a blank dashboard — verified by first reading the file, not assumed. Font not self-hosted (left as a smaller follow-up). Regression test: `tests/test_app_api.py::test_security_headers_present_on_every_response` |
| D-07 | Low | `requirements-dev.txt` with open ranges; pip-audit results depend on the environment | Pin versions or generate a `requirements-dev.lock` |
| D-08 | Low | `log.warning` with 200 to 300 characters of LLM/AI Search messages | Log only the exception type, as already done with SQL |
| D-09 | Low | Local PC folders (`logs/`, `cola/`, `cola2/`, `consultas/`, `herramientas/`) are not in `.gitignore`; they contain workspace identifiers | Add them to `.gitignore` before any commit from the PC copy |
| D-10 | Informative | `/healthz` answers 200 without a session: it is unclear whether the platform or the app serves it (both return the same) | No action |
| D-11 | Low | `oauthlib 3.3.1` to `3.2.2` depending on environment (transitive dependency of `databricks-sql-connector`): PYSEC-2026-4114 / CVE-2026-49265, a timing side-channel (non-constant-time string comparison, CWE-208) in `code_challenge_method_plain`/`_s256` of oauthlib's PKCE Authorization-Code-Grant **server** handling. Independently re-verified 17 October: `databricks-sql-connector`'s latest release (4.6.0, confirmed the current latest via `pip index versions`) still declares `oauthlib>=3.1.0,<4.0.0` (checked in the wheel's own metadata), so pinning `oauthlib>=4.0.0` directly would create an unsatisfiable dependency conflict, not a fix. The advisory's own exploit scenario requires an already-intercepted `authorization_code` plus many timed requests against a token endpoint implementing PKCE verification (it calls real-world exploitability "limited"); this app only drives OAuth as a client (service-principal OAuth M2M / client-credentials, confirmed by grepping `app/*.py` and `src/lib/*.py` for any OAuth/PKCE code -- none), never runs an authorization server, and so never calls the vulnerable functions at all | No action possible until `databricks-sql-connector` lifts the `oauthlib<4.0.0` ceiling; re-run `pip-audit -r app/requirements.txt` whenever that dependency is upgraded |
| D-12 | Medium | The serving endpoint disappeared from the workspace between 14 and 15 October without an accessible record (see `02_MODEL_VALIDATION.md`, B3.5). It does not affect the dashboard (it reads the batch table) but it does affect any direct integration | Recreated with the bundle; check its existence before a demo; consider documenting that Free Edition does not guarantee endpoint persistence |

## 5. Revalidation of 15 October 2026

Same audit environment (Python 3.13.16). Evidence in `audit/evidence/*_2026-10-15.txt` and `evidence/revalidation_2026-10-15/`.

| Control | Command | Version | Result |
|---|---|---|---|
| Secrets in the PC tree and in the history | `grep -rnEIi` with patterns `dapi...`, `ghp_`, `AKIA`, `xox[bp]-`, private keys, JWT, `client_secret=`, `password=`, `Bearer`; `git log --all -p` | grep 3.x | 0 matches in both (`secrets_scan_2026-10-15.txt`); `git ls-files` without `.env`, `.pem`, `.key` |
| App dependencies | `pip-audit -r app/requirements.txt` | pip-audit 2.10.1 | 1 transitive package with 1 warning: `oauthlib 3.3.1` PYSEC-2026-4114 (D-11). `fastapi 0.143.0`, `uvicorn 0.53.0`, `databricks-sql-connector 4.6.0`, `databricks-sdk 0.152.0` without warnings (`pip_audit_app_2026-10-15.txt`) |
| Resolved development dependencies | `pip-audit -r requirements-dev.resolved_2026-10-15.txt` | 2.10.1 | Same `oauthlib` warning (`pip_audit_dev_2026-10-15.txt`) |
| Linter | `ruff check app src tests scripts --select E9,F,B --ignore F821` | ruff 0.16.8 | 15 warnings: 7 `F401` unused imports, 8 `B905` `zip()` without `strict`; 0 syntax errors (`ruff_2026-10-15.txt`) |
| Static analysis | `bandit -r app src scripts` | bandit 1.9.4 | 1 High (`B324` non-cryptographic SHA1 in `chunking.py`), 9 Medium (`B608` SQL by strings with regex-validated names and bound parameters), 6 Low; no change since 14 October (`bandit_2026-10-15.txt`) |
| App authentication | `fetch("/api/overview", {credentials: "omit"})` from the built-in browser | | 401 (`app_api_capture_2026-10-15.json`) |
| App permissions | `apps get-permissions` (`A_10`) | CLI 1.20.0 | `CAN_MANAGE` owner and `admins` |
| UC grants of the service principal | `SHOW GRANTS ON SCHEMA workspace.mantenimiento` (`A_46`) | | `d19896a0-...`: `USE SCHEMA`, `SELECT` (no `MODIFY`, `CREATE` or `EXECUTE`) |
| Permissions of the recreated endpoint | `GET /api/2.0/permissions/serving-endpoints/dd2a3f66...` (`A_64`) | | `CAN_MANAGE` owner and `admins`; the app SP does not appear |
| Automated tests | `python3 -m pytest -q -rs` | pytest 9.1.1 | 142 passed, 0 skipped (includes 25 negative, 11 of phase E, 4 of the selection protocol) |
| GitHub Actions | Reading of the corrected `deploy.yml` | | Secrets only in `validate` and `deploy-dev`; not executed (no push): `NOT_VERIFIED` |
| Prompt injection in the RAG | Read-only (phase C) | | No changes in this phase; result in `03_RAG_VALIDATION.md` |

## 6. CI/CD review (17 October 2026)

The run cited in the mission brief (`38978657182`, 16 October) passed `test` and skipped `validate`
and `deploy-dev`. Reading `.github/workflows/deploy.yml` confirms this is the intended behaviour,
not a misconfiguration: both jobs carry `if: ... && vars.DEPLOY_ENABLED == 'true'`, a repository
variable that is unset until the owner configures Databricks credentials (documented in the
workflow's own header comment and in `RELEASE_HANDOFF_2026-10-17.md`). Confirmed still true: the
`test` job never receives the Databricks secrets (D-01 above), and `deploy-dev` is additionally
scoped to a protected `dev` environment for a manual-approval gate.

Action pinning: `actions/checkout@v4`, `actions/setup-python@v5` and `databricks/setup-cli@v0.280.0`
were mutable tags (a tag can be moved to point at a different, unreviewed commit; `setup-cli` was
already pinned to one specific release, but by tag, not by commit). All three are now pinned to
the exact commit SHA of the release they already used, resolved with `git ls-remote` against the
real upstream repositories (not guessed, not taken from a web summary) and kept as a `# vX.Y.Z`
comment for readability:
`actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2`,
`actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0`,
`databricks/setup-cli@b77668807bef70152b5cf3dfa49a6f624e61c7b4 # v0.280.0`. No behaviour change:
same released versions, just addressed by their immutable SHA instead of a tag.

Still `NOT_VERIFIED`: no workflow run has been triggered from this environment (this session only
edits files and pushes to the PR branch; GitHub Actions itself runs on GitHub's infrastructure).
The push of this change will trigger `test` on the PR branch automatically; `validate` and
`deploy-dev` stay skipped until `DEPLOY_ENABLED` is set and the workspace secrets exist, per the
mission brief's instruction not to enable automatic deployment without authorization.
