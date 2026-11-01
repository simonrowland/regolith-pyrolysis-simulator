# Takeda et al. (1986), Equilibria between Liquid Copper and Soda Slag

## Acquisition and row count

The printed article contains **zero data tables**. `mineru_table_draft.py convert` produced an index with 0 tables; `mineru_table_qa` found 2 content lists, 0 printed tables, and 6 invented `<details>` blocks. The invented chart blocks were ignored. The compare tool therefore has no table cells to reconcile; its output is stored in `takeda-1986-liquid-copper-soda-slag.compare.txt`.

One prose melting-point observation is admitted: Cu2O, 1503 K. There are 21 context rows. The printed article has zero numerical table rows, and no figures have been digitized. The page render cell grid and source reads are in `takeda-1986-liquid-copper-soda-slag.cells.csv`.

## Full-cell audit

PDF pages 1–4 were rendered at 300 dpi and each numeric prose claim transcribed on those pages was checked against its render. On pages 5–8, numeric prose cells agree between pdftotext and MinerU. There are no printed table rows or numeric figure points to transcribe. Figure captions 1–11 are preserved in the extract as context.

The initial charge and procedure timings, CO2 analytical temperature and durations, and method limit are carried as method context, not equilibrium observations. Ar gas flow is recorded as the atmosphere; no flow rate, total pressure, or CO2/CO ratio in the gas stream is stated. No pressure condition is inferred. The 101325 Pa value is the printed standard oxygen pressure in Eq. (1), not the experiment's total pressure.

| Printed cell or claim | PDF page | Verification | Disposition |
|---|---:|---|---|
| Cu2O melting point, 1503 K | 2 | Render | Transition-point observation |
| Ar experiment addition, 0.05–1 g arsenic as Cu3As and antimony | 1 | Render | Retained as method context with the wording grouped as printed |
| Oxygen-potential relation, 13.234 E − 7.211; P°O2 = 101325 Pa | 1 | Render | Method context |
| Cu/Cu2O pO2, 1.78 × 10^-4 at 1523 K | 3 | Render | Unit is not stated; typed refused, no atm assumption |
| K = 8.90; K' = 8.66; Oishi-data K = 9.02; aCu = 0.92–1; Eq. (5) ratio = 0.97–1.06 | 3 | Render | Context; ratio is not mislabeled as a CuO0.5 activity coefficient |
| Miscibility-gap prose endpoints: about 1% Cu2O in Na2CO3 melt, 20% Na2X in cuprous oxide liquid, 35–40% Cu2O in Na2O·SiO2 liquid | 4 | Render | Typed refused; the percent basis is not stated and Na2X stays distinct |
| Miscibility-gap closure at about 15% SiO2 and cited binary diagram at 30% SiO2-Cu2O | 4 | Render | First is an author's phase-relation claim; second is attributed to reference 9. Both remain typed refused because the percent basis is unstated |
| CO2/Na2X ≈ 0.4; Na2CO3:Na2O = 4:6 | 2 | Render | Typed refused; retained as the exact printed ratios |
| CuO0.5 activity ranges 0.6–1 and 0.4–0.5 across the two gaps | 5 | MinerU + pdftotext | Typed refused; these are approximate ranges across a gap, not point rows |
| CO2 stoichiometric reference 41.5%; plain carbonate retains slightly less; decomposition is complete at 2Na2O·SiO2 and CO2 is negligible in higher-acidity slag | 6 | MinerU + pdftotext | The reference and qualitative observations are retained separately; basis is unstated and the actual plotted CO2 values remain figure-only |
| Acid-oxide carbonate-decomposition ratios, 1:2, 1:3, and 1/3 | 6–7 | MinerU + pdftotext | Stoichiometric interpretation context |
| Alumina values: <1%, ratio about 0.6, 1–2%, maximum at soda ratio 0.5–0.6 | 2 and 7 | Render; MinerU + pdftotext | Context/refusal; percent basis is not stated |
| Activity and copper-solubility text: soda-ratio peak about 0.67, parallel region below 0.5, increase from 0.3 to 0.75, about 100× Cu content, and log(%Cu)-log(pO2) slope 1/4 | 5 | MinerU + pdftotext | Typed-refused prose summary/relation; no figure points read |
| External comparison temperatures: 1473 K | 5 and 7 | MinerU + pdftotext | Attributed context: Kojo et al. ref. 18; Yamato et al. ref. 21 |
| Abstract nNa2O/nSiO2 = 2; summary nNa2X/nSiO2 = 2; binary addition up to 20% | 1 and 8 | Render; MinerU + pdftotext | Kept as separately printed ratios and context |

