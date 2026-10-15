# 01. Línea base del proyecto (Fase A)

Auditoría del 13 al 15 de octubre de 2026. Rama de trabajo local `validacion` en el clon de GitHub (antes `validation`), sin commits ni push; despliegues solo al target `dev`. Todos los comandos
y resultados de este documento se ejecutaron realmente; lo que no pudo ejecutarse figura como `NOT_VERIFIED`.

## 1. Versión y entorno

| Elemento | Valor | Cómo se obtuvo |
|---|---|---|
| Commit base | `cd81b7a358a3bbebe3d468c98265616c25e64231` ("Informe de evaluación del asistente RAG...") | `git rev-parse HEAD` |
| Rama | `validacion` (cambios sin commit en el árbol de trabajo; la copia del PC no es un repositorio Git) | `git rev-parse --abbrev-ref HEAD` |
| Historial | 6 commits, un autor (Jonathan Rincon) | `git log --oneline` |
| Python local de auditoría | 3.13.16 | `python3 --version` |
| Bibliotecas locales | scikit-learn 1.9.1, pandas 3.0.5, numpy 2.5.3, pyspark 4.2.0, pyarrow 25.0.1, fastapi 0.143.0, httpx 0.28.1, pytest 9.1.1, mlflow 3.17.1 | `pip list` |
| Java | OpenJDK 21.0.12.1 (necesario para el test de Spark) | `java -version` |
| Databricks CLI | v1.20.0 | `databricks --version` (cola A, `B01_version`) |
| Entorno serverless donde se entrenó el modelo | `databricks_runtime: client.6`, Python 3.12.3, scikit-learn **1.7.2**, pandas 2.3.3, pyarrow 21.0.0, MLflow 3.12.0, cloudpickle 3.1.1 | Artefactos `MLmodel` y `requirements.txt` del modelo registrado |
| Workspace | Databricks Free Edition (solo serverless), catálogo `workspace`, schema `mantenimiento` | Brief + consultas |

Diferencia relevante: el entorno local (scikit-learn 1.9.1, pandas 3.0.5) no es el del workspace (1.7.2, 2.3.3). Es la causa
parcial de la diferencia 19,10 frente a 18,44 de RMSE (ver `02_MODEL_VALIDATION.md`, apartado B2).

## 2. Recursos realmente desplegados (listados con la CLI)

| Recurso | Estado verificado | Comando |
|---|---|---|
| Pipeline `[dev ...] mantenimiento_pipeline_dev` (id `f0ca97c9-...`) | Existe en el bundle summary | `bundle summary -t dev` |
| Jobs: `mantenimiento_end_to_end_dev` (134653962208443), `mantenimiento_rag_dev` (394502178342467), `mantenimiento_eval_assistant_dev` (616581869043888) | Listados | `jobs list` |
| Modelo UC `workspace.mantenimiento.rul_model` | 1 versión (v1, estado READY), alias `champion` apunta a v1 | API UC `models/.../versions` y `.../aliases/champion` |
| Endpoint de serving | **Nombre real: `dev_jjrm199867_rul-model-dev`** (no `rul-model-dev`: el modo `development` del bundle antepone `dev_<usuario>_`). Entidad `workspace.mantenimiento.rul_model` v1, CPU Small, scale to zero, estado READY, "Scaled to zero" al inicio de la auditoría | `serving-endpoints get`, `list` |
| App `mantenimiento-predictivo-dev` | `RUNNING`, despliegue `SUCCEEDED` (SNAPSHOT), único recurso declarado: SQL warehouse `CAN_USE` | `apps get` |
| Tablas | `bronze_cmapss_lecturas` 33.727, `bronze_cmapss_rul` 1, `silver_lecturas` 33.727, `silver_rul_test` 100, `gold_features` 33.727, `gold_test_verdad` 100, `gold_salud_curva` 33.727, `gold_predicciones` 100, `gold_model_metrics` 3, `gold_control_calidad` 2 | SQL (`audit/evidence/workspace/sql_counts.json`) |

