# 02. Validación del modelo de IA (Fase B)

Alcance: B1 integridad de datos, B2 entrenamiento y evaluación, B3 MLflow y serving, B4 pruebas negativas, B5 recomendaciones operativas.
Evidencias reproducibles en `audit/evidence/` (scripts `.py`, resultados `.json`/`.csv`, consultas SQL guardadas en `workspace/`).
Etiquetas: PASS / FAIL / NOT_VERIFIED. Nada de lo que sigue es una certificación industrial: es un prototipo de demostración sobre un conjunto de datos de simulación.

## Resumen

| Ítem | Estado | Resultado clave |
|---|---|---|
| B1 Integridad de datos | PASS | 20.631 / 13.096 filas y 100 + 100 motores (coinciden con FD001 oficial); RUL correcta (0 discrepancias); 170 sumas de variables Spark frente a pandas con diferencia relativa máxima 2,4e-9; 40 pruebas de truncado sin fuga de futuro |
| B1 Predicciones salen de datos reales | PASS | `gold_predicciones` = salida del modelo sobre `gold_features` (100 motores). Verificado con el endpoint: coincidencia exacta en los 100 |
| B2 Reproducción | PASS con reservas | HGB reproducido **bit a bit** (RMSE 18,441849882005343) con scikit-learn 1.7.2 sobre `gold_features` del workspace; 19,10 local se explica por versión de scikit-learn y ruido numérico de features (ver más abajo) |
| B2 Ausencia de fuga | PASS en características; **FAIL en protocolo histórico, corregido en el código** | Sin fuga en ventanas y etiquetas. El test de FD001 ya es un conjunto "visto": la regla de promoción del champion lo usaba (contaminación experimental, apartado B2.4). `02_train.py` promueve ahora por validación por motor solo con train; la cifra de test sigue sin ser independiente |
| B3 MLflow y serving | PASS (revalidado el 15 de octubre) | v1, alias `champion`, firma de 85 entradas, endpoint invocado realmente; salida del endpoint = `gold_predicciones` en los 100 motores. El endpoint había desaparecido del workspace entre el 14 y el 15 de octubre y se recreó con `bundle deploy -t dev` (B3.5) |
| B3 Correspondencia endpoint y app | PASS con aclaración | La app **no llama al endpoint**: lee `gold_predicciones`. Los valores coinciden (diferencia de redondeo a 2 decimales) |
| B3 Sin fallback silencioso | PASS en la app tras corrección; hallazgos en el endpoint | La app original sustituía una RUL nula por 0 y mostraba métricas de otra versión; corregido. El endpoint acepta nulos sin error (D-02) |
| B4 Pruebas negativas | PASS (tras corrección) | 25 pruebas; 10 fallaban contra la `app.py` original |
| B5 Recomendaciones | PASS (tras corrección) | Textos orientativos; sin órdenes de parada o sustitución; incertidumbre declarada como no calibrada |

---

## B1. Integridad de los datos

Script: `evidence/compare_workspace_vs_local.py`. Resultado: `evidence/b1_data_integrity.json`.

### Procedencia y conteos

La procedencia declarada (`data/cmapss/FUENTE.md`) es un espejo de GitHub (`edwardzjl/CMAPSSData`) del repositorio de NASA Ames;
no existe verificación criptográfica contra el origen de NASA (el entorno no tiene acceso). `NOT_VERIFIED` la identidad byte a byte
con el fichero original de NASA; sí se verifican los recuentos oficiales y propiedades internas:

| Comprobación | Esperado (FD001 oficial) | Observado | Estado |
|---|---|---|---|
| Filas de train | 20.631 | 20.631 (fichero y `silver_lecturas` split train) | PASS |
| Filas de test | 13.096 | 13.096 | PASS |
| Motores train / test | 100 / 100 | 100 / 100, identificadores 1 a 100 | PASS |
| Valores de RUL | 100 | 100; `silver_rul_test` coincide con el fichero | PASS |
| Ciclos contiguos desde 1 por motor | sí | sí (train y test) | PASS |
| Valores nulos | 0 | 0 | PASS |

### Separación train y test

- Ficheros de origen distintos por split (`silver_source_files.json`): train sale solo de `train_FD001.txt`, test solo de `test_FD001.txt`.
- Los identificadores de motor 1 a 100 se repiten en ambos ficheros (son motores distintos; así es FD001): el modelo y las consultas siempre
  usan la clave (`split`, `unit`). Se comprobó que **ninguna fila de sensores** es idéntica entre train y test (0 coincidencias).
- La RUL solo existe en train: en `gold_features` las 13.096 filas de test tienen `rul` y `rul_raw` nulos (0 filas con valor).

### Etiqueta RUL

- `rul_raw = ciclo_máximo_del_motor - ciclo` (SQL: 0 discrepancias en las 20.631 filas de train); `rul = min(rul_raw, 125)` (0 discrepancias).
  El último ciclo de cada motor tiene RUL 0. El 38,9 % de las filas de train está recortado a 125 (práctica estándar en C-MAPSS).
