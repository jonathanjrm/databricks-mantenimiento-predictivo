# Worklog de la auditoría A (fases A, B, D, E, G)

Registro cronológico de lo ejecutado realmente. Horas en UTC según los logs del ejecutor de la CLI en el PC (`logs/A_*.log`).
Copias de esos logs, sin cadenas con aspecto de token, en `audit/evidence/revalidation_2026-10-15/`.

## Sesión anterior (13 y 14 de octubre de 2026)

Produjo en el contenedor de auditoría (rama `validation`, sin commits): `audit/01_BASELINE.md`, `02_MODEL_VALIDATION.md`,
`04_SECURITY_QA.md` y las evidencias `audit/evidence/*.json|csv|py|txt`, `workspace/`. Corrigió `app/app.py`, `app/static/index.html`,
`src/lib/cmapss.py`, `src/03_score.py`, añadió `tests/test_negative_api.py` y `tests/test_phase_e_api.py`, copió `src/lib` al volume
`codigo` (E01), desplegó el bundle a dev (E03, E05), volvió a ejecutar solo la tarea `score` (E04, `scored_at 2026-10-14T00:52:37Z`),
arrancó la app (E06) y capturó `/api/overview` desde el navegador autenticado (`phase_e/api_values_after_redeploy.json`).
Esos tres informes no se habían copiado a la carpeta del PC; esta sesión los revisa, los actualiza y los entrega.

## Sesión actual (14 de octubre de 2026, 22:0x a 23:xx UTC; fecha civil del usuario 15 de octubre)