Observaciones:
- `bundle summary` muestra la app como "(not deployed)" aunque `apps get` la devuelve RUNNING (el summary no refleja el despliegue por SNAPSHOT). No es un defecto del proyecto.
- El nombre del endpoint con prefijo de usuario debe tenerse en cuenta en cualquier documentación o script que use `rul-model-dev` literal.
- `bronze_cmapss_rul` guarda el fichero `RUL_FD001.txt` como una única fila de texto (100 valores en un campo `value`); `silver_rul_test` lo desglosa en 100 filas.

## 3. Inventario de archivos revisados

Leídos y analizados: `README.md`, `databricks.yml`, `resources/{app,jobs,pipelines,serving}.yml`, `src/pipeline/`, `src/lib/*`,
`src/02_train.py`, `src/03_score.py`, `src/04_rag_index.py`, `src/05_eval_rag.py`, `src/07_eval_assistant.py`, `app/app.py`, `app/rag.py`,
`app/health_projection.py`, `app/static/index.html`, `eval/`, `tests/`, `.github/workflows/deploy.yml`. La auditoría RAG
detallada es responsabilidad de la fase C (`03_RAG_VALIDATION.md`).

## 4. Suite de pruebas y la discrepancia 61 + 1 omitida frente a 67

Estado inicial (commit `cd81b7a`, antes de cualquier cambio de esta auditoría):

```
python3 -m pytest -q          ->  67 passed, 187 warnings in 52.06 s
```

Recuento por archivo (`pytest --collect-only`): `test_app_api` 10, `test_chunking` 7, `test_cmapss` 15, `test_rag` 26,
`test_retrieval` 3, `test_spark_equivalence` 6. Total 67.

**Explicación de "61 superadas y una omitida":** `tests/test_spark_equivalence.py` (6 pruebas) tiene un
`pytest.importorskip("pyspark")` y un `pytest.skip("Java no disponible", allow_module_level=True)` al inicio del módulo.
Si falta PySpark **o** Java, **todo el módulo** se omite y pytest lo cuenta como **una** omisión, no como seis. 67 - 6 = 61
superadas + 1 omitida. Es el patrón exacto de "61 + 1 omitida"; es coherente con una copia sin Java o sin PySpark (el entorno de esa copia no se pudo inspeccionar: `NOT_VERIFIED`).

Reproducción controlada: se ocultó Java del `PATH` (solo `python3` y `git`) y se ejecutó la suite del árbol de trabajo actual
(ampliada con pruebas nuevas, ver abajo). Resultado: `SKIPPED [1] tests/test_spark_equivalence.py:15: Java no disponible`, 61 pruebas superadas más las
del módulo omitido (`101 passed, 1 skipped` en la suite actual ampliada; el módulo de Spark se cuenta como una omisión). Conclusión: **no hay pruebas fallidas ni
eliminadas**; la diferencia es la prueba de equivalencia pandas/Spark, que sólo corre con Java. Ese es el test que
respalda la afirmación "pandas y Spark dan lo mismo", por lo que la copia sin Java **no la verifica**; aquí sí se ejecutó
(6 superadas) y además se contrastó contra las tablas reales del workspace (ver `02_MODEL_VALIDATION.md`, B1).

Estado final de la suite tras los cambios de la fase A, B y D (ejecución completa en este entorno, con Java):

```
python3 -m pytest -q          ->  107 passed, 187 warnings in 74.66 s
```

(La cifra incluye las pruebas nuevas de B4/B5 y las ampliaciones de la fase RAG, fase C.)
Con Java oculto la suite da `101 passed, 1 skipped`.

Salvedades:
- Las pruebas de la app (`test_app_api.py`, `test_negative_api.py`) usan `TestClient` con consultas SQL simuladas; no tocan
  Databricks. Verifican el contrato de la API, no la conexión real.