## Schema and evidence decisions

- The composition quantity is available: `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/enums.py::Quantity.EQUILIBRIUM_PHASE_COMPOSITION`, and `simulator/battery/migrate.py::QUANTITY_ALIASES`. The existing `park-2021-vanadium-thermodynamics-thesis.yaml` also stores this kind of composition. The tie-line percentages remain refused because their mass/mole basis is absent; `simulator/battery/enums.py::PHASE_BASIS_TOKENS` accepts a closed basis set and has no unspecified-percent token. No plotted axis was used to guess the basis. `simulator/battery/enums.py::PhaseRole` has melt, slag, glass, metal and related roles, so the gap is the printed basis, not a missing phase role.
- The ratios nCO2/nNa2X and Na2CO3:Na2O remain verbatim and are not converted to mole fractions. There is no generic intra-phase mole-ratio quantity in `simulator/battery/enums.py::Quantity`; `simulator/battery/enums.py::Quantity.DISTRIBUTION_RATIO` and `simulator/battery/migrate.py::TYPE_QUANTITY` describe phase-distribution evidence, not these ratios.
- The printed log(%Cu)-log(pO2) slope of 1/4 is kept as a relation, not converted to an activity value or a Cu-content observation. The quantity candidates are closed by `simulator/battery/enums.py::Quantity` and `simulator/battery/migrate.py::QUANTITY_ALIASES`; neither supplies a generic slope quantity.
- The activity prose spans miscibility-gap regions but does not print a point composition. It remains a typed-refused context record rather than a scalar activity or activity coefficient.
- The paper derives pO2 from oxygen-cell measurements using Equation (1). Since the numeric EMF input is not printed, the result is retained as quoted-attributed context under the unavailable-input rule. Its unit is also not stated, so no pressure conversion or identity pressure is assigned.
- No numerical Na2O activity is printed. The paper describes Na2O activity depression at 2Na2O·SiO2 but gives no value, so there is no Na2O activity to forward for OpenIMCC. CuO0.5 activity is discussed separately.
- No Cu/O metal composition values are printed in the prose. CuO0.5 activity, liquid-copper activity, and plotted copper solubility are not metal composition measurements.

## Isolated migration and expected store delta

The focused test runs the real migrator on this extract and pins the one admitted transition temperature, its phase and point condition, absence of total pressure/fO2/composition conditions, the typed-refused prose values, and the 21 context-row count. It also pins 0 MinerU tables, no unknown observation quantity or phase, and the two migration queue entries: the melting-point method and total pressure are not stated. These are unavailable identity axes, not hard issues or assumptions to fill. The measured pO2 is quoted-attributed in context because the article gives no numeric EMF input to store for the printed cell reduction. No battery-store regeneration is committed. Isolated migration counts: 1 observation, 0 hard issues, 2 unavailable queue entries. Expected delta: one Work, one transition-temperature Observation, 21 carried context rows, and the two explained queue entries.

Verification: the focused test passed; `tests/test_yaml_flow_scalar_commas.py` passed 17 tests; all 872 cases in `tests/test_literature_extracts.py` passed across five non-fidelity groups (72, 84, 32, 28, 39) and nine fidelity groups (eight groups of 75 and one group of 17). The mutation check changed the stored gap-closure value from 15% to 16%; the focused test failed on the expected value assertion. Restoring 15% returned the focused test to green.