- Test: la RUL real es la de `RUL_FD001.txt`, evaluada en el último ciclo observado; `gold_test_verdad` y `silver_rul_test` coinciden con el fichero.
  Ojo: la verdad de test no está recortada, el modelo sí se entrena con RUL recortada; la discrepancia penaliza a los motores con RUL larga (ver B2).

### Variables eliminadas

Comprobación con los datos (`constant_columns` en el JSON):

| Variable | Evidencia | Justificación |
|---|---|---|
| `setting3`, `s1`, `s5`, `s10`, `s16`, `s18`, `s19` | Desviación típica exactamente 0 en train y en test | Sin información: correcto eliminarlas |
| `s6` | 2 valores distintos (21,60 y 21,61), desviación 0,0015 | Ruido de cuantización: decisión razonable |
| Sensores conservados (14) | Desviación mínima en train 0,0375 | Con variación real |
| `setting1`, `setting2` | Desviaciones 0,0022 y 0,0003 | Se conservan en silver pero no entran al modelo (régimen único en FD001) |

### Ventanas sin información futura

- Definición: medias, desviaciones y pendiente en ventanas hacia atrás (incluye el ciclo actual), por motor, `min_periods=1`.
- Prueba (40 comprobaciones sobre 5 motores y 8 ciclos de corte cada uno): se recortan las series en el ciclo c, se recalculan las variables y se compara con
  las del ciclo c calculadas con la serie completa. **0 discrepancias**, es decir, las variables de un ciclo no dependen de ciclos posteriores.
- El indicador de salud y sus parámetros se ajustan solo con train (`fit_health_params`).
- Observación (no es fuga): `cycle` es una variable del modelo. En test equivale a la edad observada del motor truncado; es legítimo, pero hace que la edad
  contribuya a la predicción (el baseline lineal solo con `cycle` obtiene RMSE 33,3 frente a 43,1 de la media).

### Equivalencia pandas y Spark

1. Prueba automática `tests/test_spark_equivalence.py` (6 motores reales, 6 pruebas): **6 superadas** en este entorno (Java 21, PySpark 4.2.0).
2. Contra las tablas reales del workspace (todas las filas):
   - 170 sumas de columna (85 variables x 2 splits) de `gold_features` frente al cálculo pandas: diferencia relativa máxima **2,37e-9**
     (orden de acumulación de coma flotante), todas por debajo de 1e-8. `evidence/features_sums_pandas_vs_workspace.csv`.
   - Las 85 variables del último ciclo de los 100 motores de test, fila a fila: diferencia absoluta máxima **3,7e-10**.
   - Sumas de `rul` y `rul_raw` en train: idénticas (1.791.375 y 2.224.184).
   - Sumas de sensores de `silver_lecturas` frente a los ficheros: diferencias del orden de 1e-8 (redondeo).
   Estado: PASS. La igualdad es numérica, no bit a bit; esa diferencia de ~1e-9 tiene consecuencias en el entrenamiento (B2.3).

### Las predicciones de los 100 motores salen de datos reales

Camino de código verificado de extremo a extremo:

`ficheros C-MAPSS en el volume raw` -> `bronze_cmapss_lecturas` (Auto Loader) -> `silver_lecturas` -> `gold_features` (ventanas Spark) ->
`src/03_score.py`: carga `models:/workspace.mantenimiento.rul_model@champion`, puntúa el último ciclo de cada motor de test, calcula contribuciones
por oclusión y escribe `gold_predicciones` -> `app/app.py` `q_engines()` ejecuta `SELECT ... FROM gold_predicciones` en el SQL warehouse -> `/api/engines` -> `index.html`.

Pruebas de que no hay valores estáticos ni simulados en el frontend o en la API:
- `grep` de `app.py` sin `serving_endpoints`, `/invocations`, `.predict(` ni valores por defecto numéricos (test `test_app_no_llama_al_endpoint_de_serving_ni_tiene_fallback_numerico`).
- Los 100 valores `rul_pred` de la tabla coinciden con la salida real del endpoint para las mismas 100 filas de entrada (B3); 99 valores distintos, rango 3,12 a 124,98.
- `rul_real` de la tabla coincide con el fichero oficial; `error = rul_pred - rul_real`; `cycle` = último ciclo de cada motor de test.
- La consulta de la app desplegada devolvió las mismas filas (motores 34, 60, 85, ver B3).

Alcance honesto: "predicción" en el dashboard significa estimación de RUL **al último ciclo observado de los 100 motores del conjunto de test**, calculada por lotes
(`scored_at = 2026-10-13T20:26:50Z`), no inferencia en vivo sobre telemetría.

---

## B2. Entrenamiento y evaluación

### B2.1 Configuración reproducida

`src/lib/modeling.py`: `HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0,
early_stopping=False, random_state=42)`; RUL de entrenamiento recortada a 125; 85 variables (ciclo + 14 sensores x 6); 20.631 filas de entrenamiento.
Evaluación: último ciclo de cada uno de los 100 motores de test frente a `RUL_FD001.txt`, predicciones acotadas a >= 0.
Semilla: con `early_stopping=False` y menos de 200.000 filas, `random_state` no interviene: RMSE idéntico con semillas 0, 1, 42 y 123
(`rmse_by_seed_hgb`). El modelo es determinista para unos datos y una versión de biblioteca dados.

