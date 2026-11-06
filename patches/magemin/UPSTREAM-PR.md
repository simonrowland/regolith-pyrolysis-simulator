# Draft upstream pull request: MAGEMin 1.9.6 activity references and LP loop cap

Status: owner-gated draft; not filed.

Patches in this directory, in order: 0001, 0002, 0003, each formatted with `git format-patch` against
`40ed109` (tag v1.9.6). MAGEMin's sources use CRLF line endings, so apply with `git am --keep-cr`.
The text below is the proposed pull-request description.


Three fixes against MAGEMin 1.9.6 (`40ed109`).

THERMOCALC `aMgO` used a zero periclase reference. In `compute_activities()` the `tc` branch calls `G_EM_function` for `"per"` and did not store `PP_db.gbase`, so `aMgO` was `exp(Gamma_MgO/RT)`, about 1e-21 at 1873 K. The other tc oxide references store `gbase` with the factor multiply commented out and with no `/4`. The `/4` is only the sb21/sb24 4-cation conversion. The tc branch now stores `PP_db.gbase` and nothing else. At 1873.00 K and log10 fO2 = -8, Kume 2000 sample 305 goes from 1.568821e-21 to 0.567936 (1e-6 bar) and from 1.287131e-21 to 0.465924 (1 bar). An Fe-free periclase-saturated bulk (MgO 50 wt%) goes from 2.76e-21 to 0.999845. `aSiO2`, `aAl2O3` and the assemblage do not move.

The legacy LP loop stopped only when `PC_checked == 2`, including the `max_LP_ite` clause. `PC_checked` increments only while `gamma_norm < 1`, and only twice, so a point that never gets under 1 does not stop. The diagnostic arrays are `it_f*2` long (`it_f` and `max_LP_ite` are 256), and the loop writes one past that allocation. The cap now ends the loop by itself. A converged point still waits for two pseudocompound re-checks. The hang was not reproduced: ig `--solver=0` and sb points across a wide P-T grid all exited. The change is offered on the control flow. While `gamma_norm` stays at or above 1, `iterate` is never cleared.

`aFeO` on sb21 divided the 4-cation wustite energy by 4 and then stored `PP_db.gbase` again. The second store is what the tc path used, because `fper` is one FeO and the tc branch did not assign `gbase` itself. It also discarded the sb `/4`. On sb21 at 1600 C and 100 kbar the printed activity was 5.599946e+25. With the scaling kept it is 0.473446, equal to `exp((Gamma_FeO - G_wu/4)/RT)`. The tc assignment now sits inside the tc branch. sb11, whose wustite is one FeO, is unchanged, and the four ig points above keep the same `aFeO`, `aMgO`, `aSiO2`, `aAl2O3`, assemblage and iteration count. sb24 is unaffected: it has no `FeO` oxide, and its `aFeO` comes from the later Fe+O block, which already divides by 4 for `EM_database == 2`; 0003 does not touch that block.


## Verification summary

Build: `make -j8 all` from a clean v1.9.6 clone (clang, Homebrew OpenMPI and NLopt, Accelerate), single-process run.

0001, `--db=ig`, 1873 K, log10 fO2 = -8:

| Case | aMgO before | aMgO after | exp((Gamma_MgO - G_per)/RT) |
| --- | --- | --- | --- |
| CaO-MgO-Al2O3-SiO2 slag (Kume 2000 sample 305), 1 bar | 1.287e-21 | 0.46592 | 0.46592 |
| Periclase-saturated bulk, 1 bar | 2.762e-21 | 0.99984 | 0.99984 |

aSiO2, aAl2O3, the assemblage and the iteration count are unchanged before vs after.

0003, `--rg=sb --db=sb21 --Temp=1600 --Pres=100 --sys_in=mol --Bulk=38,3,2,8,48,1`: printed aFeO 5.6e+25 before,
0.473446 after (= exp((Gamma_FeO - G_wu/4)/RT)). ig/tc and sb11 results are unchanged.

0002: the non-terminating path follows from the control flow (the cap clause also required PC_checked == 2, which
only increments while gamma_norm < 1). It was NOT reproduced: a 164-point sweep over ig/sb11/sb21, 25-3500 C and
0.001-800 kbar all terminated. The patch leaves already-converging points unchanged.
