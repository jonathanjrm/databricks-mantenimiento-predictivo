# Screenshot inventory and provenance

All files in `portfolio/screenshots/` are real captures of the deployed dev app
`mantenimiento-predictivo-dev` (Databricks Apps, Free Edition; the current bundle deploys it as `predictive-maintenance-dev`) taken from the authenticated built-in browser of the
audit session. None of them was edited, composited or retouched; the carousel only crops them (CSS overflow) and labels
them "Real screenshot". No capture contains credentials, tokens or workspace identifiers beyond the app URL host.

The app code shown is the audited working copy (the `index.html` served by the app has the same SHA-256 as the PC copy,
see `audit/evidence/revalidation_2026-10-15/app_api_capture_2026-10-15.json`). The screenshots of 2026-10-14 were taken
after the Phase B5 wording change (advisory text "Demo estimate; requires engineering review") and the Phase E labels
(MODEL PREDICTION, TEST GROUND TRUTH, criticality filters, service chips); on 2026-10-15 the same UI was re-verified live
and four more captures were added.

Clock note: the browser and the PC VM reported 2026-10-14T22:xx UTC while the Databricks control plane reported
2026-10-15T04:3x UTC for the same moment; dates below use the Databricks (UTC) day, 2026-10-15, for the second batch.
Capture tool: the built-in browser's screenshot action (JPEG, pane-native resolution), saved without re-encoding.

| File | What it shows | Captured | Source URL / view | Kind |
|---|---|---|---|---|
| `01_dashboard_desktop_1057w.jpg` (800 x 975) | Full dashboard: header chips (pipeline, model v1, scored, SQL warehouse, index, LLM), KPIs (100 engines, 14 critical, 7 watch, mean RUL 74.5, test RMSE 18.44), lineage strip, uncertainty strip, isometric fleet with filters, engine 34 card (3 cycles, actual 7) | 2026-10-14 | app root `/`, 1057 px viewport scaled by the pane to 800 px | Real capture |
| `02_dashboard_top_800w.jpg` (800 x 1200) | Header, KPIs, lineage and uncertainty strip at 800 px | 2026-10-14 | `/` top of page | Real capture |
| `03_engine34_detail_and_curve_800w.jpg` (800 x 1200) | Health curve of engine 34 (solid = indicator from real readings, dashed = illustrative projection), engine card with advisory text, traceability line, occlusion contributions, assistant idle state | 2026-10-14 | `/` scrolled to the engine panel | Real capture |
| `04_fleet_filter_critical_800w.jpg` (800 x 1200) | Fleet with the CRITICAL filter active: 14 of 100 engines highlighted, the rest dimmed; "Showing 14 of 100 (filter: critical)" | 2026-10-14 | `/` fleet panel after clicking CRITICAL 14 | Real capture |
| `05_assistant_hpc_answer_800w.jpg` (800 x 1200) | Assistant answer to "How is the high-pressure compressor inspected?": status INSUFFICIENT EVIDENCE, three claims with page citations, one flagged "partially supported, condition omitted: centrifugal flow engines", gap and manufacturer notes, cited sources p. 415 and p. 413 | 2026-10-14 | `/` assistant panel | Real capture |
| `06_assistant_hpc_sources_trace_800w.jpg` (800 x 1200) | Same answer: expanded source [1] (p. 415 snippet), six sources with scores, retrieval trace header (config v2, coverage check general_only, automatic adjustment answered -> insufficient_evidence) | 2026-10-14 | `/` assistant panel scrolled | Real capture |
| `07_assistant_hpc_retrieval_trace_800w.jpg` (800 x 1200) | Same answer: per-query retrieval lists (page, chunk kind, score) for the rewritten queries | 2026-10-14 | `/` assistant panel scrolled to the end | Real capture |
| `08_assistant_manufacturer_required_800w.jpg` (800 x 1200) | Assistant answer to a CFM56 fan-blade bolt torque question: status MANUFACTURER DOCUMENTATION REQUIRED, one claim shown, one statement removed as NOT_SUPPORTED with the reason displayed, cited pp. 337 and 342 | 2026-10-14 | `/` assistant panel | Real capture |
| `09_mobile_header_kpis_375w.jpg` (750 x 1624, 2x) | Mobile layout (375 px): chips wrap, KPI cards in two columns, lineage strip scrolls horizontally, uncertainty strip readable | 2026-10-14 | `/` with the mobile preset | Real capture |
| `10_mobile_fleet_curve_375w.jpg` (750 x 1624, 2x) | Mobile layout: fleet and health curve stacked | 2026-10-14 | `/` mobile preset, scrolled | Real capture |
| `11_assistant_loading_state_800w_half.jpg` (393 x 600, half scale) | Loading state after sending a question: "Searching the manuals... (retrieval, answer and citation check usually take 10 to 30 s)", send button disabled | 2026-10-14 | `/` assistant panel, half-scale screenshot | Real capture |
| `12_dashboard_desktop_800w_2026-10-15.jpg` (800 x 961) | Full dashboard re-verified after restarting the app: same KPIs, chips "Pipeline data 1 d ago", "Scored 22 h ago", all services ok | 2026-10-15 | `/` pane-native size | Real capture |
| `13_assistant_hpc_answer_800w_2026-10-15.jpg` (800 x 975) | Live re-run of the HPC preset question: INSUFFICIENT EVIDENCE, four claims, two tagged "partially supported" with "condition omitted: centrifugal flow engines" rendered in the UI | 2026-10-15 | `/` assistant panel | Real capture |
| `14_assistant_hpc_gaps_sources_800w_2026-10-15.jpg` (800 x 975) | Same answer scrolled: "What the documents do not cover", manufacturer documentation note, "1 statement removed", sources [1] p. 415 and [2] p. 413 marked CITED | 2026-10-15 | `/` assistant panel scrolled | Real capture |
| `15_mobile_header_kpis_390w_2026-10-15.jpg` (780 x 1688, 2x) | Mobile layout at 390 x 844 (iPhone-class viewport) | 2026-10-15 | `/` custom viewport 390 x 844 | Real capture |