### B2.2 Métricas de referencia (modelo del workspace, v1)

Calculadas desde `gold_predicciones` (`evidence/champion_v1_metrics_from_gold_predicciones.json`, `champion_v1_per_engine.csv`) y coincidentes con MLflow:

| Métrica (100 motores, último ciclo) | Valor |
|---|---|
| RMSE | **18,44** ciclos (MLflow `rmse_test` 18,441849882005343) |
| MAE | 13,48 |
| R2 | 0,803 |
| Score PHM08 | 655,0 (MLflow 655,0239) |
| Sesgo medio (pred menos real) | -1,01; 54 % de las predicciones son tardías (pred > real) |
| Error absoluto: mediana / p90 / máximo | 9,5 / 30,6 / 64,4 ciclos |
| Motores con error absoluto <= 10 / <= 20 ciclos | 52 % / 77 % |

Baselines en el mismo test (misma versión de biblioteca que el champion; ver B2.3):

| Modelo | RMSE | MAE | R2 | PHM08 |
|---|---|---|---|---|
| Media constante | 43,07 | 35,90 | -0,074 | 33.629 |
| Solo `cycle`, regresión lineal (baseline añadido en la auditoría) | 33,34 | 27,59 | 0,356 | 5.319 |
| Ridge (85 variables) | 21,80 | 16,81 | 0,725 | 1.073 |
| **HGB (champion v1)** | **18,44** | 13,48 | 0,803 | 655 |

HGB mejora a Ridge en 3,4 ciclos de RMSE (16 %). Dada la incertidumbre de B2.3 (±0,4 por variaciones numéricas) la ventaja es real pero moderada.

Errores por intervalos de RUL real (champion v1; `by_bin` en el JSON):

| RUL real | n | RMSE | MAE | Sesgo (pred - real) |
|---|---|---|---|---|
| 0 a 30 | 25 | 14,31 | 8,53 | +5,5 |
| 31 a 60 | 14 | 8,50 | 7,14 | +3,0 |
| 61 a 90 | 15 | 18,95 | 16,26 | +5,7 |
| 91 a 125 | 35 | 17,77 | 13,62 | -1,5 |
| 126 o más | 11 | 32,25 | 28,55 | -28,6 |

Lectura: el error es máximo cuando la RUL real supera 125, por construcción (el modelo no puede predecir más de 125 al entrenarse con RUL recortada); no es un fallo
del modelo sino de la diferencia entre etiqueta de entrenamiento y verdad de test.

**Motores próximos al fallo (RUL real <= 30, n = 25):** RMSE 14,31, MAE 8,53, sesgo +5,5 (el modelo tiende a sobrestimar la vida restante, dirección peligrosa),
PHM08 152,5. Clasificación de estado (umbrales demostrativos 20 y 35 ciclos) frente al estado según la RUL real:

| Estado real (por RUL real) | Motores | Predicho critical | Predicho watch | Predicho healthy |
|---|---|---|---|---|
| critical (RUL real <= 20) | 16 | 14 | 1 | 1 |
| watch (21 a 35) | 10 | 0 | 5 | 5 |
| healthy (> 35) | 74 | 0 | 1 | 73 |

Entre los 26 motores con RUL real <= 35, **6 aparecen como `healthy`** (motores 41, 18, 37, 61, 64, 77; el 41 tiene RUL real 18 y se predice 49,6) y 1 de los 16 críticos reales queda en `watch`. Con 100 motores
no hay precisión para afinar estas tasas (intervalos amplios), pero ilustra por qué no puede tratarse el estado como decisión operativa.
Peores motores por error absoluto: 74 (-66,9 con el modelo local), 45, 89, 18, 55, 37 (`worst_10`).

### B2.3 Explicación de 19,10 frente a 18,44 de RMSE

Hechos medidos:

| Experimento (HGB con los mismos hiperparámetros) | scikit-learn | Datos de entrenamiento | RMSE test |
|---|---|---|---|
| Documentado en README (`scripts/resultado_local.json`) | 1.9.1 | variables pandas locales | **19,102** |
| Reproducción auditoría | 1.9.1 | pandas local | 19,102 (idéntico) |
| Reproducción auditoría | 1.7.2 | pandas local | 18,597 |
| Reproducción auditoría | 1.9.1 | `gold_features` de Spark (workspace) | 18,505 |
| Reproducción auditoría | **1.7.2** | **`gold_features` de Spark (workspace)** | **18,441849882005343** (PHM08 655,0239471190539, MAE 13,478027648560577) |
| Workspace (MLflow v1) | 1.7.2 | `gold_features` | 18,441849882005343 |

