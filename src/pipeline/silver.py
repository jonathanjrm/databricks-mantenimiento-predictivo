"""Capa SILVER: datos tipados, validados y sin columnas constantes.

Sigue siendo streaming table: la limpieza es fila a fila, sin agregaciones, así que se
procesa de forma incremental sobre lo que Bronze va anadiendo.

Columnas eliminadas (sin información en FD001, una sola condición de operación y un
solo modo de fallo): setting3, s1, s5, s6, s10, s16, s18, s19.
  - setting3, s1, s5, s10, s16, s18, s19: desviación tipica exactamente 0 en train y test.
  - s6: solo toma dos valores (21.60 y 21.61), ruido de cuantizacion.
Se comprueba con los datos reales en tests/test_cmapss.py. setting1 y setting2 se
conservan (varian por ruido) pero no se usan como variables del modelo.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

import sys  # noqa: E402

# Hace importable el paquete `lib` (src/lib). El bundle pasa la ruta de src/ en la
# configuración del pipeline (mantenimiento.src_root). verificar en docs: sys.path y
# directorio de trabajo de los ficheros fuente de un pipeline.
_SRC_ROOT = spark.conf.get("mantenimiento.src_root", "")  # noqa: F821
if _SRC_ROOT and _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
from lib.cmapss import CONSTANT_SENSORS, CONSTANT_SETTINGS  # noqa: E402
from lib.spark_features import parse_rul_wholetext  # noqa: E402
from lib.spark_schema import plausibility_rules  # noqa: E402

# Rangos de plausibilidad: aviso (expect), no descarte. Un valor fuera de rango suele
# indicar un sensor averiado o un fallo de unidades, y es información útil para la
# operación, no algo que borrar en silencio.
RANGOS = plausibility_rules()


@dp.table(
    name="silver_lecturas",
    comment="Silver: lecturas validadas, sin sensores constantes. Clave: (split, unit, cycle).",
    table_properties={"quality": "silver"},
    cluster_by=["unit", "cycle"],
)
@dp.expect_or_drop("unit_valido", "unit IS NOT NULL AND unit > 0")
@dp.expect_or_drop("ciclo_valido", "cycle IS NOT NULL AND cycle > 0")
@dp.expect_or_drop("split_valido", "split IN ('train', 'test')")
@dp.expect_all(RANGOS)
def silver_lecturas():
    return (
        spark.readStream.table("bronze_cmapss_lecturas")  # noqa: F821
        .drop(*CONSTANT_SENSORS, *CONSTANT_SETTINGS)
    )


@dp.table(
    name="silver_rul_test",
    comment="Silver: RUL real por motor de test (unit = posicion en RUL_FD001.txt).",
    table_properties={"quality": "silver"},
)
@dp.expect_or_drop("unit_valido", "unit > 0")
@dp.expect_or_drop("rul_valida", "rul_real IS NOT NULL AND rul_real >= 0")
def silver_rul_test():
    return parse_rul_wholetext(spark.readStream.table("bronze_cmapss_rul"))  # noqa: F821