## Conceptual illustrations (not screenshots)

The carousel (`portfolio/carousel/carousel.html`) contains inline SVG drawings labelled "Illustration": the stylised
fleet on slides 1 and 10, the turbofan sketch and the synthetic degradation curve on slide 2, the medallion slabs on
slide 4. The architecture diagram (slide 3), the baseline bars and error table (slide 5), the trace chain (slide 6) and
the RAG flow (slide 8) are drawn from the configuration files and the audit tables, not from screen captures. The two
text panels on slide 6 are labelled "Rendered from API output": they transcribe values from
`audit/evidence/workspace/uc_model_versions.json` and the endpoint checks; they are not screenshots of the Databricks console.

## MLflow and Databricks console

No screenshot of the MLflow UI or the Databricks console was taken: the mission forbids automating the console UI and the
built-in browser session was used only for the app. MLflow evidence is provided as API output
(`audit/evidence/workspace/uc_model_versions.json`, `revalidation_2026-10-15/A_06_model_get.log`, `A_08_mv1.log`).
Status for "MLflow evidence as a screen capture": NOT_VERIFIED (not captured); the registry data itself is verified.

## Video

No video was recorded. The available tooling (built-in browser screenshot action, no screen recorder) cannot produce a
genuine recording, and the mission forbids fabricating one. Status: NOT_VERIFIED. `DEMO_SCRIPT_EN.md` maps the script to
the real screenshots above so a recording can be made by the author in one take.

## Availability observed while capturing

While taking the 2026-10-15 captures the app went from RUNNING to "App compute was stopped due to workspace or account
status" (Free Edition) within minutes of the first requests, and was restarted with `apps start` through the CLI queue
(`logs/B_01_app_get.log`, `logs/B_02_app_start.log` on the PC). Any live demo must start by checking `/api/health`.
