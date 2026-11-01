# Takeda et al. (1986), Equilibria between Liquid Copper and Soda Slag

## Acquisition and row count

The printed article contains **zero data tables**. `mineru_table_draft.py convert` produced an index with 0 tables; `mineru_table_qa` found 2 content lists, 0 printed tables, and 6 invented `<details>` blocks. The invented chart blocks were ignored. The compare tool therefore has no table cells to reconcile; its output is stored in `takeda-1986-liquid-copper-soda-slag.compare.txt`.

One prose melting-point observation is admitted: Cu2O, 1503 K. There are 24 context rows. The printed article has zero numerical table rows, and no figures have been digitized. The page render cell grid and source reads are in `takeda-1986-liquid-copper-soda-slag.cells.csv`.

## Full-cell audit

All eight physical PDF pages were rendered with `pdftoppm -scale-to 1800 -png` and checked against `pdftotext -layout` output extracted one physical page at a time. There are no printed table rows or numeric figure points to transcribe. Figure captions 1–11 are preserved in the extract as context.

The initial charge and procedure timings, CO2 analytical temperature and durations, and method limit are carried as method context, not equilibrium observations. Ar gas flow is recorded as the atmosphere; no flow rate, total pressure, or CO2/CO ratio in the gas stream is stated. No pressure condition is inferred. The 101325 Pa value is the printed standard oxygen pressure in Eq. (1), not the experiment's total pressure.

| Printed cell or claim | PDF page | Verification | Disposition |
|---|---:|---|---|
| Cu2O melting point, 1503 K | 2 | Render + pdftotext | Transition-point observation |
| Ar experiment addition, 0.05–1 g arsenic as Cu3As and antimony | 1 | Render + pdftotext | Retained as method context with the wording grouped as printed |
| Oxygen-potential relation, 13.234 E − 7.211; P°O2 = 101325 Pa | 1 | Render + pdftotext | Method context |
| Cu/Cu2O pO2, 1.78 × 10^-4 at 1523 K | 3 | Render + pdftotext | Unit is not stated; typed refused, no atm assumption |
| K = 8.90; K' = 8.66; Oishi-data K = 9.02; aCu = 0.92–1; Eq. (5) ratio = 0.97–1.06 | 3 | Render + pdftotext | Context; ratio is not mislabeled as a CuO0.5 activity coefficient |
| Miscibility-gap prose endpoints: about 1% Cu2O in Na2CO3 melt, 20% Na2X in cuprous oxide liquid, 35–40% Cu2O in Na2O·SiO2 liquid | 4 | Render + pdftotext | Typed refused; the percent basis is not stated and Na2X stays distinct |
| Miscibility-gap closure at about 15% SiO2 and cited binary diagram at 30% SiO2-Cu2O | 4 | Render + pdftotext | First is an author's phase-relation claim; second is attributed to reference 9. Both remain typed refused because the percent basis is unstated |
| CO2/Na2X ≈ 0.4; Na2CO3:Na2O = 4:6 | 2 | Render + pdftotext | Typed refused; retained as the exact printed ratios |
| CuO0.5 activity ranges 0.6–1 and 0.4–0.5 across the two gaps | 5 | Render + pdftotext | `quoted_attributed` author-derived ranges; numerical oxygen-potential inputs are not printed, so they remain typed-refused under d-110 |
| CO2 stoichiometric reference 41.5%; plain carbonate retains slightly less; decomposition is complete at 2Na2O·SiO2 and CO2 is negligible in higher-acidity slag | 6 | Render + pdftotext | The reference and qualitative observations are retained separately; basis is unstated and the actual plotted CO2 values remain figure-only |
| Na2O activity is considerably depressed at 2Na2O·SiO2 | 6 | Render + pdftotext | Qualitative author interpretation; no numerical Na2O activity is printed |
| Acid-oxide carbonate-decomposition ratios, 1:2 and 1:3 | 6 | Render + pdftotext | Stoichiometric interpretation context |
| Intermediate oxide ratios, 1/3, and compound 2Na2O·SiO2 | 7 | Render + pdftotext | The compound is printed literally; a 2/1 ratio is not printed |
| Alumina in Cu2O–Na2X slag: less than 1% | 2 | Render + pdftotext | Context/refusal; percent basis is not stated |
| Al2O3/SiO2 ratio about 0.6 in Cu2O–SiO2 slag | 3 | Render + pdftotext | Ratio context; locator corrected to physical page 3 / printed 610 |
| Alumina values 1–2%, maximum at soda ratio 0.5–0.6 | 7 | Render + pdftotext | Context/refusal; percent basis is not stated |
| Activity and copper-solubility text: soda-ratio peak about 0.67, parallel region below 0.5, increase from 0.3 to 0.75, about 100× Cu content, and log(%Cu)-log(pO2) slope 1/4 | 5 | Render + pdftotext | Typed-refused prose summary/relation; no figure points read |
| Kojo et al. comparison temperature: 1473 K | 5 | Render + pdftotext | Attributed context, reference 18 |
| Yamato et al. comparison temperature: 1473 K | 7 | Render + pdftotext | Attributed context, reference 21 |
| Abstract nNa2O/nSiO2 = 2 | 1 | Render + pdftotext | CuO0.5 activity-coefficient minimum composition; no Na2O activity depression is claimed in the abstract |
| Summary binary addition up to 20 per cent; 1523 K under argon gas flow | 7 | Render + pdftotext | Typed-refused composition context and experiment conditions; the percent basis is not stated |
| Summary CuO0.5 activity-coefficient minimum at nNa2X/nSiO2 = 2 | 8 | Render + pdftotext | Printed ratio retained as context; no numerical minimum value is stated |
| Figure 7 caption, activity coefficient of CuO0.5 versus log pO2 | 6 | Render + pdftotext | Figure and caption are on physical page 6 / printed 613; no plotted values are digitized |

