# Hashimoto (1990) extraction audit

## Coverage and method

The source has no text layer and no printed data tables. MinerU and Tesseract
both transcribed the numeric prose values. PDF page renders were checked for
pages 1–3; every numeric value retained in the extract was checked against its
page render and is listed in `hashimoto-1990-forsterite-evaporation-kinetics.numeric-cell-audit.csv`.
The figure captions and labels were checked against page 2. Figure 1 flux curves
were not digitized. The printed-row count is 14 alpha statements plus two
residue-composition statements. Three figure records and one qualitative
residue-stoichiometry record preserve relevant source context without adding
plotted points.

The table draft and QA found zero MinerU tables and zero table rows. There is
therefore no printed table row count to reconcile. The row count above is the
complete set of numeric alpha and residue-composition statements in the paper's
requested scope.

## Transcription and semantics

The authors define the free evaporation coefficient as `alpha_v = J_v/J_e`.
The 14 reported alpha statements are stored under the matching MgO, SiO2, and
Mg2SiO4 species. Solid forsterite coefficients remain their own family. The
previous-effusion coefficient quoted from Firsova and Nesmeyanov and Nagai
retains its printed attribution and its missing temperature. Figure-only rates
remain un-digitized.

The five primary experimental ranges are Hashimoto's own reported results and
are `measured_direct`. The eight alternate-gas recalculations and temperature
extrapolations are `measured_reduced` under the printed relation
`alpha_v = J_v/J_e`. Their numeric Figure 1 inputs are not printed, so each
reduced row locates `raw_inputs: not_printed` to Figure 1 on page 2. The earlier
SiO2 effusion coefficient remains `quoted_attributed` because Hashimoto
attributes it to other authors. The 5 vol% glass amount and approximately 65
wt% SiO2 residue-glass composition are the authors' measured residue results
and are `measured_direct`; the latter retains its printed approximation in the
migrated value. The source's solid-stoichiometric and
molten-almost-stoichiometric statements remain qualitative context because
they contain no numeric composition to store.

The source prints the liquid-SiO2 alternate-species alpha as 1.2 while explicitly
stating that alpha above one is impossible by its definition and may result from
underestimating experimental surface area. The row remains an observation with
the printed value `1.2`, its temperature range, typed-refusal status, and
`measured_reduced` evidence class. Its derivation records the printed ratio and
located unprinted Figure 1 inputs. Its
reason includes the source's caveat: “impossible, according to the definition
of alpha_v”; Hashimoto says the value may reflect underestimation of the
experimental surface area. The existing migrator alpha-domain guard
(`simulator/battery/migrate.py::_quantity_contradiction`) leaves its quantity
unknown and value unavailable. The controller ruled this refusal acceptable for
this extraction; a clearer “printed alpha > 1, author-flagged” reason is a
migrator follow-up, and no migrator or schema changes were made.

The alpha quantity profile requires an identity composition. The paper names
the MgO, SiO2, and Mg2SiO4 materials but does not state that the experimental
samples were pure synthetic or reagent compounds, nor give their origin. Under
the controller ruling, formula-derived composition is permitted only with such
a printed purity/origin statement. The existing migrator therefore leaves
`Identity.composition` unknown; no derived percentages were invented.

## Conditions and phase

The experiment section gives temperature ranges and says the charge cooled
quickly by radiation. It does not say the glass was preserved or altered on
quench, so glass composition rows carry `quench_state: not_stated` located to
page 1. The solid and liquid states are those actually measured after the run;
no in-situ values are printed. The temperature ranges are preserved as ranges,
with no invented midpoint. The alternate SiO2 gas-species coefficients use the
same solid/liquid experimental series; their numeric temperature-range source
is page 2 and is separately located in the extract.

The furnace is described as capable of operating at `1 × 10^-6 mm Hg`; that is
equipment capability, not a run pressure. The source says local vapor pressure
was below one tenth of intrinsic pressure and oxygen fugacity was not
experimentally controllable. Neither statement gives a numerical run total
pressure or fO2. Both conditions are therefore absent from every observation
and remain unknown in identity. No composition percentages are inferred for
the experimental charge.

