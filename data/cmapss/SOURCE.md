# Data source: NASA C-MAPSS, subset FD001

**Dataset:** Turbofan Engine Degradation Simulation Data Set (C-MAPSS), subset FD001
(one operating condition, one fault mode: high-pressure compressor degradation).

**Citation:** A. Saxena, K. Goebel, D. Simon and N. Eklund, "Damage Propagation Modeling for Aircraft Engine
Run-to-Failure Simulation", *Proceedings of the 1st International Conference on Prognostics and
Health Management (PHM08)*, Denver, CO, 2008.

**Original origin:** NASA Ames Prognostics Data Repository, NASA Ames Research Center, Moffett Field, CA.
Data of the US federal administration (NASA), public domain.

**Copy used here:** GitHub mirror `edwardzjl/CMAPSSData`
(`https://raw.githubusercontent.com/edwardzjl/CMAPSSData/master/`), downloaded on 13 October 2026.
The files have not been modified.

| File | Content | Checked |
|---|---|---|
| `train_FD001.txt` | 100 engines run to failure | 20631 rows, 100 engines |
| `test_FD001.txt` | 100 engines truncated before failure | 13096 rows, 100 engines |
| `RUL_FD001.txt` | Actual RUL at the end of every test engine, one value per line (line i = engine i) | 100 values |
| `readme.txt` | Original NASA description (Windows-1252 encoding) | |

Format: 26 space-separated columns (engine, cycle, 3 operational settings and 21 sensors), with
trailing spaces at the end of every line.
