# Canonical lunar full-yield pyrolysis recipe

## Verdict

The owner-specified no-MRE recipe beats the prior starred hardcoded demo on the
required product set. For a 1000 kg `lunar_mare_low_ti` charge it produces
160.379598 kg of Fe + SiO + Na + K + Mg, versus 134.886206 kg for the old demo:
+25.493392 kg, or +18.8999%. The runner exited 0 with `partial` certification
because the internal-analytical backend is non-authoritative, not because the
run failed or refused.

## Recipe

Canonical bundle: `data/recipes/canonical_lunar_full_yield.yaml`.

- Sequence: C0 -> C0B -> C2A_STAGED -> C3_NA -> C4 -> final C2A.
- Initial pyrolysis uses the staged temperature path; it is not a fixed hold.
- C3 carries 140 kg Na and 56 kg K shuttle inventory, both catalog-bound values.
  Na performs the practical FeO cleanup; the thermodynamic gate still refuses
  K -> FeO in the melt window, so recovered K remains product/recyclable stock.
- C4 remains enabled before the final kinetic-pyrolysis pass. The non-FeO
  dissociation driver first produces kg-scale Mg at hour 32 in C3_NA, 1150 C:
  19.365611 kg Mg. C4 follows at hours 35-41, and continuous C2A is not
  re-enabled until hour 42.
- C5 is absent: zero MRE hours and zero MRE electrical energy.
- Final C2A uses the configured ramp-band midpoints. The catalogued 1150 C
  post-shuttle floor to 1843 C requires 82 h; the 160 h hard cap is the
  conservative slow-band 132 h ramp plus the existing 28 h extraction window.

## Alumina ceiling

The high-temperature bound is **1843 C**, exactly
`furnace_materials.dense_alumina_max.max_service_T_C` in
`data/furnace_materials.yaml`. The catalog describes it as a
formulation-dependent dense-alumina maximum. This is a furnace-material limit,
not a yield-fitted number. The run reaches exactly 1843 C.

## Yield comparison (kg per 1000 kg charge)

The floor is the pre-change starred viewer artifact
`web/report_viewer/sample-run-artifact.json`, run
`sample-fullseq-lunar-197h`: C0 -> C0B -> C2A_STAGED -> C3_NA -> C4 -> C5 ->
C6, peak 1750 C. SiO is integrated from hourly vapor-product flow; other rows
are terminal metal-product yields.

| Required product | Old starred demo (kg) | Canonical no-MRE (kg) | Delta (kg) |
|---|---:|---:|---:|
| Fe | 132.018541 | 85.565570 | -46.452971 |
| SiO | 0.000000017 | 53.473532 | +53.473532 |
| Na | 1.086024 | 1.121350 | +0.035326 |
| K | 0.764904 | 0.853535 | +0.088631 |
| Mg | 1.016737 | 19.365611 | +18.348874 |
| **Required-species total** | **134.886206** | **160.379598** | **+25.493392** |

The new profile is kg-scale for Fe, SiO, combined alkali (1.974885 kg), and Mg.
It trades some Fe recovery from the old MRE-bearing chain for much larger
thermal SiO and Mg recovery, while improving the requested total by 18.8999%.

## Validation

- End-to-end command used the canonical `.venv`, internal-analytical backend,
  400 h horizon, 1000 kg lunar mare low-Ti charge, and the named recipe.
- Completed at hour 201; sequence and ceiling match the recipe.
- Maximum absolute mass-balance residual: `1.0231815394945442e-13%`
  (gate: `5e-12%`).
- Raw evidence: `canonical-run.json` in this report directory.
- Focused campaign/CLI/recipe sweep: 109 passed.
- Optimizer/viewer/artifact sweep: 289 passed.
- Final profile/golden sweep: 63 passed; fresh identity/vocabulary pins: 4 passed.
- Canonical freeze-gate mass-balance cases: 2 passed. The extended full-path
  cumulative transition-closure case passed in 810.71 s with its
  1800-second serial timeout.
- Independent review: `VERDICT: GO`, no P0-P2 findings. One non-blocking P3
  records that CI checks the frozen yield golden while this report retains the
  fresh 201-hour run as manual evidence.

## Demo, test, and FAQ pin

- The lunar optimizer profile seeds `canonical-lunar-full-yield`.
- `web/report_viewer/sample-run-artifact.json` is regenerated from this run,
  includes the recipe snapshot, and is starred by `runs-index.json`.
- `tests/test_canonical_lunar_recipe.py` pins the catalog ceiling, ordering,
  no-MRE path, kg-scale products, legacy floor, and mass-balance gate.
- `docs/faq.md`, `docs/getting-started.md`, and `docs/recipe-playbook.md` use the
  named recipe as the executable demo.

## Golden impact

Golden-affecting changes are intentionally staged without a commit:

- report-viewer sample artifact and runs index;
- optimizer recipe vocabulary plus its payload/identity hashes;
- resolved setpoint/schema recipe-identity hashes;
- canonical recipe/profile and focused yield assertions.

`data/corpus_version.yaml` is unchanged. No corpus-version bump was made.
