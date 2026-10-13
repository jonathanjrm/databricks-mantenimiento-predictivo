# Informe: precisión y trazabilidad del asistente RAG

Fecha: 13 de octubre de 2026. Índice `workspace.mantenimiento.manual_chunks_index` (720 fragmentos: 703 del FAA Powerplant Handbook FAA-H-8083-32B y 17 del artículo de NASA de Saxena et al., 2008). LLM y juez: `databricks-gpt-oss-120b`.

## 1. Caso analizado: «¿Cómo se inspecciona el compresor de alta presión?»

### Qué contiene realmente el manual (verificado en el texto extraído del PDF, no con el modelo)

| Búsqueda en el texto | Páginas PDF |
|---|---|
| "HPC" (sigla) | ninguna |
| "high-pressure compressor" cerca de "inspect" | ninguna |
| "high-pressure compressor" en cualquier contexto | 57, 59, 71, 285 (descripción de ejes y zonas), 472 (glosario: N2) |
| "compressor blade" cerca de "inspect" | 413, 418 |
| "compressor section" cerca de "inspect" | 415, 499 (índice) |
| "borescope" | 462 (glosario) |

Conclusión: **el manual no tiene un procedimiento de inspección específico del compresor de alta presión.** Trata la inspección de la sección del compresor en general (páginas PDF 413 a 415, páginas impresas 10-45 a 10-47):

- p.413: la eliminación de daños menores en álabes es admisible solo dentro de los límites del fabricante; tras reparar, inspección por partículas magnéticas o líquidos penetrantes fluorescentes; no se tolera ninguna grieta; en motores de flujo centrífugo se retira la rejilla de admisión y se inspecciona el inductor con luz intensa.
- p.414: figura 10-57 con límites de reparación *típicos* (el texto extraído de la figura está desordenado).
- p.415: inspección de álabes guía y de remolino, y la frase «Any inspection and repair of turbine compressor section components require that the technician always use the specific manufacturer's current information for evaluation and limits of repairs».

### Antes (configuración `v1_baseline`, la que estaba en producción)

- **Consulta en inglés generada:** «high pressure compressor inspection borescope visual examination HP compressor maintenance FAA Powerplant Handbook NASA turbine degradation article synonyms: compressor stage inspection, engine health monitoring, turbine wear analysis». La consulta arrastra nombres de documentos y sinónimos genéricos que meten ruido.
- **Fragmentos recuperados (top 6, puntuación híbrida):**
  - p.329, 0,984: análisis espectrométrico de aceite.
  - p.499, 0,976: índice del libro.
  - NASA p.5, 0,933: fórmula de degradación.
  - p.415, 0,926: el único relevante.
  - p.475, 0,902: glosario.
  - p.460, 0,873: glosario.

  En total, 1 de 6 fragmentos era útil.
- **Respuesta:** «Para inspeccionar el compresor de alta presión se deben seguir estos pasos básicos» y 4 pasos tomados de p.415.
  - Las frases citadas existen en p.415, pero se presentan como procedimiento del HPC, cosa que el manual no dice.
  - 6 de las 10 frases no tenían cita.
  - El estado implícito era «respondida».
- **Inestabilidad:** en otras dos ejecuciones idénticas la misma pregunta devolvió «No lo encuentro en los manuales proporcionados» y una respuesta citando p.415. No hay forma de distinguir «no encontrado» de «solo hay información general».

### Después (configuración `v2`)

- **Consultas generadas:** «How is the high-pressure compressor inspected?», «HPC inspection procedure» o «How to inspect the HPC (high-pressure compressor)?», «compressor section inspection», más la pregunta original.
- **Fragmentos:**
  - p.413 y p.415 en las posiciones 1 y 2 en las dos ejecuciones.
  - Índice y preliminares eliminados.
  - Siguen entrando fragmentos poco útiles (glosario p.475 y p.477, NASA p.2 y p.3, p.391) en las posiciones 3 a 6.
- **Estado:** `insufficient_evidence` en las dos ejecuciones.
  - En la primera, el modelo devolvió «answered» y el control de cobertura lo corrigió (`general_only`). El ajuste queda registrado en la traza.
  - Huecos declarados: «The passages describe general compressor inspection... not specifically the high-pressure compressor».
  - Nota de fabricante: cita la frase de p.415.
- **Afirmaciones mostradas:** 2 o 3, todas verificadas contra p.413 y p.415.

### ¿Las páginas citadas respaldan la respuesta? (revisión manual)

