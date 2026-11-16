# Benchmark trusses (Examples 1-5 of the paper)

Each folder holds the model extracted from `examples/autocad/AutoCad_OpenSees_3D_truss.dwg` by the extraction agent
(`get_truss_joints.py`), and the outputs of `python run_example.py <folder>/truss.txt --optimize PSO`
(population 50, 100 generations, seed 42, linear analysis):

| Folder | Structure | Joints / members / loads / supports | `result.txt` | `truss_optimized.txt`, `result_optimized.txt` | `run_log.txt` |
|---|---|---|---|---|---|
| `ex1_dome_120` | Geodesic dome | 49 / 120 / 13 / 12 | initial design | PSO design | full console log |
| `ex2_pyramid_25` | Tapered tripod pyramidal tower | 10 / 25 / 2 / 4 | | | |
| `ex3_lattice_42` | Vertical lattice truss, 3 modules | 16 / 42 / 8 / 4 | | | |
| `ex4_grid_72` | Double-layer space grid, 3 bays | 25 / 72 / 4 / 4 | | | |
| `ex5_grid_144` | Double-layer space grid, 6 bays | 46 / 144 / 10 / 4 | | | |

Reproduced versus reported values (paper, Figs. 8, 10 and 11):

| Example | Max displacement (mm) | Initial mass (kg) | Initial max unity | PSO optimized mass (kg) | Paper |
|---|---|---|---|---|---|
| 1 | 168.119 | 7814.832 | 25.31 | 16199.434 | 168.119 mm (Fig. 8); 16234.680 kg PSO, 15449.616 kg best of PSO/GA/DE (Fig. 10) |
| 2 | 28.564 | 174.262 | 23.30 | 408.838 | 174.262 kg, unity 23.28, 408.838 kg |
| 3 | 3.208 | 3112.769 (feasible) | < 1 | 1240.318 | 3112.769 kg feasible, 1240.318 kg |
| 4 | 10.556 | 558.957 | 1.31 | 722.201 | 558.957 kg, unity 1.31, 722.201 kg |
| 5 | 43.445 | 1123.566 | 3.06 | 1998.338 | 1123.566 kg, unity 3.06, 1998.338 kg |

Load magnitudes. The load blocks in the released drawing were edited after the runs reported in the paper. The
`truss.txt` files of Examples 2-5 therefore carry the load cases of the paper (Fig. 7), with geometry, sections and
supports unchanged from the extraction: 2 x 50 kN lateral (Ex. 2); 4 x 50 kN vertical + 30 kN along X and along Y at
two top joints (Ex. 3); 4 x 50 kN vertical (Ex. 4); 10 x 30 kN vertical (Ex. 5). Example 1 is used as drawn.
Joint and element numbers follow the selection order, so they can differ from the numbering in the paper figures.

The SAP2000 cross-check of any model is obtained with `python sap2000_truss.py` (requires SAP2000).
