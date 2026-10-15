Mantenimiento predictivo en Databricks Free Edition, construido de extremo a extremo y auditado antes de publicarlo.

NASA C-MAPSS FD001 (100 turbofanes llevados hasta el fallo, 100 truncados para test) convertido en una cadena funcional sobre cómputo serverless:

- Pipeline medallion con Lakeflow: 33.727 lecturas, 85 variables de ventanas hacia atrás, RUL recortada a 125. Spark y pandas coinciden hasta 1e-9.
- Modelo de RUL con HistGradientBoosting en MLflow y Unity Catalog, alias champion, en un endpoint de serving con escalado a cero. RMSE de 18,4 ciclos en el test de FD001 frente a 21,8 de Ridge y 43,1 de la media constante.
- Una app de sala de control: flota isométrica de 100 motores, filtros por criticidad, franja de incertidumbre, versión del modelo y hora de scoring en pantalla.
- Un asistente sobre el FAA Powerplant Handbook que cita páginas, retira las afirmaciones sin respaldo y retiene los valores de fabricante. En 85 preguntas held-out en tres idiomas: 97,6 % de estados correctos, 0 afirmaciones mostradas sin respaldo según dos jueces, 100 % de abstención apropiada. Si le piden un procedimiento del HPC que el manual no contiene, lo dice.

Lo que añadió la auditoría, porque un dashboard que parece correcto no es evidencia:
- Champion reproducido bit a bit; endpoint invocado para los 100 motores, 0 discrepancias con el dashboard.
- El conjunto de test había influido en la selección del modelo, así que se declara como conjunto visto; la promoción usa ahora validación solo con train.
- La incertidumbre no está calibrada; las recomendaciones son orientativas. Cerca del fallo el modelo sobreestima la vida en 5,5 ciclos.
- 142 pruebas, sin secretos en el árbol ni en el historial, service principal de mínimo privilegio.

Límites: datos de simulación, puntuaciones por lotes y no telemetría en vivo, disponibilidad no continua en Free Edition. No es un sistema de mantenimiento certificado.

Método: yo definí la arquitectura, los parámetros técnicos y la secuencia, y revisé cada fase. Los informes de auditoría y las evidencias están en el repositorio.

Carrusel en los comentarios. github.com/jonathanjrm/databricks-mantenimiento-predictivo

#Databricks #MLOps #MantenimientoPredictivo #RAG
<!-- Traducción del texto de LINKEDIN_POST_EN.md (1.938 caracteres en inglés). El límite de 2.000 caracteres de la misión se aplica a la versión en inglés; esta versión en español tiene más caracteres por la propia longitud del idioma y se indica a continuación. -->
<!-- character count: 2229 characters (Python len() del texto anterior, saltos de línea incluidos) -->
