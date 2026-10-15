# 04. Seguridad y calidad (Fase D)

Alcance: árbol Git, historial, configuración de la app y del workspace, dependencias, linters y flujo de CI. La revisión del
tratamiento de prompt injection en `app/rag.py` es de solo lectura (el código RAG se audita en `03_RAG_VALIDATION.md`).
No se han impreso ni almacenado secretos; los identificadores del service principal aparecen truncados.

Resumen: **sin secretos en el árbol ni en el historial; 0 hallazgos críticos.** Revalidación del 15 de octubre de 2026 en el apartado 5:
aparece una advertencia nueva en una dependencia transitiva (`oauthlib`, no explotable en esta app) y se cierran D-01, D-03 y D-09.
Hallazgos de severidad media y baja en el apartado 4.

## 1. Evidencia por control

| # | Control | Comando / método | Resultado | Estado |
|---|---|---|---|---|
| D1 | Secretos en el árbol Git | `grep -rnEi "(dapi[0-9a-f]{20,}\|client_secret\|secret\|token\|password\|api[_-]?key)"` sobre el árbol rastreado (excluidos `audit/`, datos y `.git`) | Solo coincidencias de la palabra "tokens" (troceo de texto) y de `max_tokens`; ningún valor de credencial. `git ls-files` no contiene `.env`, `.pem` ni `.key` | PASS |
| D2 | Secretos en el historial | `git log --all -p` y `grep -E` con patrones `dapi[0-9a-f]{20,}`, `client_secret = <valor>`, `password = "..."`, `BEGIN (RSA\|PRIVATE)`, `ghp_`, `AKIA`, JWT `eyJ...`, `Bearer <token>`, `xox[bp]-` | 0 coincidencias en los 6 commits. Autores: 2 (sin correos de terceros) | PASS |
| D3 | Variables de entorno de la app | Lectura de `app/app.yaml` | Solo `DATABRICKS_WAREHOUSE_ID` (valueFrom del recurso), `CATALOG`, `SCHEMA`, `AI_SEARCH_INDEX`, `LLM_ENDPOINT`, `RAG_K`. Ninguna credencial; la identidad llega por el entorno de Databricks Apps (`Config()` del SDK). Añadida `SQL_TIMEOUT_SECONDS` (opcional, por defecto 90) en el código | PASS |
| D4 | Autenticación de la app | Desde la sesión del navegador integrado, `fetch` con `credentials: "omit"` a la URL de la app | `/api/engines` 401, `/api/overview` 401. `/healthz` responde 200 sin credenciales (devuelve solo `{"status":"ok"}`) | PASS (con nota D-10) |
| D5 | Autorización en la app | Lectura de `app/app.py` | La app no implementa roles propios: todo usuario autenticado en el workspace con permiso `CAN_USE` ve toda la flota y puede usar el asistente (consume LLM). No hay datos personales ni escrituras. | PASS para demo; ver D-04 |
| D6 | Permisos de la app | `apps get-permissions` / API `permissions/apps` | `CAN_MANAGE` solo para el propietario y el grupo `admins` (heredado). Sin acceso público ni a todo el workspace | PASS |
| D7 | Service principal | `apps get`, `SHOW GRANTS` | Un único recurso: SQL warehouse `CAN_USE`. `effective_user_api_scopes`: `iam.access-control:read`, `iam.current-user:read` | PASS |
| D8 | Grants de Unity Catalog | `SHOW GRANTS ON CATALOG workspace`, `ON SCHEMA workspace.mantenimiento`, `ON TABLE ...gold_predicciones`, `ON FUNCTION ...rul_model` | Principal `d19896a0-...` (app): `USE CATALOG`, `USE SCHEMA`, `SELECT` sobre el schema. Sin `EXECUTE` sobre el modelo (la app no llama al modelo), sin `MODIFY`, sin `CREATE`. Sin grants directos sobre el modelo. Mínimo privilegio cumplido | PASS |
| D9 | Permisos del endpoint de serving | API `permissions/serving-endpoints/<id>` | `CAN_MANAGE` solo propietario y `admins`. El service principal de la app **no** tiene `CAN_QUERY` (coherente: no lo usa) | PASS |
| D10 | Endpoint sin validación de entrada | 6 invocaciones directas (ver `02_MODEL_VALIDATION.md`, B3/B4) | El endpoint acepta `null` en cualquier variable y también una fila entera de nulos (devuelve 120,66), valores absurdos (-1e9) y columnas extra. Solo rechaza columnas ausentes, cadenas no numéricas y lote vacío | FAIL (media, ver D-02) |
| D11 | Dependencias de la app | `pip-audit -r app/requirements.txt --no-deps` (pip-audit 2.10.1, base OSV/PyPI) | "No known vulnerabilities found" (fastapi 0.143.0, uvicorn 0.53.0, databricks-sql-connector 4.6.0, databricks-sdk 0.152.0). Evidencia: `evidence/pip_audit_app.txt` | PASS |
| D12 | Dependencias de desarrollo | `pip-audit` sobre las versiones resueltas del entorno (`evidence/requirements-dev.resolved.txt`) | "No known vulnerabilities found". `requirements-dev.txt` declara rangos (`>=`), así que el resultado depende del entorno | PASS (nota D-07) |
| D13 | Linter | `ruff 0.16.8 check app src tests scripts --select E9,F,B --ignore F821` | 13 avisos: 6 importaciones sin usar, 7 `zip()` sin `strict=`. Sin errores de sintaxis ni nombres indefinidos. Los 114 `F821` son `dbutils`, `spark` y `display` de los notebooks de Databricks (esperados). Evidencia: `evidence/ruff.txt` | PASS (calidad menor) |
| D14 | Análisis estático de seguridad | `bandit 1.9.4 -r app src scripts` | 0 hallazgos con confianza alta de riesgo real. 9 `B608` (SQL por cadenas, confianza baja): son falsos positivos, los nombres de catálogo/schema se validan con regex al arrancar, `q_count` valida el nombre de tabla y los valores van como parámetros (`:u`, `:v`). 1 `B324` SHA1 en `chunking.py` para identificadores de fragmento (uso no criptográfico). 1 `B110` (`except: pass`) en `rag.py:419`, reintento acotado. 5 `B101` `assert` en notebooks. Evidencia: `evidence/bandit.txt` | PASS |
| D15 | Inyección SQL | Revisión de `app/app.py` y prueba de rango | Parámetros enlazados para `unit` y `version`; `unit` validado como entero 1 a 10.000; tablas limitadas por regex | PASS |
| D16 | Manejo de errores | Código + 25 pruebas negativas (`tests/test_negative_api.py`) | Antes: cualquier fallo SQL se convertía en `ready:false` con 200 (indistinguible de "aún no hay datos"). Ahora: 503 `data_unavailable` explícito; tabla ausente = estado vacío explícito. Las respuestas de error no incluyen trazas ni cadenas del servidor | PASS (tras corrección) |
| D17 | Logs | Lectura de `log.*` en `app.py` | Se registran estado, número de afirmaciones y `fuente:página`. No se registra la pregunta del usuario, ni tokens, ni filas de datos. Se cambió el log de fallo SQL para registrar solo el tipo de excepción (antes 300 caracteres del mensaje, que podría incluir fragmentos de la consulta). Quedan dos `log.warning` con 200/300 caracteres del mensaje del LLM/AI Search | PASS (nota D-08) |
| D18 | Prompt injection (revisión de `rag.py`, lectura) | Lectura de `V2_SYSTEM_PROMPT` y filtros | Mitigaciones presentes: el prompt declara que los pasajes son datos y que no se siguen sus instrucciones; un filtro por expresiones regulares elimina frases con apariencia de instrucción (EN/ES/FR) antes de pasar el contexto; la salida es JSON estructurado con verificación de afirmaciones y retirada de las no respaldadas. Limitación: las expresiones regulares no cubren todas las variantes y el LLM actúa como verificador de sí mismo; se evalúa en `03_RAG_VALIDATION.md` | PASS parcial (la eficacia se mide en la fase C) |
| D19 | XSS en la interfaz | `grep` de `innerHTML`, `insertAdjacentHTML`, `eval`, `document.write` en `index.html` | 0 usos. El DOM se construye con `createElement`/`textContent`, así que la salida del LLM y los datos se muestran como texto | PASS |
| D20 | Exposición pública de información técnica | `fetch` sin credenciales (D4) y revisión del contenido | Datos y asistente no son accesibles sin autenticación. La página carga CSS de Google Fonts (petición a un tercero, sin datos del usuario). Sin cabeceras CSP/`X-Frame-Options` propias | PASS con nota (D-06) |
| D21 | Acceso externo sin autenticación desde fuera del navegador | `curl` desde el entorno de auditoría y desde el PC | Bloqueado por el proxy de salida (403 `blocked-by-allowlist`): no concluyente. La comprobación válida es la de D4 | NOT_VERIFIED por curl; cubierto por D4 |
| D22 | GitHub Actions (`deploy.yml`) | Lectura | Ver apartado 3 | PASS con recomendaciones |

