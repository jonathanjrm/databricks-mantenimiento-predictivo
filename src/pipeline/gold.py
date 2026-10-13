"""Capa GOLD: variables para el modelo, verdad de test e indicador de salud.

Por que materialized views: las ventanas moviles (medias, desviaciones, pendiente) y las
estadisticas de referencia necesitan ver el historial completo de cada motor, algo que en
streaming exigiria estado sin límite. Una materialized view recalcula (o refresca de forma
incremental cuando puede) a partir de silver y siempre es coherente con ella.

Etiqueta RUL (solo train): RUL_raw = último ciclo del motor - ciclo actual; RUL = min(RUL_raw, 125).
El recorte a 125 ciclos es la práctica estándar en la literatura de C-MAPSS (Heimes, 2008;
Li, Ding y Sun, 2018): al inicio de la vida no hay degradacion observable y modelar la RUL
como lineal por tramos con techo mejora la estimación.
"""
from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

import sys  # noqa: E402

# Hace importable el paquete `lib` (src/lib). El bundle pasa la ruta de src/ en la
# configuración del pipeline (mantenimiento.src_root). verificar en docs: sys.path y
# directorio de trabajo de los ficheros fuente de un pipeline.
_SRC_ROOT = spark.conf.get("mantenimiento.src_root", "")  # noqa: F821
if _SRC_ROOT and _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
from lib import spark_features as SF  # noqa: E402


@dp.materialized_view(
    name="gold_features",
    comment=("Gold: variables moviles por motor y ciclo (train y test) y etiqueta RUL recortada "
             "a 125 (solo train)."),
    table_properties={"quality": "gold"},
    cluster_by=["split", "unit"],
)
@dp.expect_or_fail("clave_presente", "unit IS NOT NULL AND cycle IS NOT NULL")
@dp.expect_or_fail("rul_train_valida", "split <> 'train' OR (rul IS NOT NULL AND rul BETWEEN 0 AND 125)")
def gold_features():
    silver = spark.read.table("silver_lecturas")  # noqa: F821
    return SF.add_rolling_features(SF.add_rul_train(silver))


@dp.materialized_view(
    name="gold_test_verdad",
    comment=("Gold: último ciclo observado de cada motor de test unido con su RUL real "
             "(RUL_FD001.txt). Es la verdad contra la que se evalua el modelo."),
    table_properties={"quality": "gold"},
)
@dp.expect_or_fail("verdad_presente", "rul_real IS NOT NULL")
def gold_test_verdad():
    silver = spark.read.table("silver_lecturas").filter("split = 'test'")  # noqa: F821
    w = Window.partitionBy("unit").orderBy(F.col("cycle").desc())
    last = (silver.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1")
                  .select("unit", F.col("cycle").alias("cycle_final")))
    rul = spark.read.table("silver_rul_test")  # noqa: F821
    return last.join(rul.select("unit", "rul_real"), "unit", "left")


@dp.materialized_view(
    name="gold_salud_parametros",
    comment=("Gold: parámetros del indicador de salud (1 fila). Referencia sana = primeros 30 ciclos "
             "de cada motor de train; pesos = desplazamiento medio de cada sensor en los últimos 30."),
    table_properties={"quality": "gold"},
)
def gold_salud_parametros():
    return SF.health_params(spark.read.table("silver_lecturas"))  # noqa: F821


@dp.materialized_view(
    name="gold_salud_curva",
    comment=("Gold: indicador de salud por motor y ciclo (0 = sano, 1 = nivel medio de fallo). "
             "Vista resumen de los sensores, no una predicción."),
    table_properties={"quality": "gold"},
    cluster_by=["split", "unit"],
)
def gold_salud_curva():
    silver = spark.read.table("silver_lecturas")  # noqa: F821
    params = spark.read.table("gold_salud_parametros")  # noqa: F821
    return SF.health_curve(silver, params)


@dp.materialized_view(
    name="gold_control_calidad",
    comment="Gold: conciliación de filas, motores y claves por split.",
    table_properties={"quality": "gold"},
)
@dp.expect_or_fail("claves_unicas", "filas = claves_distintas")
@dp.expect("train_100_motores", "split <> 'train' OR motores = 100")
@dp.expect("test_100_motores", "split <> 'test' OR motores = 100")
def gold_control_calidad():
    s = spark.read.table("silver_lecturas")  # noqa: F821
    return s.groupBy("split").agg(
        F.count(F.lit(1)).alias("filas"),
        F.countDistinct("unit", "cycle").alias("claves_distintas"),
        F.countDistinct("unit").alias("motores"),
        F.max("cycle").alias("ciclo_max"),
    )
