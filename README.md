# Mantenimiento predictivo de turbinas con Databricks

Proyecto de demostración para portafolio de ingeniería industrial: de los datos de sensores de turbofanes
(NASA C-MAPSS FD001) a una sala de control web en inglés que muestra la vida útil restante (RUL) de cada motor,
su estado, una recomendación orientativa y un asistente que responde con citas verificadas a partir de manuales técnicos en PDF.

Pensado para **Databricks Free Edition** (octubre de 2026): todo es serverless, no hay clústeres clásicos y
no se descarga nada de internet en tiempo de ejecución salvo `pip`. Uso no comercial, datos públicos, coste cero.

> **Aviso.** Es un prototipo sobre datos de simulación. No está certificado para mantenimiento aeronáutico real ni
> para ninguna decisión operativa. Las recomendaciones de la app llevan siempre el texto "Demo estimate; requires engineering review".

## Resultados (tal como figuran en la auditoría)

Las cifras salen de `audit/` y cada una tiene su evidencia en `audit/evidence/`. No hay ninguna cifra en este README que no esté allí.

**Modelo de RUL** (`HistGradientBoostingRegressor`, 85 variables, versión 1 con alias `champion` en Unity Catalog):

| Medida | Valor | Fuente |
|---|---|---|
| RMSE en el último ciclo de los 100 motores de test de FD001 (workspace, scikit-learn 1.7.2) | **18,4 ciclos** (MAE 13,5; PHM08 655; R2 0,80) | `audit/02_MODEL_VALIDATION.md` B2.2, `evidence/champion_v1_metrics_from_gold_predicciones.json` |
| Misma medida en local (scikit-learn 1.9.1, variables pandas) | 19,10 ciclos (PHM08 727) | `scripts/resultado_local.json`, reproducible con `python scripts/entrenar_local.py` |
| Baselines en el mismo test | media constante 43,07; Ridge 21,80 | `audit/02_MODEL_VALIDATION.md` B2.2 |
| Motores cercanos al fallo (RUL real <= 30, n = 25) | RMSE 14,3; sesgo +5,5 ciclos (tiende a sobrestimar) | `audit/02_MODEL_VALIDATION.md` B2.2 |
| Validación por motor solo con train (GroupKFold 5, corte aleatorio por motor) | RMSE 19,21 (desviación entre pliegues 3,14); Ridge 22,11 | `audit/02_MODEL_VALIDATION.md` B2.5, `evidence/validation_protocol_train_only.json` |

Dos matices que la auditoría obliga a declarar:

- **El test de FD001 es un conjunto visto, no independiente.** La regla histórica de promoción del champion comparaba el RMSE de test, y
  esa cifra se publicó y se comparó entre entornos. Por eso el 18,4 describe el comportamiento del modelo congelado sobre un conjunto
  conocido y no es una estimación independiente del error futuro. La app lo dice en pantalla (`uncertainty.note` en `/api/overview`) y
  declara la incertidumbre como no calibrada.
- **18,4 frente a 19,10.** El modelo del workspace se reprodujo bit a bit (RMSE 18,441849882005343) con scikit-learn 1.7.2 sobre `gold_features`.
  La diferencia con la medida local se debe a la versión de scikit-learn (1.9.1) y al ruido numérico de ~1e-9 entre las variables calculadas con pandas
  y con Spark; perturbando las variables con ese ruido el RMSE oscila entre 18,29 y 18,98. Ninguna de las dos cifras es "mejor": citar "18,4 a 19,1 ciclos" es lo honesto.

**Protocolo de promoción actual (solo con train).** Desde el 15 de octubre `src/02_train.py` ya no compara el RMSE de test para mover el alias:
calcula `rmse_val_truncated` con `GroupKFold` por motor sobre train (cada motor retenido se corta en un ciclo fijado por semilla y se evalúa
como el test) y promueve solo si mejora en más de `std_folds / sqrt(k)`. Si el champion no tiene esa métrica (caso de la v1), la versión nueva queda
como `challenger` y la decisión es manual. Está implementado y probado (`tests/test_modeling_selection.py`), pero **no se ha ejecutado todavía en el workspace**.