- `tests/test_spark_equivalence.py` usa 6 motores, no los 100. La comparación de los 100 motores se hace por separado con las
  tablas del workspace (B1).

## 5. Estado del workspace al empezar

- Alias `champion` en v1. `gold_predicciones` con 100 filas, `model_version = 1`, `scored_at = 2026-10-13T20:26:50Z`.
- `gold_model_metrics`: 3 filas (baseline media, baseline Ridge, HGB v1), una sola ejecución de entrenamiento (`trained_at 2026-10-13T18:46Z`).
- La tabla `gold_predicciones` desplegada conserva todavía la columna `accion` con el texto antiguo ("Schedule shutdown and replacement..."),
  que la app recalcula; se corregirá al volver a ejecutar `03_score` tras el despliegue del texto nuevo (ver B5).
- Permisos Unity Catalog del service principal de la app (`d19896a0-...`): `USE CATALOG`, `USE SCHEMA`, `SELECT` sobre el schema. Nada más.

## 6. Revalidación del 15 de octubre de 2026 (sesión final)

Evidencias: `audit/evidence/revalidation_2026-10-15/A_*.log` y `audit/evidence/A_worklog.md`.

### 6.1 Copia del PC frente a GitHub `main`

La copia de trabajo del usuario (`C:\Users\...\databricks-mantenimiento-predictivo`, fuente de verdad) se comparó por md5 con el
clon de GitHub (`main` = `cd81b7a`). Resultado: **12 ficheros de código difieren de `main`** y **13 ficheros o carpetas son nuevos**
(sin contar `audit/` y `portfolio/`). La copia del PC coincide fichero a fichero con el árbol de trabajo auditado en el contenedor
salvo `.gitignore` (añade `herramientas/`, `logs/`, `*.bat`; en esta sesión se añaden `cola/`, `cola2/`, `consultas/`) y
`databricks.yml` (idéntico salvo comentarios; mismo `warehouse_id`, `model_version 1`, `serverless_env_version 6`).

| Fichero | Estado frente a `main` | Origen del cambio |
|---|---|---|
| `.github/workflows/deploy.yml` | modificado (+26/-x) | D-01: secretos solo en los jobs que despliegan; `pytest -rs` |
| `app/app.py` | modificado (+279) | B3/B4/D16: 503 explícito, RUL nula, timeout SQL, `/api/health`, umbrales, trazabilidad |
| `app/rag.py`, `src/07_eval_assistant.py`, `tests/test_rag.py`, `resources/jobs.yml` | modificados | Fase C |
| `app/static/index.html` | modificado (+192) | Fase E: estados de carga y error, etiquetas predicción/dato real/indicador, filtros, chips de servicios |
| `src/02_train.py`, `src/lib/modeling.py` | modificados (esta sesión) | B2.4: promoción del champion por validación por motor solo con train |
| `src/03_score.py`, `src/lib/cmapss.py` | modificados | B4/B5: `validate_feature_frame`, texto orientativo |
| `tests/test_app_api.py`, `tests/test_cmapss.py` | modificados | pruebas de lo anterior |
| Nuevos | `tests/test_negative_api.py` (25), `tests/test_phase_e_api.py` (11), `tests/test_modeling_selection.py` (4), `eval/MANUAL_REVIEW.md`, `eval/rag_eval_set_holdout.json`, 7 ficheros `eval/resultados/results_*.json` | B4, E, B2.4, fase C |

Ninguno de estos cambios está en GitHub: el repositorio público sigue en `cd81b7a`. **La publicación requiere un commit y push que
sólo el usuario debe hacer** tras revisar el diff (los `.bat`, `cola/`, `logs/`, `consultas/`, `herramientas/` quedan excluidos por `.gitignore`).

### 6.2 Suite de pruebas (árbol del PC, contenedor con Java 21)