| Afirmación (v2) | Página | Veredicto manual |
|---|---|---|
| Piezas reparadas: partículas magnéticas o penetrante fluorescente | 413 | Respaldada literalmente |
| Seguir la información vigente del fabricante para evaluación y límites | 415 | Respaldada literalmente |
| Retirar la rejilla, limpiar el inductor, luz intensa, girar el compresor y buscar grietas | 413 | **Parcial.** El manual lo dice para motores de flujo centrífugo y la afirmación omite esa condición. El juez automático la dio por respaldada incluso tras endurecer su regla. Es una limitación conocida. |

## 2. Métricas: antes y después

Conjunto de 12 preguntas (`eval/rag_eval_set.json`) con páginas de referencia comprobadas en el texto del PDF y estados aceptados. Se repitió `v1_baseline` en cada ejecución porque el modelo no es determinista.

| Ejecución | Config. | Estado correcto | hit@6 | MRR | Recall de páginas | Afirmaciones mostradas | Respaldadas | Parciales | No respaldadas | Sin cita |
|---|---|---|---|---|---|---|---|---|---|---|
| before | v1 | 0,83 | 1,00 | 0,84 | 0,73 | 49 | 24,5 % | 0 % | 75,5 % | 26 |
| final_run1 | v1 | 0,83 | 1,00 | 0,78 | 0,68 | 49 | 18,4 % | 8,2 % | 73,5 % | 35 |
| final_run2 | v1 | 0,83 | 0,89 | 0,78 | 0,68 | 59 | 28,8 % | 8,5 % | 62,7 % | 37 |
| final_run1 | **v2** | **1,00** | 1,00 | **0,94** | **0,80** | 26 | **88,5 %** | 11,5 % | **0 %** | 2* |
| final_run2 | **v2** | **1,00** | 1,00 | **1,00** | **0,82** | 24 | **95,8 %** | 4,2 % | **0 %** | 3* |

\* En v2, las «sin cita» son resúmenes retirados que aparecen en la lista de eliminados, no afirmaciones mostradas.

Las frases sin cita cuentan como no respaldadas. Solo entre las frases citadas de v1, el juez respalda el 64 % y el 77 % en las dos ejecuciones finales.

Las preguntas de fabricante y fuera de alcance:
- **v1:** contestaba siempre «No lo encuentro», tanto si no había nada como si el manual remite al fabricante. La pregunta de límites de reparación salía como «respondida» en las 3 ejecuciones y la del HPC en 2 de 3.
- **v2:** distingue `not_found` (precio de un álabe), `requires_manufacturer_documentation` (par de apriete del CFM56-7B, intervalo boroscópico del PW1100G, límites exactos de la figura 10-57) e `insufficient_evidence` (inspección del HPC).

## 3. Qué cambió (archivo `app/rag.py`)

1. **Consultas:** 3 consultas en inglés (traducción, términos técnicos y tema general) más la original, fusionadas con Reciprocal Rank Fusion.
2. **Filtro de páginas:** se descartan el índice, el sumario y los preliminares. Se clasifican por página a partir del primer fragmento; el glosario se conserva.
3. **Respuesta estructurada:** `status`, `summary`, `claims` con citas, `gaps` y `manufacturer_note`, siempre en inglés.
4. **Verificación de cada afirmación y del resumen** con un juez. Lo no respaldado o no verificable se retira y se lista aparte; nunca se reescribe.
5. **Control de cobertura** (específica / solo general / ninguna). Si el modelo dice «answered» con cobertura general, el estado pasa a `insufficient_evidence` y queda registrado.
6. **Valores numéricos:** si la pregunta pide límites, pares de apriete o intervalos, el estado es `requires_manufacturer_documentation` y las cifras se retienen (veredicto `WITHHELD`). Las cifras del manual son típicas y el texto de sus figuras sale desordenado.
7. **Trazabilidad en la app:** consultas, fragmentos con puntuación y tipo de página, veredicto por afirmación, afirmaciones retiradas, control de cobertura y ajustes automáticos.

Pruebas: `tests/test_rag.py` (26 pruebas con clientes falsos) y el arnés en Databricks `src/07_eval_assistant.py` (job `mantenimiento_eval_assistant`). Resultados completos, con todos los fragmentos y veredictos, en `eval/resultados/`.

## 4. Limitaciones

- El juez es el mismo modelo que responde. Es un filtro, no una verdad de referencia. El caso de la condición omitida («centrifugal flow engines») muestra que puede aprobar afirmaciones que generalizan.
- 12 preguntas y 2 ejecuciones por configuración: los porcentajes orientan, no son estadísticamente sólidos.
- Las posiciones 3 a 6 todavía traen fragmentos poco útiles. El modelo de embeddings `gte-large-en` es solo inglés y hay texto de figuras desordenado.
- v2 hace más llamadas al LLM (unas 5 a 10 por pregunta), así que es más lento.