## Residue volume-fraction schema gap

The printed approximately 5 vol% value is a phase volume fraction. No exact
token represents it: `simulator/battery/enums.py::Quantity` has no phase volume
fraction; `tools/validate_literature_extracts.py::OBSERVATION_TYPES` has no
phase-volume-fraction observation type; `simulator/battery/migrate.py::QUANTITY_ALIASES`
and `simulator/battery/migrate.py::TYPE_QUANTITY` have no exact mapping.
`simulator/battery/enums.py::Quantity.RESIDUE_COMPONENT_COMPOSITION` describes
an oxide component's composition, not phase abundance;
`simulator/battery/enums.py::PHASE_BASIS_TOKENS` has no volume-fraction basis;
`simulator/battery/enums.py::PhaseRole.GLASS` identifies the phase but does not
type its amount. The row is retained as typed refusal with a descriptive
non-token quantity. The 65 wt% SiO2 composition remains a typed
`residue_component_composition` observation for phase `glass`.

## Migration evidence

The isolated migration emits 16 source observation rows, with zero hard
validation issues, two quantity-unknown rows, and zero phase-unknown rows. The
two unavailable values are the 5 vol% typed schema refusal and the 1.2
alpha-domain refusal described above. The solid and liquid alpha ranges reach
the migrator as printed temperature ranges; identity temperature is unknown
because the profile does not accept a range midpoint. The two author
extrapolations reach identity as 1973.15 K and 1673.15 K. Total pressure and
fO2 stay unknown because the source does not print their run values. The
source-origin basis for unknown composition and the alpha 1.2 domain refusal
are recorded in the isolated migration report.

Three alpha-profile identity checks (the profile requires composition,
total_pressure_Pa, fO2_Pa, and temperature_K):

| Observation | Composition | Total pressure | fO2 | Temperature |
| --- | --- | --- | --- | --- |
| MgO solid alpha | Unknown: origin/purity not printed | Unknown: not printed | Unknown: not printed | Unknown: printed range 1873.15–2173.15 K; no midpoint |
| SiO2 solid alpha | Unknown: origin/purity not printed | Unknown: not printed | Unknown: not printed | Unknown: printed range 1833.15–1958.15 K; no midpoint |
| Forsterite extrapolation at 1700 °C | Unknown: origin/purity not printed | Unknown: not printed | Unknown: not printed | 1973.15 K |

The generated observation/battery store was not regenerated or committed. The
expected addition is 16 source rows: 14 alpha statements (one unavailable by
the existing alpha-domain guard), one glass-residue composition row, and one
typed-refused glass volume-fraction row. Figure and qualitative context records
do not add observation rows.

## Verification

- Isolated migration: 16 observations, 0 hard issues, 2 unavailable values,
  2 quantity-unknown rows, and 0 phase-unknown rows. The quantity-unknown IDs
  are the volume-fraction schema gap and the author-flagged alpha 1.2 refusal.
- Focused extraction test: 3 passed in 2.55 seconds. It pins the rendered coefficients,
  evidence classes, approximation flags, and residue composition; deliberate
  coefficient, evidence-class, and approximation mutations fail. It pins the
  migrated 65 wt% value as 65 without manual division and with
  `Value.approximate` true.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed in 59.68 seconds.
- `test_repo_extracts_validate_green`: 1 passed in 250.09 seconds after
  correcting the residue composition fidelity path.
- The focused Hashimoto fidelity matcher selected 4 cases and passed all 4 in
  35.29 seconds.
- The complete `tests/test_literature_extracts.py` file remains
  `UNVERIFIED-local`: two earlier full-file attempts exceeded the 300-second
  cap (128 passed at 329.50 seconds; 158 passed, one excluded, at 328.78
  seconds); a post-fix attempt also reached its 300-second cap without
  completing. The extract-tree validator and Hashimoto fidelity cases were
  then run separately and passed.

## Figure-read candidate

Figure 1 contains measured flux-vs-temperature curves for SiO2, MgO, and
Mg2SiO4. These are figure-read candidates for a later digitization task; no
values were read from the curves in this extraction.