## 2. Cambios de código derivados de esta fase

- `app/app.py`: fallo de datos distinto de "tabla ausente" devuelve 503 explícito (D16), `_socket_timeout` en el conector SQL (90 s,
  configurable con `SQL_TIMEOUT_SECONDS`), log sin fragmentos de mensaje SQL (D17), validación de pregunta en blanco.
- Detalle y pruebas en `02_MODEL_VALIDATION.md`, B4.

## 3. Revisión de `.github/workflows/deploy.yml`

Aspectos correctos: `permissions: contents: read`; autenticación OAuth M2M con un service principal (no tokens personales);
secretos solo a través de `secrets.*`, no hay valores en el repositorio; la CLI se fija a una versión (`setup-cli@v0.280.0`);
`concurrency` evita despliegues simultáneos; `deploy-dev` solo en `push` a `main` y depende de `test`.

Riesgos y recomendaciones:
- **Media.** `DATABRICKS_CLIENT_SECRET` y el resto se exponen como variables de entorno a nivel de workflow, es decir, también
  al job `test`, que ejecuta `pip install -r requirements-dev.txt` y `pytest` (código y dependencias del PR). Un PR del mismo repositorio
  que modifique las pruebas podría leer el secreto. Mover `env:` a los jobs `validate` y `deploy-dev` y proteger el entorno con aprobación.