Conclusión: la fila 5 reproduce **exactamente** (todos los decimales de RMSE, PHM08 y MAE) el modelo registrado en MLflow. Por tanto:
- No hay diferencias de código, semilla, validación cruzada ni conjunto de variables entre local y workspace. Los baselines (media y Ridge) coinciden en ambos entornos hasta el noveno decimal (43,0670 y 21,7963), lo que descarta diferencias de datos o de etiquetas.
- La diferencia 19,10 a 18,44 (0,66 ciclos) se debe a **la versión de scikit-learn** (1.9.1 frente a 1.7.2) **y al ruido numérico de ~1e-9** entre las variables calculadas con pandas y con Spark. Ambos factores reorganizan algunas divisiones del árbol.
  Con datos de workspace y 1.9.1 el RMSE es 18,505; con datos pandas y 1.7.2, 18,597; con ambos cambios, 19,102.
- Sensibilidad medida: perturbando las variables de entrenamiento con ruido relativo gaussiano de 1e-9 (del orden de la diferencia pandas/Spark) el RMSE de test va de **18,29 a 18,98** (10 reentrenamientos, dos versiones de scikit-learn). Es decir, la
  incertidumbre de la métrica puntual por inestabilidad numérica es de aproximadamente ±0,4 ciclos, del mismo tamaño que la diferencia entre 18,44 y 19,10.
- La cifra del workspace (18,44) no es "mejor" que la local (19,10): las dos son muestras de una misma distribución de ruido. Ninguna debería citarse con más de un decimal ni como prueba de que una versión del entorno sea superior.

Recomendación para el README: indicar "RMSE de 18,4 a 19,1 ciclos según versión de scikit-learn y precisión numérica de las variables" y registrar versiones en cada ejecución (`02_train.py` no registra la versión de scikit-learn como parámetro; el artefacto `MLmodel` sí).

### B2.4 Contaminación del conjunto de test y protocolo honesto

Hechos sobre el uso del test, tal como están en el código:

1. **`src/02_train.py` promueve el champion comparando el RMSE del test.** La función `rmse_de_version` lee `rmse_test` de `gold_model_metrics` y el alias `champion`
   se mueve solo si `cand["rmse_test"] < rmse_champ`. Es selección de modelo sobre el test, aunque el propio notebook lo reconoce ("puerta de calidad, no una estimación independiente").
   En la práctica solo hay una versión (v1), asignada porque no existía champion, así que la regla no se ha ejercido todavía; pero el diseño está contaminado desde la primera repetición.
2. **Hiperparámetros:** según los comentarios, se eligieron con `GroupKFold` por motor solo sobre train (`scripts/entrenar_local.py --tune`). La auditoría repitió esa búsqueda (8 combinaciones, 5 pliegues por motor, solo train; `repro_sklearn172.json`, `cv_grid_train_only`).
   La mejor combinación en esta repetición (`learning_rate 0.05, max_leaf_nodes 15, min_samples_leaf 100`, RMSE CV 15,76) **no es** la configuración desplegada (`max_leaf_nodes 31, min_samples_leaf 40`, CV 15,80). Las diferencias de CV son de 0,04, dentro del ruido; no hay evidencia de uso del test,
   pero el parámetro fijado en `HGB_PARAMS` no es el óptimo de la búsqueda reproducida. No se puede probar qué se hizo antes del único commit del modelo: el historial de Git solo contiene un estado del código de entrenamiento (`NOT_VERIFIED` para decisiones anteriores).
3. **Otras decisiones de diseño** (recorte a 125, ventanas 5 y 10, pendiente a 10, incluir `cycle`, eliminar `s6`, elegir HGB frente a Ridge): están justificadas con la literatura en los comentarios, pero no hay registro de si se miraron métricas de test al decidir.
4. **El test de FD001 ya es un conjunto visto.** Su RMSE se ha publicado en el README, comparado con baselines y comparado entre entornos. Cualquier cifra nueva calculada sobre ese mismo test no es independiente y no se presenta aquí como tal. Las cifras de B2.2 son una **descripción del comportamiento del modelo congelado sobre un conjunto conocido**, no una estimación independiente del error futuro.

Protocolo propuesto (los pasos 1, 2 y 6 están implementados en el código desde el 15 de octubre, ver B2.5; el paso 5 no se ha ejecutado):

| Paso | Qué hacer | Regla |
|---|---|---|
| 1. Selección | Elegir variables, recorte, hiperparámetros y champion solo con validación por motor dentro de train (`GroupKFold` 5 pliegues, o 20 motores de validación fijos con semilla registrada). Registrar versión de scikit-learn y semilla | Prohibido leer métricas de test para decidir |
| 2. Puerta de promoción | Cambiar `02_train.py` para comparar el RMSE de validación por motor (no el de test) y exigir mejora mayor que la desviación entre pliegues | El test no participa |
| 3. Congelar | Entrenar con todo train, registrar versión y fijar | |
| 4. Informe de test | Evaluar **una sola vez** en FD001 test, etiquetándolo como "conjunto de referencia visto" | No iterar después |
| 5. Evaluación fresca | Opción A: reservar 20 motores de train completamente fuera (entrenamiento, selección y ajuste) y cortarlos en ciclos aleatorios como el test. Opción B: FD003 (misma condición, dos modos de fallo) para medir generalización fuera de distribución; requiere reentrenar y declarar que es otro problema | Solo así se puede hablar de independencia |
| 6. Incertidumbre | Reportar la variabilidad (desviación entre pliegues, intervalos bootstrap por motor) en lugar de un solo RMSE | |

