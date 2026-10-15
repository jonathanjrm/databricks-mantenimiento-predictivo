Predictive maintenance on Databricks Free Edition, built end to end and audited before publishing.

NASA C-MAPSS FD001 (100 turbofan engines run to failure, 100 truncated for test) as a working chain on serverless compute:

- Lakeflow medallion pipeline: 33,727 readings, 85 backward-window features, RUL clipped at 125. Spark and pandas agree to 1e-9.
- HistGradientBoosting RUL model in MLflow and Unity Catalog, alias champion, on a scale-to-zero serving endpoint. RMSE 18.4 cycles on the FD001 test set vs 21.8 for Ridge and 43.1 for a constant mean.
- A control-room app: isometric fleet of 100 engines, criticality filters, uncertainty strip, model version and scoring time on screen.
- A manuals assistant over the FAA Powerplant Handbook that cites pages, removes unsupported claims and withholds manufacturer values. On 85 held-out questions in three languages: 97.6 % correct status, 0 unsupported shown claims under two judges, 100 % appropriate abstention. Asked for an HPC procedure the handbook lacks, it says so.

What the audit added, since a dashboard that looks right is not evidence:
- Champion reproduced bit for bit; endpoint invoked for all 100 engines, 0 mismatches with the dashboard.
- The test set had informed model selection, so it is declared a seen set; promotion now uses train-only validation.
- Uncertainty is not calibrated; recommendations are advisory. Near failure the model overestimates life by 5.5 cycles.
- 142 tests, no secrets in tree or history, least-privilege service principal.

Limits: simulation data, batch scores not live telemetry, Free Edition availability not continuous. Not a certified maintenance system.

Method: I defined the architecture, technical parameters and sequence, and reviewed every phase. Audit reports and evidence are in the repo.

Carousel in the comments. github.com/jonathanjrm/databricks-mantenimiento-predictivo

#Databricks #MLOps #PredictiveMaintenance #RAG
<!-- character count: 1938 characters (Python len() of the post text above this comment, newlines included; mission limit 2,000) -->