- **Baja.** Las acciones se referencian por etiqueta (`@v4`, `@v5`, `@v0.280.0`), no por SHA de commit. Fijar por SHA.
- **Baja.** El comentario del propio workflow indica "verificar en docs la etiqueta vigente de databricks/setup-cli": la existencia de
  `v0.280.0` no se ha podido comprobar (sin acceso a GitHub). `NOT_VERIFIED`. La CLI local usada en la auditoría es la v1.20.0; conviene alinear ambas.
- **Baja.** El CI instala `pyspark` pero el runner `ubuntu-latest` incluye Java, por lo que el test de equivalencia sí corre en CI (a diferencia
  de la copia local del usuario). No hay verificación de que lo haga: añadir `-rs` en pytest para que las omisiones sean visibles.
- **Baja.** El flujo despliega a `dev` con la identidad del service principal; los recursos creados se prefijan con `[dev <usuario>]`, no con la identidad del SP.
  Revisar antes de usarlo en producción.
- No se ha ejecutado el workflow (no se hace push): `NOT_VERIFIED`.

## 4. Hallazgos abiertos

| ID | Severidad | Hallazgo | Recomendación |
|---|---|---|---|
| D-01 | Media (**corregido en el árbol**) | `deploy.yml` exponía secretos de despliegue al job de pruebas (apartado 3) | Hecho: `env` solo en `validate` y `deploy-dev`; `pytest -rs`. Pendiente de push y de una ejecución real del workflow (`NOT_VERIFIED`) |
| D-02 | Media | El endpoint `dev_jjrm199867_rul-model-dev` no valida entradas: una fila de nulos devuelve una RUL (120,66) y un `null` en variables importantes cambia el resultado (3,12 a 67,24) sin error | Envolver el modelo en una función `pyfunc` que rechace nulos, infinitos y fuera de rango, o validar siempre antes de llamar. `src/03_score.py` ya valida con `validate_feature_frame` (nuevo). La app no llama al endpoint, así que hoy solo afecta a consumidores directos |
| D-03 | Media (**cerrado**) | Errores SQL se presentaban como estado vacío 200 | `app/app.py` corregido y desplegado a dev (E05, A_52); verificado por `tests/test_negative_api.py` y por el SHA-256 del `index.html` servido |
| D-04 | Baja | Cualquier usuario del workspace con `CAN_USE` puede consumir el LLM sin límite de tasa | Límite de tasa o cuota si la app se comparte; en Free Edition hay cuotas de plataforma |
| D-05 | Baja | `/healthz` accesible sin autenticación | Aceptable (no revela datos); documentarlo |
| D-06 | Baja | Sin cabeceras de seguridad (CSP, `X-Content-Type-Options`, `X-Frame-Options`) en las respuestas de la app; fuente externa de Google Fonts | Añadir middleware con cabeceras y alojar la fuente localmente |
| D-07 | Baja | `requirements-dev.txt` con rangos abiertos; los resultados de pip-audit dependen del entorno | Fijar versiones o generar un `requirements-dev.lock` |
| D-08 | Baja | `log.warning` con 200 a 300 caracteres de mensajes del LLM/AI Search | Registrar solo el tipo de excepción, como ya se hace con SQL |
| D-09 | Baja | Carpetas locales del PC (`logs/`, `cola/`, `cola2/`, `consultas/`, `herramientas/`) no figuran en `.gitignore`; contienen identificadores del workspace | Añadirlas a `.gitignore` antes de cualquier commit desde la copia del PC |
| D-10 | Informativa | `/healthz` responde 200 sin sesión: no está claro si lo atiende la plataforma o la app (ambas devuelven lo mismo) | Sin acción |
| D-11 | Baja | `oauthlib 3.3.1` (dependencia transitiva de `databricks-sql-connector`, que fija `oauthlib<4.0.0`): PYSEC-2026-4114, comparación no constante en `code_challenge_method_plain` del **servidor** OAuth PKCE. La app solo usa `oauthlib` como cliente; no hay servidor OAuth propio | Sin acción posible hasta que el conector permita `oauthlib>=4`; vigilar en el siguiente `pip-audit` |
| D-12 | Media | El endpoint de serving desapareció del workspace entre el 14 y el 15 de octubre sin registro accesible (ver `02_MODEL_VALIDATION.md`, B3.5). No afecta al dashboard (lee la tabla por lotes) pero sí a cualquier integración directa | Recreado con el bundle; comprobar su existencia antes de una demo; considerar documentar que Free Edition no garantiza persistencia de endpoints |