Estimación de desarrollo ejecutada (`evidence/dev_protocol_heldout_train.py`, `dev_protocol_heldout_train.json`): 5 pliegues por motor solo en train; cada motor retenido se corta en un ciclo con RUL real extraída
de la distribución marginal de RUL del test (sin usar las etiquetas por motor ni el modelo del test); RUL real sin recorte; 200 repeticiones.
RMSE medio **18,04**, desviación entre repeticiones **1,24**, percentiles 5 y 95: 15,9 y 19,8 (scikit-learn 1.9.1, variables pandas). RMSE OOF sobre todos los ciclos con etiqueta recortada: 16,12.
Esta cifra **no es una evaluación independiente** (los hiperparámetros se eligieron con el mismo train) pero muestra que el error típico esperado es de unos 16 a 20 ciclos y que el rango por simples cortes distintos (±1,2) es mayor que la diferencia entre entornos.

### B2.5 Cambio aplicado en el código: promoción sin test (15 de octubre de 2026)

Ficheros: `src/lib/modeling.py` (`validation_rmse_by_engine`, `promotion_decision`), `src/02_train.py`, `tests/test_modeling_selection.py` (4 pruebas).
Evidencia local: `evidence/validation_protocol_train_only.py` y `.json`.

- `validation_rmse_by_engine`: `GroupKFold` de 5 pliegues por `unit` **solo con train**. Cada motor retenido se corta en un ciclo fijado por
  una semilla (RUL real entre 0 y 145, sin usar la distribución del test) y se evalúa como en el test (último ciclo observado, RUL sin recortar):
  `rmse_val_truncated`. También se calcula el RMSE out-of-fold sobre todos los ciclos (`rmse_val_all_cycles`) y la desviación entre pliegues (`std_folds`).
- `promotion_decision`: sin champion se promueve la primera versión; si el champion no tiene `rmse_val_truncated` registrada (caso de la v1 actual),
  la versión nueva queda como `challenger` y la decisión es manual; en otro caso se promueve solo si mejora en más de `std_folds / sqrt(k)`.
  **El RMSE de test no interviene** (una prueba comprueba que `02_train.py` ya no contiene la comparación `cand["rmse_test"] <`).
- `02_train.py` registra además `sklearn_version`, `selection_metric` y las métricas de validación en MLflow, en las etiquetas de la versión y en
  `gold_model_metrics` (columnas nuevas `rmse_val_truncated`, `rmse_val_all_cycles`, `std_folds`, con `mergeSchema`).

Resultado local con los hiperparámetros del champion (scikit-learn 1.9.1, variables pandas, 20.631 filas, 100 motores):

| Modelo | `rmse_val_truncated` | `rmse_val_all_cycles` | `std_folds` |
|---|---|---|---|
| Ridge (baseline) | 22,11 | 19,36 | 2,04 |
| HGB (candidato) | 19,21 | 16,12 | 3,14 |

Con esta regla HGB se habría promovido frente a Ridge (mejora 2,9 > margen 1,4) sin mirar el test. La cifra 19,21 es **una estimación de
desarrollo** (los hiperparámetros se eligieron con el mismo train) y coincide en orden de magnitud con el 18,4 a 19,1 del test.

**No se ha ejecutado el entrenamiento en el workspace** con el código nuevo: hacerlo crearía la versión 2 del modelo (que quedaría como
`challenger` porque la v1 no tiene métrica de validación) sin aportar nada a esta auditoría. El código nuevo está desplegado en el bundle
(`A_52`) y en el volume `codigo` (`A_50`); el champion y `gold_predicciones` siguen siendo los de la v1. Estado: **protocolo implementado,
verificado con pruebas unitarias y evidencia local; no ejercitado en el workspace (NOT_VERIFIED en Databricks)**.

---

## B3. MLflow y serving

### Registro y artefactos (API de Unity Catalog, `evidence/workspace/uc_model_versions.json`)

| Elemento | Valor |
|---|---|
| Modelo | `workspace.mantenimiento.rul_model`, versión **1**, estado READY (única versión) |
| Alias | `champion` = v1 (consulta `.../aliases/champion` devuelve la versión 1) |
| Ejecución | `rul_entrenamiento` (job 134653962208443, notebook `02_train`), FINISHED |
| Parámetros registrados | `n_features 85`, `n_train_rows 20631`, `rul_clip 125` |
| Métricas registradas | `rmse_test 18,441849882005343`, `mae_test 13,478027648560577`, `phm08_test 655,0239471190539`, `rmse_mejor_baseline 21,796289834949274` |
| Etiquetas de la versión | `dataset=C-MAPSS FD001`, `rmse_test=18.4418` |
| Firma | entradas: 85 columnas `double` obligatorias (`cycle`, 14 sensores, 5 estadísticos por sensor); salida: tensor `float64` de forma (-1) |
| Artefactos | `MLmodel`, `conda.yaml`, `input_example.json`, `model.pkl` (1,2 MB, cloudpickle), `python_env.yaml`, `requirements.txt`, `serving_input_example.json` |
| Entorno | Python 3.12.3, scikit-learn 1.7.2, MLflow 3.12.0, pandas 2.3.3 |

