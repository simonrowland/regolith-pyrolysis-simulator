# Romero-Serrano & Pelton (1999) extraction audit — in progress

Source: `romero-serrano-1999-binary-ternary-silicates`, 10-page born-digital PDF. The local sidecar records no DOI. Duplicate checks for title, authors/year, and the paper's likely DOI returned no existing extract.

## Printed table

| Table | Disposition under review |
|---|---|
| 1, thermodynamic properties relative to elements at 298.15 K (PDF page 2) | The 20-row table gives reference enthalpy/entropy baselines and heat-capacity polynomial coefficients for pure oxides and silicate compounds. Candidate quantity tokens are `delta_fH`, `S`, and `cp`. These are model inputs, not activity measurements, and the table gives no per-row source attribution. Whether to store them as compilation-assessed thermochemistry or retain them as model-input context needs a ruling. |

The MinerU table draft is `p002-t01` (20 body rows). The printed table is confirmed on physical PDF page 2; the table is not transcribed into scored observations in this WIP.

## Printed quoted measurements outside tables

The prose reports four cited invariant points with temperature and composition. They are not activity rows:

- Glasser and Osborn [19]: 1480 °C, 55 wt% SiO2, 25 wt% MnO (PDF page 6; compared with the authors' calculated point).
- Riboud and Muan [20]: 1185 °C, 39.9 wt% SiO2, 23.9 wt% MnO (PDF page 7; compared with the authors' calculated point).
- Osborn and Muan [22], point (a): 1470 °C, 56.4 wt% SiO2, 20.0 wt% FeO (PDF page 8).
- Osborn and Muan [22], point (b): 1305 °C, 45.3 wt% SiO2, 45.7 wt% FeO (PDF page 8).

The paper-specific rule authorizes quoted activity data only when a row prints T and composition. It does not say whether these non-activity prose points should be typed as `quoted_attributed` phase-equilibrium observations or kept with the deferred phase-diagram records. That choice also controls the phase and incomplete-composition representation.

## Activity and figures

The paper's only activity series identified so far is the calculated and experimental FeO iso-activity diagram at 1450 °C (Fig. 10, PDF page 7), attributed in the prose/caption to Ban-ya et al. [21]. No numerical activity table or printed curve values were found. Figure values remain deferred under the wave rule. The activity reference state is not stated in the caption.

The remaining figures show modelled phase diagrams, a model schematic, or calculated mixing free energies. Their model output belongs in context; no figure has been digitised.
