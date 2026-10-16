# Demo script (60 to 90 seconds)

Status of the recording: NOT_VERIFIED. No video was recorded: the tooling available during the audit (the built-in
browser's screenshot action, no screen recorder) cannot produce a genuine recording and the mission forbids fabricating
one. The script below is mapped shot by shot to real screenshots in `portfolio/screenshots/` so that the author can
record it in one take against the live app. Before recording: check `/api/health` (the app auto-stops on Free Edition;
start it with `databricks apps start predictive-maintenance-dev` or `bundle run maintenance_app -t dev`; the audited
deployment was named `mantenimiento-predictivo-dev`) and
confirm the serving endpoint exists (`bundle deploy -t dev` recreates it if not).

Target length 75 s at a calm reading pace (about 190 words of narration). Narration in English.

| # | Time | Shot (what is on screen) | Real screenshot it corresponds to | Narration |
|---|---|---|---|---|
| 1 | 0 to 8 s | Carousel slide 3, the architecture diagram, or the README diagram | `carousel/png/slide-03.png` (diagram, not a screenshot) | "A predictive-maintenance demo on Databricks Free Edition: NASA C-MAPSS data, a medallion pipeline, a RUL model in MLflow, a Databricks App and a manuals assistant. Everything was audited before publishing." |
| 2 | 8 to 20 s | App root, desktop: header chips, KPIs, lineage strip, uncertainty strip | `12_dashboard_desktop_800w_2026-10-15.jpg`, `02_dashboard_top_800w.jpg` | "The dashboard reads batch scores from a gold table and says so: model version 1, scoring time, typical error 18.4 cycles on a seen test set, and the near-failure bias. Service chips show the warehouse, the index and the LLM." |
| 3 | 20 to 30 s | Click CRITICAL 14 on the fleet; the 14 red units stay, the rest dim | `04_fleet_filter_critical_800w.jpg` | "One hundred engines in an isometric fleet. Height is the estimated remaining life, colour the demo state. Filter by criticality: fourteen engines are critical by the demo thresholds." |
| 4 | 30 to 42 s | Click engine 34 (front-left, highlighted); engine card with 3 cycles, actual 7, advisory text, sensors s4, s11, s2; health curve | `03_engine34_detail_and_curve_800w.jpg`, `01_dashboard_desktop_1057w.jpg` | "Engine 34: three cycles estimated, seven actual. The recommendation is advisory by design: demo estimate, requires engineering review. The three sensors that most change the estimate come from occlusion, not SHAP. The audit followed this exact row from the features through the serving endpoint to this card." |
| 5 | 42 to 60 s | Click the preset "How is the high-pressure compressor inspected?"; wait 10 to 30 s (loading state); answer with status, claims, flags | `11_assistant_loading_state_800w_half.jpg`, `13_assistant_hpc_answer_800w_2026-10-15.jpg` | "Ask the manuals assistant about inspecting the high-pressure compressor. The handbook has no HPC procedure, and the assistant says so: insufficient evidence, generic compressor inspection only, and one claim flagged because the source limits it to centrifugal-flow engines." |
| 6 | 60 to 72 s | Scroll the answer: gaps, manufacturer note, removed statement, sources with CITED badges and page numbers, retrieval trace | `14_assistant_hpc_gaps_sources_800w_2026-10-15.jpg`, `06_assistant_hpc_sources_trace_800w.jpg`, `07_assistant_hpc_retrieval_trace_800w.jpg` | "Every statement carries its page. Unsupported statements are removed and listed. The retrieval trace shows the rewritten queries and every chunk with its score." |
| 7 | 72 to 84 s | Terminal or notebook: `databricks registered-models get workspace.maintenance.rul_model` (audited deployment: `workspace.mantenimiento.rul_model`), or the saved API output `audit/evidence/workspace/uc_model_versions.json` scrolled on screen | No screenshot of the MLflow UI exists (NOT_VERIFIED as a capture); API evidence: `audit/evidence/workspace/uc_model_versions.json`, `audit/evidence/b3_endpoint_vs_gold_all100.json` | "In MLflow: version 1, alias champion, 85-feature signature, RMSE 18.44. The endpoint was invoked for all one hundred engines and matched the dashboard with zero mismatches." |
| 8 | 84 to 90 s | Carousel slide 9 (verified vs limitations) or slide 10 | `carousel/png/slide-09.png`, `slide-10.png` | "Simulation data, uncalibrated uncertainty, batch scores. An audited prototype, not a certified system. I defined the architecture and the parameters. Everything is in the repository." |

Notes for the recording:

- Record at 1440 x 900 or wider so the fleet and the engine card are side by side (the audit captures were taken at
  the built-in browser's 800 px pane, which stacks them).
- The assistant answer varies between runs at temperature 0.1: the HPC question may return `not_found` instead of
  `insufficient_evidence`. Both are acceptable statuses; do not re-run until a nicer answer appears, say what it did.
- Do not show the MLflow UI through automation; open it by hand if a console shot is wanted, and keep workspace URLs
  and identifiers out of frame or blur them.
- If the app is stopped, the page shows the Databricks "App Not Available" screen: start the app and wait for
  `/api/health` to report `ok` before recording.