## 5. Revalidación del 15 de octubre de 2026

Mismo entorno de auditoría (Python 3.13.16). Evidencias en `audit/evidence/*_2026-10-15.txt` y `evidence/revalidation_2026-10-15/`.

| Control | Comando | Versión | Resultado |
|---|---|---|---|
| Secretos en el árbol del PC y en el historial | `grep -rnEIi` con patrones `dapi...`, `ghp_`, `AKIA`, `xox[bp]-`, claves privadas, JWT, `client_secret=`, `password=`, `Bearer`; `git log --all -p` | grep 3.x | 0 coincidencias en ambos (`secrets_scan_2026-10-15.txt`); `git ls-files` sin `.env`, `.pem`, `.key` |
| Dependencias de la app | `pip-audit -r app/requirements.txt` | pip-audit 2.10.1 | 1 paquete transitivo con 1 aviso: `oauthlib 3.3.1` PYSEC-2026-4114 (D-11). `fastapi 0.143.0`, `uvicorn 0.53.0`, `databricks-sql-connector 4.6.0`, `databricks-sdk 0.152.0` sin avisos (`pip_audit_app_2026-10-15.txt`) |
| Dependencias de desarrollo resueltas | `pip-audit -r requirements-dev.resolved_2026-10-15.txt` | 2.10.1 | Mismo aviso de `oauthlib` (`pip_audit_dev_2026-10-15.txt`) |
| Linter | `ruff check app src tests scripts --select E9,F,B --ignore F821` | ruff 0.16.8 | 15 avisos: 7 `F401` importaciones sin usar, 8 `B905` `zip()` sin `strict`; 0 errores de sintaxis (`ruff_2026-10-15.txt`) |
| Análisis estático | `bandit -r app src scripts` | bandit 1.9.4 | 1 High (`B324` SHA1 no criptográfico en `chunking.py`), 9 Medium (`B608` SQL por cadenas con nombres validados por regex y parámetros enlazados), 6 Low; sin cambios respecto al 14 de octubre (`bandit_2026-10-15.txt`) |
| Autenticación de la app | `fetch("/api/overview", {credentials: "omit"})` desde el navegador integrado | | 401 (`app_api_capture_2026-10-15.json`) |
| Permisos de la app | `apps get-permissions` (`A_10`) | CLI 1.20.0 | `CAN_MANAGE` propietario y `admins` |
| Grants UC del service principal | `SHOW GRANTS ON SCHEMA workspace.mantenimiento` (`A_46`) | | `d19896a0-...`: `USE SCHEMA`, `SELECT` (sin `MODIFY`, `CREATE` ni `EXECUTE`) |
| Permisos del endpoint recreado | `GET /api/2.0/permissions/serving-endpoints/dd2a3f66...` (`A_64`) | | `CAN_MANAGE` propietario y `admins`; el SP de la app no aparece |
| Pruebas automatizadas | `python3 -m pytest -q -rs` | pytest 9.1.1 | 142 passed, 0 skipped (incluye 25 negativas, 11 de fase E, 4 del protocolo de selección) |
| GitHub Actions | Lectura de `deploy.yml` corregido | | Secretos solo en `validate` y `deploy-dev`; no ejecutado (sin push): `NOT_VERIFIED` |
| Prompt injection en el RAG | Solo lectura (fase C) | | Sin cambios en esta fase; resultado en `03_RAG_VALIDATION.md` |