**Asistente RAG v2** (`app/rag.py`), medido sobre un conjunto held-out de 85 preguntas en español, inglés y francés que no se usó para decidir ningún cambio
(`eval/rag_eval_set_holdout.json`), con juez independiente de otra familia de modelos (`databricks-meta-llama-3-3-70b-instruct`):

| Métrica (held-out, versión final C, ejecución 6) | v1 | v2 final |
|---|---|---|
| Estado correcto (responde o se abstiene cuando toca) | 0,788 | 0,976 |
| hit@6 / MRR / Recall@6 de páginas | 0,826 / 0,653 / 0,741 | 0,971 / 0,842 / 0,888 |
| Afirmaciones mostradas respaldadas (juez independiente) | 0,358 | 0,940 (parciales 0,060; no respaldadas 0,000) |
| Página citada existe en el índice | 1,000 | 1,000 |
| Precisión de citas | 0,685 | 0,989 |
| Violaciones `must_not` afirmativas (cifras de fabricante, procedimientos inventados, texto inyectado) | n/d | 0 |

Detalle, limitaciones y cronología honesta de los cambios en `audit/03_RAG_VALIDATION.md`; revisión manual del caso obligatorio
(inspección del compresor de alta presión) en `eval/MANUAL_REVIEW.md`. La versión final C solo tiene una ejecución sobre el held-out; con
temperatura 0,1 algunas preguntas cambian de estado entre ejecuciones, así que las tasas tienen una resolución de 1/85.

## Qué demuestra (mapa a la especialización)

Especialización "Enterprise AI and Data Engineering with Databricks", cinco cursos:

| Curso | Dónde se ve en el proyecto |
|---|---|
| 1. Fundamentos del lakehouse | Arquitectura medallion, Unity Catalog (`catálogo.schema.tabla`, volumes `raw`, `manuales` y `codigo`), cómputo serverless, Free Edition |
| 2. Ingeniería de datos con Delta Lake | Lakeflow Spark Declarative Pipelines (`pyspark.pipelines`), Auto Loader, expectations, streaming tables y materialized views (`src/pipeline/`) |
| 3. ML con MLflow | Entrenamiento con baseline, MLflow 3, firma e input example, modelo en Unity Catalog con alias `champion`, promoción por validación solo con train (`src/02_train.py`, `src/03_score.py`) |
| 4. IA generativa y LLM | RAG con AI Search (índice Delta Sync, embeddings gestionados), LLM de Foundation Model APIs, verificación de afirmaciones y citas, evaluación con held-out y juez independiente (`src/04_rag_index.py`, `src/05_eval_rag.py`, `src/07_eval_assistant.py`) |
| 5. Gobierno y MLOps | Declarative Automation Bundles (`databricks.yml`), CI/CD con GitHub Actions y OAuth M2M, permisos de mínimo privilegio para la app, 142 pruebas, auditoría con evidencia |

## Arquitectura

```mermaid
flowchart LR
  subgraph Volumes
    RAW[(raw/cmapss<br/>train, test, RUL)]
    MAN[(manuales<br/>PDF)]
    COD[(codigo<br/>copia de src/lib)]
  end
  RAW -->|Auto Loader| B[bronze_cmapss_lecturas<br/>bronze_cmapss_rul]
  B --> S[silver_lecturas<br/>silver_rul_test]
  S --> G[gold_features<br/>gold_test_verdad<br/>gold_salud_curva]
  G --> T[02_train<br/>MLflow 3]
  T --> M[(rul_model@champion<br/>Unity Catalog)]
  M --> P[03_score<br/>gold_predicciones]
  M --> E[Endpoint de serving<br/>CPU Small, scale to zero]
  T --> GM[gold_model_metrics]
  MAN -->|pypdf, troceo| C[manual_chunks<br/>Delta + CDF]
  C --> I[AI Search<br/>índice Delta Sync]
  P --> APP[Databricks App<br/>FastAPI + SQL warehouse]
  GM --> APP
  G --> APP
  I --> APP
  APP --> LLM[Foundation Model APIs<br/>gpt-oss-120b]
  I --> EV[07_eval_assistant<br/>held-out + juez]
```

