# Mantenimiento predictivo de turbinas con Databricks

Proyecto de demostración para portafolio de ingeniería industrial: de los datos de sensores de turbofanes
(NASA C-MAPSS FD001) a una sala de control web que muestra la vida útil restante (RUL) de cada motor,
su estado y un asistente que responde con citas a partir de manuales técnicos en PDF.

Pensado para **Databricks Free Edition** (octubre de 2026): todo es serverless, no hay clústeres clásicos y
no se descarga nada de internet en tiempo de ejecución salvo `pip`. Uso no comercial, datos públicos, coste cero.

> RMSE medido en local: **19,10 ciclos** en el último ciclo de los 100 motores de test de FD001 (13 de octubre de 2026,
> scikit-learn 1.9.1, `HistGradientBoostingRegressor`). Score PHM08: 727. Baselines en la misma prueba: media constante
> 43,07 y regresión Ridge 21,80. Reproducible con `python scripts/entrenar_local.py`; el detalle queda en
> `scripts/resultado_local.json`. Es una medida en mi máquina con la misma lógica que `src/02_train.py`; el
> resultado dentro de Databricks puede variar ligeramente con las versiones de las librerías.

## Qué demuestra (mapa a la especialización)

Especialización "Enterprise AI and Data Engineering with Databricks", cinco cursos:

| Curso | Dónde se ve en el proyecto |
|---|---|
| 1. Fundamentos del lakehouse | Arquitectura medallion, Unity Catalog (`catálogo.schema.tabla`, volumes `raw` y `manuales`), cómputo serverless, Free Edition |
| 2. Ingeniería de datos con Delta Lake | Lakeflow Spark Declarative Pipelines (`pyspark.pipelines`), Auto Loader, expectations, streaming tables y materialized views (`src/pipeline/`) |
| 3. ML con MLflow | Entrenamiento con baseline, MLflow 3, firma e input example, modelo en Unity Catalog con alias `champion`, puerta de calidad (`src/02_train.py`, `src/03_score.py`) |
| 4. IA generativa y LLM | RAG con AI Search (índice Delta Sync, embeddings gestionados), LLM de Foundation Model APIs, evaluación de retrieval con recall@k y MRR (`src/04_rag_index.py`, `src/05_eval_rag.py`) |
| 5. Gobierno y MLOps | Declarative Automation Bundles (`databricks.yml`), CI/CD con GitHub Actions y OAuth M2M, permisos de mínimo privilegio para la app, tests |

## Arquitectura

```mermaid
flowchart LR
  subgraph Volumes
    RAW[(raw/cmapss<br/>train, test, RUL)]
    MAN[(manuales<br/>PDF)]
  end
  RAW -->|Auto Loader| B[bronze_cmapss_lecturas<br/>bronze_cmapss_rul]
  B --> S[silver_lecturas<br/>silver_rul_test]
  S --> G[gold_features<br/>gold_test_verdad<br/>gold_salud_curva]
  G --> T[02_train<br/>MLflow 3]
  T --> M[(rul_model@champion<br/>Unity Catalog)]
  M --> P[03_score<br/>gold_predicciones]
  T --> GM[gold_model_metrics]
  MAN -->|pypdf, troceo| C[manual_chunks<br/>Delta + CDF]
  C --> I[AI Search<br/>índice Delta Sync]
  P --> APP[Databricks App<br/>FastAPI + SQL warehouse]
  GM --> APP
  G --> APP
  I --> APP
  APP --> LLM[Foundation Model APIs]
```

Decisiones de diseño:

- **Bronze y silver son streaming tables**: los ficheros llegan al volume y solo se añaden; Auto Loader procesa
  únicamente lo nuevo. La limpieza de silver es fila a fila, así que no necesita estado.
- **Gold son materialized views**: las ventanas móviles, las estadísticas de referencia y la unión con la verdad de test
  necesitan el historial completo de cada motor. Una materialized view se mantiene coherente con silver sin gestionar estado en streaming.
- **Etiqueta RUL**: `RUL = min(ciclos restantes, 125)` en train. El recorte a 125 es práctica estándar en la literatura de
  C-MAPSS (Heimes, 2008; Li, Ding y Sun, 2018). La evaluación usa la RUL real de test, sin recorte.
- **Sensores descartados en silver**: `setting3`, `s1`, `s5`, `s10`, `s16`, `s18`, `s19` tienen desviación típica exactamente 0 en
  train y test; `s6` solo toma dos valores (21,60 y 21,61). Lo comprueba `tests/test_cmapss.py` con los datos reales. Quedan 14 sensores.