## Schema and evidence decisions

- The composition quantity is available: `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/enums.py::Quantity.EQUILIBRIUM_PHASE_COMPOSITION`, and `simulator/battery/migrate.py::QUANTITY_ALIASES`. The existing `park-2021-vanadium-thermodynamics-thesis.yaml` also stores this kind of composition. The tie-line percentages remain refused because their mass/mole basis is absent; `simulator/battery/enums.py::PHASE_BASIS_TOKENS` accepts a closed basis set and has no unspecified-percent token. No plotted axis was used to guess the basis. `simulator/battery/enums.py::PhaseRole` has melt, slag, glass, metal and related roles, so the gap is the printed basis, not a missing phase role.
- The ratios nCO2/nNa2X and Na2CO3:Na2O remain verbatim and are not converted to mole fractions. There is no generic intra-phase mole-ratio quantity in `simulator/battery/enums.py::Quantity`; `simulator/battery/enums.py::Quantity.DISTRIBUTION_RATIO` and `simulator/battery/migrate.py::TYPE_QUANTITY` describe phase-distribution evidence, not these ratios.
- The printed log(%Cu)-log(pO2) slope of 1/4 is kept as a relation, not converted to an activity value or a Cu-content observation. The quantity candidates are closed by `simulator/battery/enums.py::Quantity` and `simulator/battery/migrate.py::QUANTITY_ALIASES`; neither supplies a generic slope quantity.
- The activity prose spans miscibility-gap regions but does not print a point composition. The ranges are author-derived from oxygen-potential measurements; their numeric inputs are not printed, so they are `quoted_attributed` under d-110, not `measured_reduced`. They remain typed-refused context rather than scalar activities or activity coefficients.
- The paper derives pO2 from oxygen-cell measurements using Equation (1). Since the numeric EMF input is not printed, the result is retained as quoted-attributed context under the unavailable-input rule. Its unit is also not stated, so no pressure conversion or identity pressure is assigned.
- No numerical Na2O activity is printed. The paper describes Na2O activity depression at 2Na2O·SiO2 on physical page 6 / printed 613; there is no Na2O activity to forward for OpenIMCC. CuO0.5 activity is discussed separately.
- Printed page 614 gives the compound formula 2Na2O·SiO2; the extract no longer labels an inferred 2/1 ratio as printed.
- No Cu/O metal composition values are printed in the prose. CuO0.5 activity, liquid-copper activity, and plotted copper solubility are not metal composition measurements.

## Isolated migration and expected store delta

The focused test runs the real migrator on this extract and pins the one admitted transition temperature, its phase and point condition, absence of total pressure/fO2/composition conditions, the typed-refused prose values, all 24 context-row locators, and the 24-row count. It also pins 0 MinerU tables, no unknown observation quantity or phase, and the two migration queue entries: the melting-point method and total pressure are not stated. These are unavailable identity axes, not hard issues or assumptions to fill. The measured pO2 is quoted-attributed in context because the article gives no numeric EMF input to store for the printed cell reduction. No battery-store regeneration is committed. Isolated migration counts: 1 observation, 0 hard issues, 2 unavailable queue entries. Expected delta: one Work, one transition-temperature Observation, 24 carried context rows, and the two explained queue entries.

Verification: isolated migration emitted 1 Work, 1 Observation, 24 context rows, 0 registry issues, 0 hard issues, and 2 unavailable-axis entries. The focused test and the Takeda fidelity subset each passed (1 test); the YAML comma guard passed all 17 tests. Seven mutations covering corrected extract and CSV locators, the evidence class, and the compound label each failed as expected; restoring the source returned the focused test to green. `git diff --check` passed. The review lists no P3 findings. No battery-store regeneration was committed.