Observaciones: las métricas se llaman `*_test` y se registraron sobre el conjunto de test (ver B2.4). El modelo guarda la versión de scikit-learn en el artefacto pero no como parámetro MLflow.

### Endpoint

Endpoint real: `dev_jjrm199867_rul-model-dev` (id `f83f37b7...`), entidad `rul-model` -> `workspace.mantenimiento.rul_model` v1, CPU Small, scale to zero, `READY`.
La versión servida es la **1 fijada por variable** (`model_version` en `databricks.yml`); el endpoint no sigue al alias. Si se promueve otra versión sin cambiar la variable, `champion` y el endpoint divergirán.
Permisos: `CAN_MANAGE` solo propietario y `admins`.

### Prueba de extremo a extremo (motor 34)

Datos de un motor -> variables -> modelo -> endpoint -> API -> dashboard, con los mismos valores en cada eslabón:

| Eslabón | Evidencia | Valor para el motor 34 |
|---|---|---|
| Datos y variables | `gold_features` (split test, ciclo 203 = último), 85 variables | `cycle 203`, `s2 643,74`, `s4 1427,49`, `s11 48,13`, `s4_mean5 1427,362`, `s11_mean10 48,064`, `s11_slope10 0,023515` (fila completa en `workspace/sql_gold_features_test_last_cycle_85feats.json`) |
| Modelo | `rul_model` v1 @champion | RUL real del test: 7 |
| Endpoint | `POST /serving-endpoints/dev_jjrm199867_rul-model-dev/invocations` con `dataframe_split` de esa fila | **3,118922691130997** |
| Tabla de scoring | `gold_predicciones` (`scored_at 2026-10-13T20:26:50Z`, `model_version 1`) | 3,12 |
| API desplegada | `GET /api/engines` desde la sesión autenticada de la app | `rul_pred 3.12`, `estado critical`, `model_version 1` |
| Dashboard | Misma respuesta que la API (renderizada por `index.html`) | 3 ciclos estimados, estado critical |

Otros dos motores: motor 60 (endpoint 82,26835521034641, tabla/API 82,27, real 100) y motor 85 (124,90846155881731; 124,91; real 118).
Mismo ensayo para los **100 motores** en una sola llamada: diferencia máxima entre `round(endpoint, 2)` y `gold_predicciones.rul_pred` = **0,00**; 0 discrepancias; RMSE del endpoint contra la RUL real = 18,441849882005343, idéntico al `rmse_test` de MLflow
(`evidence/b3_endpoint_vs_gold_all100.json`). La consulta por CLI (`serving-endpoints query`) devolvió el mismo valor que la llamada REST.

**Aclaración obligatoria:** la app **no** invoca al endpoint. Lee `gold_predicciones` por SQL, escrita por `03_score.py` con el mismo modelo. El service principal
de la app no tiene permiso sobre el endpoint (ni lo necesita). Lo que se contrasta es que ambos caminos dan lo mismo. El endpoint existe como
interfaz de inferencia independiente, pero el dashboard no depende de él; si el endpoint está caído, el dashboard sigue mostrando la última puntuación por lotes con su `scored_at`. La interfaz no muestra aún la fecha de
puntuación (la API la devuelve; el campo `model.scored_at` se añade al overview).

### Fallback silencioso

Camino revisado (`app/app.py`): no existe ninguna llamada al modelo ni ninguna generación de valores. Defectos encontrados y corregidos:
- Una fila con `rul_pred` nulo se transformaba en RUL 0 y recibía el texto de acción de un motor crítico (`e.get("rul_pred") or 0.0`). Ahora queda `rul_pred: null`, estado `unknown`, y el texto "No prediction available for this engine."; se excluye de los KPI.
- Si no había métricas de la versión del modelo, la app mostraba las métricas del último entrenamiento de **otra** versión. Ahora muestra "--" (`rmse: null`).
- Cualquier fallo SQL se devolvía como `ready: false` con HTTP 200, indistinguible de "todavía no hay datos". Ahora: 503 `data_unavailable` (B4).
- La última actualización del pipeline usaba `DESCRIBE DETAIL` sobre una vista materializada y fallaba siempre (overview desplegado: `missing: ["gold_features"]`, `last_update: null`). Se sustituye por `MAX(_ingest_ts)`.

### B3.5 Revalidación del 15 de octubre de 2026 (`evidence/revalidation_2026-10-15/`)

- A las 22:16 UTC el endpoint `dev_jjrm199867_rul-model-dev` **no existía** (`A_13`, `A_25`, `A_29`, `A_36`): la invocación devolvía
  "The given endpoint does not exist". El 14 de octubre a las 00:5x respondía. No hay registro accesible de quién o qué lo eliminó (`NOT_VERIFIED`).
  Se recreó con `bundle deploy -t dev` (`A_52`, "Created model_serving_endpoints.rul_endpoint", nuevo id `dd2a3f66...`), `READY` a las 22:27 (`A_53`).