La app **no llama al endpoint de serving**: lee `gold_predicciones` (scoring por lotes) y lo declara en `/api/overview` (`model.source`).
El endpoint existe para integraciones directas y se verificó que devuelve exactamente los mismos valores que la tabla en los 100 motores.

Decisiones de diseño:

- **Bronze y silver son streaming tables**: los ficheros llegan al volume y solo se añaden; Auto Loader procesa
  únicamente lo nuevo. La limpieza de silver es fila a fila, así que no necesita estado.
- **Gold son materialized views**: las ventanas móviles, las estadísticas de referencia y la unión con la verdad de test
  necesitan el historial completo de cada motor.
- **Etiqueta RUL**: `RUL = min(ciclos restantes, 125)` en train. El recorte a 125 es práctica estándar en la literatura de
  C-MAPSS (Heimes, 2008; Li, Ding y Sun, 2018). La evaluación usa la RUL real de test, sin recorte.
- **Sensores descartados en silver**: `setting3`, `s1`, `s5`, `s10`, `s16`, `s18`, `s19` tienen desviación típica exactamente 0 en
  train y test; `s6` solo toma dos valores. Lo comprueba `tests/test_cmapss.py` con los datos reales. Quedan 14 sensores.
- **Variables**: valor, media y desviación móvil en ventanas de 5 y 10 ciclos y pendiente en 10 ciclos por sensor, más el ciclo (85 variables).
  Ventanas solo hacia atrás; la auditoría comprobó la ausencia de fuga de futuro con 40 pruebas de truncado.
- **Una definición, dos implementaciones**: `src/lib/cmapss.py` (pandas) es la referencia testeable sin Spark y
  `src/lib/spark_features.py` la implementa con funciones de ventana de Spark. `tests/test_spark_equivalence.py` comprueba que dan los mismos
  números (diferencia relativa máxima 2,4e-9 en 170 sumas de columnas); se omite si no hay Java.
- **Sensores principales por motor**: sensibilidad local por oclusión. No son valores SHAP.
- **Indicador de salud** (curva de la app): combinación lineal de z-scores respecto a los primeros 30 ciclos. Es una vista resumen, no una predicción.
- **Recomendaciones orientativas**: los textos de `critical`, `watch` y `healthy` no ordenan paradas ni sustituciones y empiezan siempre por
  "Demo estimate; requires engineering review". Una prueba comprueba que `lib` y `app` generan exactamente el mismo texto y otra prohíbe
  palabras como `shutdown`, `replace`, `immediately`.
- **Convención de nombres**: en Free Edition hay un único schema de trabajo, así que las capas se identifican por prefijo (`bronze_`, `silver_`, `gold_`).

### Asistente RAG v2: cómo responde

1. La pregunta (ES, EN o FR) se reescribe a una consulta técnica en inglés y se expande en varias consultas; la recuperación es híbrida con fusión RRF y k = 6.
2. Las frases con forma de instrucción se eliminan de los pasajes y de la pregunta antes de llegar al modelo: los documentos son datos, no órdenes.
3. El LLM devuelve JSON estructurado con resumen, afirmaciones citadas (documento y página), huecos y una nota de fabricante.
4. **Verificación de citas**: cada afirmación se contrasta con el fragmento citado (veredicto `SUPPORTED`, `PARTIAL` o `NOT_SUPPORTED`, más apoyo léxico);
   las no respaldadas se retiran y se listan en `removed_claims`. Si el fragmento limita el enunciado a un tipo de motor ("On centrifugal flow engines")
   y la afirmación lo omite, pasa a parcial con la condición anotada.