```
python3 -m pytest -q -rs   ->  138 passed, 0 skipped, 187 warnings in 78.74 s   (antes de añadir tests/test_modeling_selection.py)
python3 -m pytest -q -rs   ->  142 passed, 0 skipped, 187 warnings in 49.59 s   (estado final del árbol del PC)
```

Recuento por archivo: `test_app_api` 11, `test_chunking` 7, `test_cmapss` 17, `test_modeling_selection` 4, `test_negative_api` 25,
`test_phase_e_api` 11, `test_rag` 58, `test_retrieval` 3, `test_spark_equivalence` 6. En `main` (`cd81b7a`) la colección es de 67
(10+7+15+26+3+6), que confirma la explicación del apartado 4: 61 + 1 omitida = 67 sin el módulo de Spark. Las 187 advertencias son
`FutureWarning`/`Pandas4Warning` de PySpark 4.2 con pandas 3.0; no afectan al resultado.

### 6.3 Estado real del workspace el 15 de octubre (CLI, cola `A_`)

| Recurso | Estado a las 22:1x UTC | Acción | Estado posterior |
|---|---|---|---|
| App `mantenimiento-predictivo-dev` | `STOPPED`: "App compute was stopped due to workspace or account status" (parada automática de Free Edition tras ~24 h) | `apps start` (`A_12`) | `RUNNING`, despliegue `SUCCEEDED` a las 22:16:04Z (`A_32`) |
| SQL warehouse `<warehouse-id>` | `STOPPED`; `warehouses start` y las consultas SQL fallaban con "Cannot start warehouse ... Serverless Compute ... disabled in global warehouse config" (`A_20`..`A_24`, `A_34`) | Ninguna: la app lo arrancó al servir `/api/overview` | `RUNNING` a las 22:22 (`A_40`); todas las consultas posteriores correctas (`A_41`..`A_46`). El error inicial fue transitorio, pero muestra que la disponibilidad en Free Edition no está garantizada |
| Endpoint `dev_jjrm199867_rul-model-dev` | **No existe** (`A_13`, `A_25`, `A_29`, `A_36`; `A_30/A_33` solo listan endpoints Foundation Model). El 14 de octubre a las 00:5x respondía (B3). Causa `NOT_VERIFIED`: no hay registro de auditoría accesible; lo más probable es una limpieza de Free Edition de endpoints con scale-to-zero inactivos, o un borrado manual | `bundle deploy -t dev` (`A_52`) recrea el recurso declarado en `resources/serving.yml` | ver `02_MODEL_VALIDATION.md`, B3 (revalidación) |
| Jobs | 3 jobs listados; últimas dos ejecuciones de `mantenimiento_end_to_end_dev` `SUCCESS` (`A_38`) | | |
| Pipeline | `IDLE`, última actualización `COMPLETED` 2026-10-13T18:43Z (`A_11`) | | |
| Modelo `rul_model` | v1 `READY`, `run_id 4688ee42...`, creado 2026-10-13T18:46Z (`A_06`, `A_08`). `registered-models get-by-alias` no existe en la CLI v1.20.0; el alias se verificó por API en la sesión anterior (`workspace/uc_model_versions.json`) | | |
| Tablas | `bronze_cmapss_lecturas` 33.727, `silver_lecturas` 20.631 train + 13.096 test (100 + 100 motores), `silver_rul_test` 100, `gold_features` 33.727, `gold_test_verdad` 100, `gold_predicciones` 100, `gold_model_metrics` 3 (`A_41`) | | |
| Permisos de la app | `CAN_MANAGE` propietario y `admins` (`A_10`); grants UC del SP: `USE SCHEMA`, `SELECT` sobre el schema (`A_46`) | | |

Conclusión de la línea base: el pipeline, las tablas, el modelo y la app siguen siendo los auditados; el único recurso que había
desaparecido es el endpoint de serving, que el bundle vuelve a crear. La disponibilidad continua **no** forma parte de lo que se certifica.