| Hora UTC | Acción | Resultado |
|---|---|---|
| 22:08 | `A_00_ping` (`current-user me`) | Ejecutor de la cola activo |
| 22:10 | Inventario de la copia del PC y del clon del contenedor; md5 de 48 ficheros de código | Copia del PC = árbol de trabajo del contenedor salvo `.gitignore` y `databricks.yml` (locales). Frente a GitHub `main` difieren 12 ficheros (ver `01_BASELINE.md`, apartado 6) |
| 22:11 | Rama local `validacion` creada en el clon (sin commit) | |
| 22:12 | `python3 -m pytest -q -rs` sobre el árbol del PC | 138 passed, 0 skipped (78,7 s) |
| 22:12 | `A_01`..`A_11`: versión CLI, bundle summary, apps get, endpoint, jobs, modelo, alias, permisos, pipelines | CLI v1.20.0. App `STOPPED` ("App compute was stopped due to workspace or account status"). `registered-models get-by-alias` no existe en la CLI (se usa la API). `serving-endpoints get rul-model-dev`: no existe (nombre real con prefijo `dev_jjrm199867_`) |
| 22:15 | `A_12 apps start` | App `RUNNING` a las 22:16:04Z |
| 22:16 | `A_13` endpoint `dev_jjrm199867_rul-model-dev` | **No existe** (el 14 de octubre a las 00:5x sí existía y respondía). `A_30/A_33` listan solo endpoints Foundation Model |
| 22:16 | `A_20`..`A_24` consultas SQL | Error: "Cannot start warehouse ... Serverless Compute ... disabled in global warehouse config" (transitorio, ver abajo) |
| 22:16 | Nuevo protocolo de selección sin test: `validation_rmse_by_engine` y `promotion_decision` en `src/lib/modeling.py`, `02_train.py` promueve por validación; `tests/test_modeling_selection.py` (4 pruebas) | Pruebas superadas; `validation_protocol_train_only.json` (HGB RMSE validación truncada 19,21, Ridge 22,11) |
| 22:17 | Escaneo de secretos (árbol + historial), pip-audit, ruff, bandit | Sin secretos. Nueva advertencia `oauthlib 3.3.1` PYSEC-2026-4114 (transitiva, lado servidor OAuth, no explotable aquí; `databricks-sql-connector` fija `oauthlib<4`) |
| 22:21 | Navegador integrado: `/healthz`, `/api/health`, `/api/overview`, `/api/engines` | 200; `health` ok para warehouse, índice y LLM; overview con `model.version 1`, `scored_at 2026-10-14T00:52:37Z`; motores con `action` nueva |
| 22:22 | `A_40 warehouses get` | `RUNNING` (lo arrancó la app); el error anterior fue transitorio tras la parada de la app |
| 22:23 | `A_41`..`A_46` consultas SQL de revalidación | Conteos 20.631/13.096/100/100; RUL 0 discrepancias, 8.131 filas recortadas; métricas v1; motores 34/60/85 = 3,12/82,27/124,91; 100 acciones con texto nuevo, 0 con el antiguo; grants del SP: `USE SCHEMA`, `SELECT` |
| 22:23 | Ficheros nuevos copiados al PC (`modeling.py`, `02_train.py`, `test_modeling_selection.py`, `.gitignore`) | |
| 22:24 | `A_50 fs cp src/lib` al volume `codigo`; `A_51 bundle validate`; `A_52 bundle deploy -t dev` (recrea el endpoint ausente) | ver continuación |
| 22:25 | `A_52 bundle deploy -t dev` | "Created model_serving_endpoints.rul_endpoint"; 11 ficheros subidos, 223 eliminados del workspace (cola/logs ahora ignorados) |
| 22:27 | `A_53 serving-endpoints get` | Endpoint recreado (id `dd2a3f66...`), `READY`, `DEPLOYMENT_READY` |
| 22:28 | `A_54`..`A_62` invocaciones reales: motores 34, 60, 85, lote de 100, `s11` ausente, cadena en `s7`, 85 nulos; `A_60 serving-endpoints query` | 3,1189 / 82,2684 / 124,9085; 100 valores = `gold_predicciones` (0 discrepancias, RMSE 18,4418); errores explícitos para columna ausente y cadena; 85 nulos -> 120,66 (D-02 sigue) |
| 22:29 | Navegador integrado: `/api/engines`, `/api/overview`, `/api/health`, `credentials: omit`, SHA-256 del `index.html` servido; clic en el filtro Critical | 34 = 3,12 critical; 401 sin credenciales; SHA-256 servido = PC; filtro muestra 14 y atenúa 86 |
| 22:30 | `A_63` `gold_predicciones` completa; `A_64` permisos del endpoint recreado | Comparación de los 100 valores guardada en `b3_endpoint_vs_gold_all100_2026-10-15.json`; `CAN_MANAGE` propietario y `admins` |
| 22:33 | `POST /api/ask` en la app desplegada: caso HPC y pregunta sobre boroscopio | `not_found` sin procedimiento inventado (11,7 s); `answered` con 1 afirmación `SUPPORTED` citando p. 462 (10,5 s). `assistant_live_check_2026-10-15.json` |
| 22:35 | `01_BASELINE.md` §6, `02_MODEL_VALIDATION.md` B2.5 y B3.5, `04_SECURITY_QA.md` §5 y hallazgos D-11, D-12 escritos; 50 ficheros de `audit/` copiados al PC | |
| 22:41 | Suite final: `python3 -m pytest -q -rs` | 142 passed, 0 skipped (49,6 s) |
| 22:4x | Espera (sondeo de `portfolio/` y `audit/03_RAG_VALIDATION.md` en el PC) para la fase G | Sin cambios de la fase C a las 22:41 UTC; `FINAL_READINESS_REPORT.md` escrito con los criterios 11 a 14 como NOT_VERIFIED en el PC |

## Decisiones tomadas sin consultar

- No se ejecutó el entrenamiento en el workspace con el protocolo nuevo (crearía una v2 `challenger` sin valor para la auditoría).
- Se recreó el endpoint de serving con el bundle (recurso ya declarado en el proyecto, CPU Small con scale-to-zero, target dev).
- No se tocaron `app/rag.py`, `src/07_eval_assistant.py`, `eval/`, `portfolio/` ni `audit/03_*` (ámbito de la fase C).
- No se hizo commit ni push: el usuario debe revisar el diff de la copia del PC frente a `main`.