- Invocaciones reales tras la recreación (`A_54`..`A_62`), mismas filas de `gold_features` que el 14 de octubre:

| Entrada | Respuesta | Igual que el 14 de octubre |
|---|---|---|
| Motor 34 (`api post .../invocations`) | 3,118922691130997 | sí |
| Motor 34 (`serving-endpoints query`) | 3,118922691130997 | sí |
| Motor 60 | 82,26835521034641 | sí |
| Motor 85 | 124,90846155881731 | sí |
| 100 motores en un lote | 100 valores; `round(., 2)` = `gold_predicciones.rul_pred` en los 100 (diferencia máxima 0,00); RMSE frente a RUL real 18,441849882005343 (`b3_endpoint_vs_gold_all100_2026-10-15.json`) | sí |
| `s11` ausente | error de esquema explícito | sí |
| cadena en `s7` | "Failed to convert column s7 to type float64" | sí |
| 85 variables `null` | **120,6590326691026** (predicción sin datos) | sí: D-02 sigue abierto en el endpoint |

- Tabla `gold_predicciones` (`A_44`, `A_45`): 100 filas, 99 valores distintos, 3,12 a 124,98, RMSE 18,4417 (sobre valores redondeados a 2 decimales),
  `scored_at 2026-10-14T00:52:37Z`, `model_version 1`; **100 filas con el texto orientativo nuevo y 0 con el antiguo** (la nota de B5 queda cerrada).
- App desplegada (`app_api_capture_2026-10-15.json`): `/api/engines` motor 34 = 3,12 critical, 60 = 82,27, 85 = 124,91; `/api/overview` muestra
  `model.version 1`, `scored_at`, `source: batch scoring table gold_predicciones (no live inference)`, `uncertainty.calibrated false`;
  `/api/health` ok en warehouse, índice y LLM; `/api/overview` sin credenciales: 401. El `index.html` servido tiene el mismo SHA-256 que el del PC.
- Cadena completa verificada hoy: `gold_features` (fila del motor 34) -> endpoint recreado (3,1189) -> `gold_predicciones` (3,12) -> `/api/engines` (3,12) -> dashboard (3 ciclos, critical).
- Permisos del endpoint recreado (`A_64`): `CAN_MANAGE` propietario y `admins`; el SP de la app no tiene `CAN_QUERY` (no lo necesita).

---

## B4. Pruebas negativas

### En la app (`tests/test_negative_api.py`, 25 pruebas)

Ejecutadas contra la `app.py` original (copia del commit base): **10 fallan** (`evidence/b4_negative_tests_against_original_app.txt`); contra la versión corregida: **25 superadas**.

| Caso | Comportamiento original | Comportamiento corregido |
|---|---|---|
| Error SQL, timeout, warehouse inalcanzable (3 casos) | 200 con `ready:false` | 503 `{"error":"data_unavailable"}`, sin `engines`, `kpis` ni `history` |
| Tabla inexistente | estado vacío | estado vacío explícito con `missing:["gold_predicciones"]` |
| Warehouse sin configurar | estado vacío | igual |
| Timeout del conector | sin límite | `_socket_timeout` 90 s configurable |
| Fila sin RUL (`null`/NaN) | `500` o RUL 0 inventado | `rul_pred:null`, `estado:"unknown"`, sin acción, excluida de KPI |
| Métricas de otra versión | se mostraban | `null` |
| Fallo parcial de consultas auxiliares | marcadas como "tabla ausente" | lista `errors`, KPI parciales con `--` |
| Motor 0, negativo, 10001 | 422 | 422 |
| Motor no numérico (`abc`, `1.5`) | 422 | 422 |
| Motor inexistente (999) | 200 vacío | 404 |
| Pregunta ausente, vacía, en blanco (`"   "`), 2 caracteres, 501 caracteres, `null`, número, campo equivocado, JSON inválido | en blanco pasaba al asistente | 422 sin llamar al LLM |
| LLM caído / índice caído | 502 / 503 (ya cubierto en `test_app_api.py`) | igual |

"Sensores ausentes" y "datos incompletos" no son entradas de la app (no acepta telemetría): se prueban en el endpoint y en el scoring. "Modelo no disponible": la app no depende del modelo; en `03_score.py` `get_model_version_by_alias` falla con error claro si no hay champion.

### En el endpoint (invocaciones reales, `evidence/b3_trace_engine34_and_endpoint_negatives.json`)

| Entrada | Respuesta | Evaluación |
|---|---|---|
| Columna `s11` ausente | Error de esquema (explícito) | PASS |
| Cadena en `s7` | "Failed to convert column s7 to float64" | PASS |
| Lote vacío | Error del modelo (0 muestras) | PASS |
| `null` en `s7` | 3,118922691130997 (idéntico al valor con dato) | FAIL (aceptado sin aviso) |
| `null` en 11 variables relevantes (`s4`, `s11`, sus medias, `cycle`...) | 67,24 en lugar de 3,12 | FAIL (predicción distinta sin error) |
| 85 variables `null` | **120,66** | FAIL (predicción inventada a partir de nada) |
| `s4` y `s11` = -1e9 | 3,82 | FAIL (sin comprobación de rango) |
| `cycle = 0` | 5,04 | FAIL (valor imposible aceptado) |
| Columna extra desconocida | Se ignora y devuelve 3,1189 | Débil (contrato permisivo) |

