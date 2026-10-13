# Fuente de los datos: NASA C-MAPSS, subconjunto FD001

**Conjunto de datos:** Turbofan Engine Degradation Simulation Data Set (C-MAPSS), subconjunto FD001
(una condición de operación, un modo de fallo: degradación del compresor de alta presión).

**Cita:** A. Saxena, K. Goebel, D. Simon y N. Eklund, "Damage Propagation Modeling for Aircraft Engine
Run-to-Failure Simulation", *Proceedings of the 1st International Conference on Prognostics and
Health Management (PHM08)*, Denver, CO, 2008.

**Origen original:** NASA Ames Prognostics Data Repository, NASA Ames Research Center, Moffett Field, CA.
Datos de la administración federal de EE. UU. (NASA), de dominio público.

**Copia usada aquí:** espejo en GitHub `edwardzjl/CMAPSSData`
(`https://raw.githubusercontent.com/edwardzjl/CMAPSSData/master/`), descargado el 13 de octubre de 2026.
Los ficheros no se han modificado.

| Fichero | Contenido | Comprobado |
|---|---|---|
| `train_FD001.txt` | 100 motores hasta el fallo | 20631 filas, 100 motores |
| `test_FD001.txt` | 100 motores truncados antes del fallo | 13096 filas, 100 motores |
| `RUL_FD001.txt` | RUL real al final de cada motor de test, un valor por línea (línea i = motor i) | 100 valores |
| `readme.txt` | Descripción original de NASA (codificación Windows-1252) | |

Formato: 26 columnas separadas por espacio (motor, ciclo, 3 ajustes operativos y 21 sensores), con
espacios al final de cada línea.