5. **Estados**: `answered`, `insufficient_evidence`, `requires_manufacturer_documentation` (la pregunta pide cifras o nombra un modelo de motor:
   los valores con unidades se retienen, también en el resumen) y `not_found`.
6. La interfaz muestra el estado, las fuentes con página, las afirmaciones retiradas y la traza de recuperación. Todo llega por `/api/assistant`.

## Estructura

```
databricks.yml              bundle: variables, targets dev y prod
resources/                  pipelines.yml, jobs.yml, serving.yml, app.yml
src/00_setup.py             schema, volumes (raw, manuales, codigo), copia de los datos
src/pipeline/               bronze.py, silver.py, gold.py (Lakeflow SDP)
src/02_train.py             entrenamiento, validación por motor solo con train, registro y promoción
src/03_score.py             validación de variables y puntuación de los 100 motores (gold_predicciones)
src/04_rag_index.py         PDF a fragmentos, tabla con CDF, índice de AI Search
src/05_eval_rag.py          recall@k y MRR del retrieval
src/06_permisos_app.py      GRANT de lectura al service principal de la app
src/07_eval_assistant.py    evaluación del asistente: dev y held-out, comprobaciones deterministas, juez independiente
src/lib/                    lógica pura y testeable (sin Spark salvo spark_*.py)
app/                        Databricks App: app.yaml, requirements.txt, app.py, rag.py, health_projection.py, static/index.html
tests/                      pytest (142 pruebas: funciones puras, equivalencia Spark, API, 25 negativas, RAG, selección de modelo)
scripts/                    entrenar_local.py (RMSE local), demo_local.py (app sin Databricks)
data/cmapss/                datos de NASA y FUENTE.md
eval/                       conjuntos de evaluación del RAG, resultados por ejecución, MANUAL_REVIEW.md, INFORME_RAG.md
audit/                      01_BASELINE, 02_MODEL_VALIDATION, 03_RAG_VALIDATION, 04_SECURITY_QA, FINAL_READINESS_REPORT y evidence/
portfolio/                  caso de estudio, capturas reales, carrusel (HTML, PDF, PNG), posts, guion de demo, SOURCES.md
.github/workflows/          deploy.yml
```

## Cómo desplegar

**Con la CLI (recomendado)**

```bash
databricks auth login --host https://<tu-workspace>
databricks bundle validate -t dev --var warehouse_id=<id-del-warehouse>
databricks bundle deploy   -t dev --var warehouse_id=<id-del-warehouse>
```

El host del workspace no se escribe en el bundle: va en un perfil de la CLI o en `DATABRICKS_HOST`. `warehouse_id` es el SQL warehouse
2X-Small de Free Edition (Compute, SQL warehouses, Connection details).

**Workaround de lectura de workspace files.** En runtimes serverless recientes, leer ficheros normales del workspace desde un notebook puede
fallar con `OSError: [Errno 5] Input/output error`. Por eso los notebooks buscan el paquete `lib` primero en el volume `codigo` y los datos se
suben al volume `raw` con la CLI en lugar de copiarse desde el repositorio:

```bash
databricks fs cp -r src/lib dbfs:/Volumes/workspace/mantenimiento/codigo/lib --overwrite
databricks fs cp -r data/cmapss dbfs:/Volumes/workspace/mantenimiento/raw/cmapss --overwrite
```

Hay que repetir la primera copia cada vez que cambie `src/lib` (el bundle despliega el código, pero los notebooks cargan la copia del volume).
`00_setup.py` crea los volumes y, si los datos ya están en `raw`, no vuelve a leerlos del workspace.

**Orden de ejecución**