- **Variables**: valor, media y desviación móvil en ventanas de 5 y 10 ciclos y pendiente en 10 ciclos por sensor, más el ciclo (85 variables). Ventanas solo hacia atrás, sin fuga de futuro.
- **Una definición, dos implementaciones**: `src/lib/cmapss.py` (pandas) es la referencia testeable sin Spark y
  `src/lib/spark_features.py` la implementa con funciones de ventana de Spark.
  `tests/test_spark_equivalence.py` comprueba que dan los mismos números (se omite si no hay Java).
- **Sensores principales por motor**: sensibilidad local por oclusión (se sustituyen las variables de un sensor por su valor sano y se mide
  cuánta RUL cambia). No son valores SHAP: no suman la predicción y dependen de la correlación entre sensores.
- **Indicador de salud** (curva de la app): combinación lineal de z-scores respecto a los primeros 30 ciclos, ponderada por el desplazamiento de cada
  sensor hasta el fallo; 0 es sano y 1 el nivel medio de fallo en train. Es una vista resumen, no una predicción. La proyección es una recta sobre los
  últimos 30 ciclos y se muestra junto a la RUL del modelo; si divergen, la app lo dice.
- **Convención de nombres**: en Free Edition hay un único schema de trabajo, así que las capas se identifican por prefijo (`bronze_`, `silver_`, `gold_`)
  y no por schema. Las tablas de resultados son `gold_model_metrics` y `gold_predicciones`.
- **Champion**: se asigna solo si el RMSE de test mejora el del champion actual. El test de C-MAPSS actúa como banco fijo: los hiperparámetros se eligieron con
  validación cruzada por motor solo sobre train, pero decidir el champion con ese mismo test es una puerta de calidad y no una estimación independiente del error futuro.

## Estructura

```
databricks.yml              bundle: variables, targets dev y prod
resources/                  pipelines.yml, jobs.yml, serving.yml, app.yml
src/00_setup.py             schema, volumes, copia de los datos
src/pipeline/               bronze.py, silver.py, gold.py (Lakeflow SDP)
src/02_train.py             entrenamiento, evaluación, registro y alias champion
src/03_score.py             puntuación de los 100 motores y gold_predicciones
src/04_rag_index.py         PDF a fragmentos, tabla con CDF, índice de AI Search
src/05_eval_rag.py          recall@k y MRR con preguntas generadas desde los fragmentos
src/06_permisos_app.py      GRANT de lectura al service principal de la app
src/lib/                    lógica pura y testeable (sin Spark salvo spark_*.py)
app/                        Databricks App: app.yaml, requirements.txt, app.py, static/index.html
tests/                      pytest
scripts/                    entrenar_local.py (medida del RMSE), demo_local.py (app sin Databricks)
data/cmapss/                datos de NASA y FUENTE.md
.github/workflows/          deploy.yml
```

## Orden de ejecución

1. **Desplegar** el bundle (ver más abajo). En el primer despliegue comenta la línea `resources/serving.yml` de `databricks.yml`: el endpoint necesita una versión del modelo que aún no existe.
2. **Subir los PDF** de los manuales públicos al volume `manuales` (Catálogo, `workspace`, `mantenimiento`, `manuales`, Upload). Los PDF no están en el repositorio.
3. **Job `mantenimiento_end_to_end`**: `00_setup` (schema, volumes, datos), pipeline bronze, silver y gold, `02_train` y `03_score`.
4. **Job `mantenimiento_rag`**: `04_rag_index` y `05_eval_rag`. Crea el endpoint de AI Search la primera vez (tarda varios minutos).
5. **App**: despliega con `databricks bundle run mantenimiento_app -t dev` o desde la UI. Después ejecuta `src/06_permisos_app.py` para dar lectura al service principal de la app.
6. **Serving (opcional)**: descomenta `resources/serving.yml`, fija `model_version` con la versión champion y vuelve a desplegar. El endpoint apunta a una versión concreta, no sigue al alias: actualiza la variable cada vez que cambie el champion.

## Límites de Free Edition y cómo se respetan

