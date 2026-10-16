# Technology stack

Versions are the ones recorded by the audit (`audit/01_BASELINE.md` section 1, `audit/04_SECURITY_QA.md` D11 and D12,
model artefacts in `audit/evidence/workspace/uc_model_versions.json`). Where a version was not pinned, the range from the
requirements file is given.

## Platform (Databricks Free Edition, serverless only)

| Component | Use in the project | Notes |
|---|---|---|
| Databricks Asset Bundles | Declares pipeline, jobs, serving endpoint and app (`databricks.yml`, `resources/*.yml`); `bundle deploy -t dev` | Development mode prefixes resources with `[dev <user>]`; endpoint name becomes `dev_<user>_rul-model-dev` |
| Unity Catalog | Catalog `workspace`, schema `maintenance` (audited deployment: `mantenimiento`): volumes (raw data, manuals, code), Delta tables, registered model | App service principal has `USE SCHEMA` and `SELECT` only |
| Lakeflow Declarative Pipelines | Bronze, silver and gold tables with expectations; serverless | Previously Delta Live Tables |
| Auto Loader | Ingestion of the C-MAPSS text files into bronze | 33,727 rows |
| Delta Lake and Spark SQL | Storage format and window functions for the 85 features; `mergeSchema` for the new validation metrics | |
| Databricks Jobs (serverless, environment version 6) | `maintenance_end_to_end` (setup, pipeline, train, score), `maintenance_rag`, `maintenance_eval_assistant` | The audited deployment used the names `mantenimiento_*` |
| MLflow 3.12.0 | Experiment tracking, model logging with signature, Unity Catalog model registry, alias `champion` | Local audit environment used MLflow 3.17.1 |
| Model Serving | CPU Small, scale to zero, model version pinned by variable | Recreated by `bundle deploy` after it disappeared (D-12) |
| AI Search (vector search) | Delta Sync index `manual_chunks_index` with managed embeddings (`databricks-gte-large-en`), hybrid retrieval | 720 chunks |
| Foundation Model APIs | `databricks-gpt-oss-120b` (query rewrite, structured answer, claim verification); `databricks-meta-llama-3-3-70b-instruct` (independent evaluation judge only) | |
| SQL warehouse (serverless 2X-Small) | Read path of the app through the Databricks SQL connector | |
| Databricks Apps | Hosts the FastAPI app; identity and warehouse access via the app's service principal and `app.yaml` `valueFrom` | Auto-stops on Free Edition when idle |
| Databricks CLI v1.20.0 | All workspace operations during the audit, through a file-queue runner on the user's PC | `databricks/setup-cli@v0.280.0` in CI |

## Modelling and data

| Component | Version | Use |
|---|---|---|
| Python | 3.12.3 (serverless job), 3.13.16 (local audit) | |
| scikit-learn | 1.7.2 (workspace, champion v1); 1.9.1 locally | `HistGradientBoostingRegressor`, `Ridge`, `GroupKFold` |
| pandas | 2.3.3 (workspace); 3.0.5 locally | Reference feature implementation, evaluation |
| NumPy | 2.5.3 locally | |
| PySpark 4.2.0 and PyArrow 25.0.1 (local) | pandas vs Spark equivalence test | Requires Java 21 |
| cloudpickle 3.1.1 | Model artefact | |

## Application

| Component | Version | Use |
|---|---|---|
| FastAPI | 0.143.0 | REST API, validation with Pydantic (`question` 3 to 500 characters, `unit` 1 to 10,000) |
| uvicorn | 0.53.0 | ASGI server |
| databricks-sql-connector | 4.6.0 | Parameterised SQL with 90 s socket timeout |
| databricks-sdk | 0.152.0 | Workspace client, Foundation Model and AI Search calls |
| Front end | plain HTML, CSS and JavaScript, inline SVG; no framework, no `innerHTML` | Isometric fleet, health curve, assistant panel; IBM Plex fonts |

## Quality, security and tooling

| Tool | Version | Result recorded |
|---|---|---|
| pytest | 9.1.1 | 142 passed, 0 skipped on the audited tree (67 on `main`) |
| httpx | 0.28.1 | `TestClient` for API tests with mocked SQL |
| ruff | 0.16.8 | No syntax errors or undefined names (notebook globals excluded) |
| bandit | 1.9.4 | 0 high-confidence findings |
| pip-audit | 2.10.1 | No known vulnerabilities in the app pins; one transitive `oauthlib` advisory in the dev environment, not exploitable here |
| GitHub Actions | `actions/checkout@v4`, `setup-python@v5`, `databricks/setup-cli@v0.280.0` | Validate, test and deploy with an OAuth M2M service principal; secrets limited to deploying jobs |
| Playwright (Chromium) | local | Renders the carousel to PNG and PDF (`portfolio/carousel/render.py`) |
| pypdf | local | Text extraction used to build the held-out evaluation set by hand |

## Data and documents

- NASA C-MAPSS FD001 (Saxena et al., 2008), public domain, via a public GitHub mirror of the NASA Ames repository.
- FAA-H-8083-32B, Aviation Maintenance Technician Handbook, Powerplant (500 PDF pages), public domain.
- Saxena, Goebel, Simon, Eklund (2008), "Damage Propagation Modeling for Aircraft Engine Run-to-Failure Simulation",
  IEEE PHM (9 pages).

## Development method

Architecture, technical parameters and sequence defined by Jonathan Rincon, who reviewed the code, pipelines, tests,
evaluation harness and audit phase by phase. Development sessions worked on a container clone and
synchronised with the PC copy; Databricks commands ran through a CLI queue on the PC.