1. Desplegar el bundle. En el primer despliegue comenta la línea `resources/serving.yml` de `databricks.yml`: el endpoint necesita una versión del modelo que aún no existe.
2. Subir `src/lib` y los datos a los volumes (comandos anteriores).
3. Subir los PDF de los manuales públicos al volume `manuales`. Los PDF no están en el repositorio.
4. Job `mantenimiento_end_to_end`: `00_setup`, pipeline bronze, silver y gold, `02_train` y `03_score`.
5. Job `mantenimiento_rag`: `04_rag_index` y `05_eval_rag`. Crea el endpoint de AI Search la primera vez (tarda varios minutos).
6. App: `databricks bundle run mantenimiento_app -t dev` o desde la UI. Después ejecuta `src/06_permisos_app.py` para dar lectura al service principal de la app.
7. Serving (opcional): descomenta `resources/serving.yml`, fija `model_version` con la versión champion y vuelve a desplegar. El endpoint apunta a una
   versión concreta, no sigue al alias. En `mode: development` el nombre real lleva prefijo de usuario (`dev_<usuario>_rul-model-dev`).
8. Evaluación del asistente (opcional): job `mantenimiento_eval_assistant` con parámetros `dataset` (`dev` o `holdout`), `configs` y `eval_judge_endpoint`.
   Los resultados se escriben en el volume `codigo` y se copian a `eval/resultados/`.

**Antes de una demo**: `databricks apps start mantenimiento-predictivo-dev`, comprobar `/api/health` (warehouse, índice y LLM en `ok`) y, si el
endpoint de serving no existe, `databricks bundle deploy -t dev`.

**CI/CD:** `.github/workflows/deploy.yml` ejecuta los tests en cada PR sin acceso a secretos, valida el bundle en PR y despliega a `dev` en cada push
a `main` (entorno `dev`, para el que conviene configurar aprobación manual). Secretos del repositorio: `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`,
`DATABRICKS_CLIENT_SECRET` (OAuth M2M). Nunca hay tokens en el repositorio. El target `prod` existe como referencia; en Free Edition no hay service
principals de cuenta para un `run_as` separado.

## Límites de Free Edition y lo que se observó

| Límite | Cómo se respeta o qué pasó |
|---|---|
| Solo serverless, sin clústeres | Pipeline `serverless: true`; jobs con `environments` serverless; ningún clúster definido |
| Internet restringido a dominios de confianza | Los datos viajan en el repositorio y se suben al volume; solo se usa `pip` en tiempo de ejecución (`pypdf`, `databricks-ai-search`) |
| 1 SQL warehouse 2X-Small | La app consulta con ese warehouse, declarado como recurso con `CAN_USE`, con caché de 30 s. El 15 de octubre estuvo unos minutos sin poder arrancar (error transitorio de la plataforma) |
| 1 endpoint de AI Search de 1 unidad | Se reutiliza si existe; índice `TRIGGERED` (sin sincronización continua) |
| Las apps se paran solas | La app se paró a las 24 h y también minutos después de usarla el 15 de octubre: hay que arrancarla antes de cada demo. **La disponibilidad no es continua** |
| Persistencia del endpoint de serving | El endpoint **desapareció del workspace** entre el 14 y el 15 de octubre sin registro accesible; se recreó con `bundle deploy -t dev`. No afecta al dashboard (lee la tabla por lotes) |
| 1 pipeline activo por tipo | Un único pipeline en modo triggered, lanzado desde el job |
| Máx. 5 tareas de job concurrentes | Jobs secuenciales, `max_concurrent_runs: 1` |
| Serving sin GPU ni provisioned throughput | Endpoint CPU `Small` con scale to zero; el LLM es un endpoint pay-per-token de Foundation Model APIs |
| Sin consola de cuenta | Sin federación OIDC: el CI usa OAuth M2M con un service principal del workspace |

## Seguridad

Resumen de `audit/04_SECURITY_QA.md` (22 controles, revalidados el 15 de octubre):