| Límite | Cómo se respeta |
|---|---|
| Solo serverless, sin clústeres | Pipeline `serverless: true`; jobs con `environments` serverless; ningún clúster definido |
| Internet restringido a dominios de confianza | Los datos viajan en el repositorio y se copian al volume; solo se usa `pip` en tiempo de ejecución (`pypdf`, `databricks-ai-search`) |
| 1 SQL warehouse 2X-Small | La app consulta con ese warehouse, declarado como recurso con `CAN_USE`, con caché de 30 s para no tenerlo despierto sin necesidad |
| 1 endpoint de AI Search de 1 unidad | Se reutiliza si existe; índice `TRIGGERED` (sin sincronización continua) |
| Hasta 3 apps, se paran a las 24 h | Una sola app; hay que volver a arrancarla pasado ese tiempo |
| 1 pipeline activo por tipo | Un único pipeline en modo triggered, lanzado desde el job |
| Máx. 5 tareas de job concurrentes | Jobs secuenciales, `max_concurrent_runs: 1` |
| Serving sin GPU ni provisioned throughput | Endpoint CPU `Small` con scale to zero; el LLM es un endpoint pay-per-token de Foundation Model APIs |
| Sin consola de cuenta | Sin federación OIDC: el CI usa OAuth M2M con un service principal del workspace |

## Coste cero y datos

Todo corre en Free Edition, con cuotas de uso razonable. No hay servicios de pago ni claves externas.

**Datos:** NASA C-MAPSS FD001 (Saxena et al., 2008, NASA Ames Prognostics Data Repository), dominio público.
Procedencia y comprobaciones en `data/cmapss/FUENTE.md`. Los manuales para el RAG los aportas tú; usa solo documentos públicos y revisa su licencia.
El código es MIT (`LICENSE`).

## Cómo desplegar

**Desde la UI del workspace (Git folder)**

1. Sube este repositorio a GitHub y crea un Git folder (Workspace, Create, Git folder).
2. Abre `src/00_setup.py` y ejecútalo en cómputo serverless.
3. Crea el pipeline apuntando a `src/pipeline` con la configuración `mantenimiento.raw_path` y `mantenimiento.src_root` de `resources/pipelines.yml`, y ejecuta cada notebook en orden.

**Con la CLI (recomendado)**

```bash
databricks auth login --host https://<tu-workspace>
databricks bundle validate -t dev --var warehouse_id=<id-del-warehouse>
databricks bundle deploy   -t dev --var warehouse_id=<id-del-warehouse>
databricks bundle run mantenimiento_end_to_end -t dev
```

**CI/CD:** `.github/workflows/deploy.yml` ejecuta los tests en cada PR, valida el bundle y despliega a `dev` en cada push a `main`.
Secretos del repositorio: `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` (OAuth M2M). Nunca hay tokens en el repositorio.
El target `prod` existe como referencia; en Free Edition no hay service principals de cuenta para un `run_as` separado.

## Tests y demo local

```bash
pip install -r requirements-dev.txt
python -m pytest -q                    # funciones puras, equivalencia con Spark y API de la app
python scripts/entrenar_local.py       # RMSE en test FD001 real (con --tune busca hiperparámetros por validación cruzada en train)
python scripts/demo_local.py           # la app con datos locales, sin Databricks (el asistente responde con texto fijo)
```

## Puntos que hay que verificar dentro del workspace

Marcados `verificar en docs` en el código. Son los que no se pudieron comprobar sin un workspace real:

1. Que el pipeline importa `lib` desde `mantenimiento.src_root` (`sys.path` en ficheros de pipeline).
2. Que el notebook ve `lib` (directorio del notebook en `sys.path`) y la ruta del contexto de notebook usada en `00_setup.py`.
3. Que `pathGlobFilter` con llaves (`{train,test}_FD001.txt`) y la opción `wholetext` funcionan en Auto Loader (la lectura batch equivalente sí se probó con Spark local).
4. Nombre y disponibilidad en Free Edition del LLM (`databricks-gpt-oss-120b`) y de los embeddings (`databricks-gte-large-en`, solo inglés; para manuales en español valora un modelo multilingüe).
5. Estructura de salida de `ai_parse_document` (solo es el plan B de extracción de texto) y su disponibilidad.
6. Privilegios del service principal de la app sobre el endpoint e índice de AI Search (`06_permisos_app.py` concede lectura sobre el schema).
7. Versión de entorno serverless (`serverless_env_version`), `environment_key` en tareas de notebook y nombres de recursos con prefijo en `mode: development` (apps y endpoints).
8. Etiqueta vigente de `databricks/setup-cli` en el workflow.
9. Que `valueFrom: sql_warehouse` resuelve el ID del warehouse y que el service principal de las apps puede crearse en Free Edition.