Causa: `HistGradientBoostingRegressor` trata los `NaN` de forma nativa y MLflow convierte `null` a `NaN`, de modo que la firma "required" no impide nulos. Impacto: solo
afecta a quien llame directamente al endpoint (la app no lo hace). Mitigación implementada en el árbol: `validate_feature_frame` (`src/lib/cmapss.py`) rechaza vacíos, columnas ausentes, `NaN`/`inf` y `cycle < 1`, y `src/03_score.py` la ejecuta antes de predecir.
Pendiente (requiere decisión): hacer lo mismo dentro del propio modelo servido (envoltorio `pyfunc` y nueva versión), o poner siempre un validador delante.
**NOT_VERIFIED:** comportamiento ante cold start prolongado o timeout del endpoint (el endpoint respondió en menos de 90 s desde "Scaled to zero"); no se pudo forzar una caída.

---

## B5. Recomendaciones operativas

Textos revisados: `src/lib/cmapss.py` (`recommended_action`), `src/03_score.py` (columna `accion`), `app/app.py` (duplicado del anterior) y el respaldo de `index.html`.

Antes (órdenes categóricas basadas en un modelo experimental):
- critical: "Schedule shutdown and replacement within N cycles. Inspect s4 and s11 now."
- watch: "Increase inspection frequency and plan the intervention within the N-cycle window."
- healthy: "Normal operation. Keep the usual maintenance plan."

Problemas: ordena parada y sustitución con una cuenta atrás en ciclos sin base; "now" y "within N cycles" sugieren una precisión que el modelo no tiene (error típico del orden de 18 ciclos, con un 23 % de motores con error > 20); no aparece la incertidumbre; "Normal operation" afirma un estado operativo que el modelo no puede garantizar (en B2.2, 6 de 26 motores con RUL real <= 35 se predijeron `healthy`).

Después (idéntico en lib, app y respaldo del frontend; una prueba comprueba que `lib` y `app` generan exactamente el mismo texto):
- critical: "Demo estimate; requires engineering review. Estimated RUL is low (about N cycles): consider prioritising an inspection. Sensors to check first: s4 and s11."
- watch: "Demo estimate; requires engineering review. Estimated RUL is moderate (about N cycles): consider closer monitoring and planning an inspection."
- healthy: "Demo estimate; requires engineering review. No degradation flagged by the model; keep the usual maintenance plan."

Distinciones que ahora se reflejan:

| Concepto | Dónde |
|---|---|
| Predicción estadística | `rul_pred` (estimación del modelo al último ciclo observado) |
| Incertidumbre | `uncertainty` en `/api/overview`: `calibrated: false`, `typical_error_cycles` = RMSE del test (18,44), con la nota de que el test también influyó en la selección y de que no hay intervalos de predicción |
| Estado demostrativo | `estado` (umbrales 20 y 35 ciclos, parametrizables) |
| Recomendación orientativa | `action`, siempre con "Demo estimate; requires engineering review" |
| Decisión operativa | No la toma el sistema; los textos no ordenan parada ni sustitución |

No se ha inventado ningún intervalo de confianza. El RMSE se cita como error típico medido en un conjunto concreto, no como intervalo. No se calibró la incertidumbre (por ejemplo con regresión cuantílica o conformal prediction sobre validación por motor): queda como trabajo futuro.
Pruebas actualizadas/añadidas: `test_normalize_state_y_accion`, `test_acciones_son_orientativas_y_sin_ordenes_categoricas` (prohíbe `shutdown`, `replace`, `now.`, `must`, `immediately`...), `test_texto_de_accion_identico_en_app_y_lib`, `test_estados_antiguos_y_acciones_en_ingles`.
Nota: tras volver a ejecutar la tarea `score` (E04, 14 de octubre 00:52 UTC) la columna `accion` de `gold_predicciones` contiene el texto nuevo en las 100 filas (`A_45`: 0 filas con "shutdown" o "replacement").

---

## Archivos de código modificados por esta fase (para despliegue)

- `app/app.py`, `app/static/index.html` (solo estados de error, respaldo del texto de acción y manejo de RUL nula)
- `src/lib/cmapss.py` (texto orientativo, `validate_feature_frame`), `src/03_score.py` (validación previa, texto de cabecera)
- `tests/test_negative_api.py` (nuevo), `tests/test_app_api.py`, `tests/test_cmapss.py`
- `src/lib/modeling.py`, `src/02_train.py`, `tests/test_modeling_selection.py` (B2.5, 15 de octubre)
- Hecho: `src/lib` copiado al volume `codigo` (E01 y A_50), bundle desplegado a dev (E03, E05, A_52), tarea `score` ejecutada (E04), app reiniciada (E06, A_12).
