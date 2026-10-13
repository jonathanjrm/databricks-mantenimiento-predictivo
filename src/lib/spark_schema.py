"""Esquema explicito de los ficheros C-MAPSS y rangos de plausibilidad (para expectations)."""
from pyspark.sql.types import DoubleType, IntegerType, StructField, StructType

from lib.cmapss import COLUMNS, SENSORS, SETTINGS

# 26 columnas; unit y cycle enteros, el resto double. Los ficheros traen espacios finales:
# con sep=" " aparecen campos vacios extra que Spark descarta al ser el esquema mas corto.
READINGS_SCHEMA = StructType(
    [StructField(c, IntegerType() if c in ("unit", "cycle") else DoubleType(), True) for c in COLUMNS]
)

# Rangos de plausibilidad por columna: [min, max] observados en train y test de FD001
# ampliados un 25 % del recorrido a cada lado. Se usan con `expect` (aviso, no descarte).
_OBSERVED = {
    "setting1": (-0.0087, 0.0087), "setting2": (-0.0006, 0.0007),
    "s2": (641.13, 644.53), "s3": (1569.04, 1616.91), "s4": (1382.25, 1441.49),
    "s7": (549.85, 556.06), "s8": (2387.89, 2388.56), "s9": (9021.73, 9244.59),
    "s11": (46.8, 48.53), "s12": (518.69, 523.76), "s13": (2387.88, 2388.56),
    "s14": (8099.94, 8293.72), "s15": (8.3249, 8.5848), "s17": (388.0, 400.0),
    "s20": (38.14, 39.43), "s21": (22.8942, 23.6419),
}


def plausibility_rules(margin: float = 0.25) -> dict:
    rules = {}
    for col, (lo, hi) in _OBSERVED.items():
        span = hi - lo
        rules[f"rango_{col}"] = f"{col} BETWEEN {lo - margin * span:.6f} AND {hi + margin * span:.6f}"
    return rules


__all__ = ["READINGS_SCHEMA", "plausibility_rules", "SENSORS", "SETTINGS"]