- Sin secretos en el árbol ni en el historial de Git; el host del workspace y los identificadores del warehouse y de la app se han sustituido por
  marcadores en `audit/evidence/`.
- La app exige sesión del workspace (`/api/*` responde 401 sin credenciales; `/healthz` responde 200 sin datos). `CAN_MANAGE` solo para el propietario y `admins`.
- El service principal de la app tiene `USE CATALOG`, `USE SCHEMA` y `SELECT` sobre el schema, `CAN_USE` sobre el warehouse y nada más
  (ni `MODIFY`, ni `EXECUTE` sobre el modelo, ni `CAN_QUERY` sobre el endpoint).
- SQL con parámetros enlazados y nombres validados por regex; interfaz sin `innerHTML` (la salida del LLM se muestra como texto); logs sin preguntas del usuario ni tokens.
- Errores explícitos: un fallo SQL devuelve 503 `data_unavailable`, nunca un estado vacío con 200; una RUL nula no se sustituye por 0 (25 pruebas negativas).
- Prompt injection: pasajes y preguntas se tratan como datos, filtro de frases con forma de instrucción (EN, ES, FR) y verificación de cada afirmación mostrada.
  La inyección dentro de un pasaje indexado no se probó en vivo.
- `pip-audit` sin avisos en las dependencias directas; un aviso en `oauthlib` transitivo (no explotable: la app es cliente, no servidor OAuth).

Hallazgos abiertos (ninguno bloqueante para una demo): el endpoint de serving acepta nulos y valores absurdos sin error (D-02; `03_score.py` valida antes de
puntuar y la app no lo usa), sin límite de tasa al LLM para usuarios del workspace (D-04), sin cabeceras CSP (D-06), rangos abiertos en `requirements-dev.txt` (D-07).

## Tests y demo local

```bash
pip install -r requirements-dev.txt
python -m pytest -q -rs                # 142 pruebas; la equivalencia con Spark necesita Java (se omite si falta)
python scripts/entrenar_local.py       # RMSE en test FD001 real (con --tune busca hiperparámetros por validación cruzada en train)
python scripts/demo_local.py           # la app con datos locales, sin Databricks (el asistente responde con texto fijo)
```

## Auditoría y portafolio

- `audit/FINAL_READINESS_REPORT.md`: criterios de aceptación, qué funciona realmente y problemas pendientes.
- `audit/01_BASELINE.md`: recursos desplegados, inventario y revalidación del 15 de octubre.
- `audit/02_MODEL_VALIDATION.md`: integridad de datos, reproducción bit a bit, explicación de 19,10 frente a 18,44, contaminación del test y protocolo nuevo, endpoint, pruebas negativas.
- `audit/03_RAG_VALIDATION.md`: método, held-out de 85 preguntas, cambios en `rag.py` y su justificación, caso del HPC, limitaciones.
- `audit/04_SECURITY_QA.md`: controles, hallazgos abiertos y revalidación.
- `portfolio/CASE_STUDY_EN.md`, `portfolio/carousel/`, `portfolio/screenshots/` (capturas reales de la app desplegada, sin retoques), `portfolio/SOURCES.md` (de dónde sale cada cifra).

## Método de trabajo

Jonathan Rincon definió la arquitectura, los parámetros, la secuencia de trabajo y los criterios de aceptación, y revisó los resultados
de cada fase. Las cifras de este README se han cotejado con los ficheros de `audit/`.

## Coste cero y datos

Todo corre en Free Edition, con cuotas de uso razonable. No hay servicios de pago ni claves externas.

**Datos:** NASA C-MAPSS FD001 (Saxena et al., 2008, NASA Ames Prognostics Data Repository), dominio público.
Procedencia y comprobaciones en `data/cmapss/FUENTE.md`. Los manuales del RAG usados en la evaluación son públicos (FAA-H-8083-32B y Saxena et al., 2008);
no están en el repositorio. Si usas otros, revisa su licencia. El código es MIT (`LICENSE`).
