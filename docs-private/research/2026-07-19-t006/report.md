# t-006 CF-10 tail: NiO graph row and minor MRE residuals

## TL;DR

- Added the missing phase-correct Ni/NiO Ellingham graph row; NiO is now graph-authoritative instead of a flagged `0.39 V` fallback.
- Source-grid fits use Mah & Pankratz USBM Bulletin 668 data, not model-output fitting.
- Replaced one stale 130-hour trajectory-dependent refusal test with deterministic typed-refusal propagation, rollback, and atom/mass-balance coverage.
- Canonical requested gate: **42 passed, 37 deselected**; companion MRE gate: **60 passed**.
- Golden-affecting files are staged only; no commit, corpus-version bump, or golden regeneration.

## Implemented

### 1. NiO Ellingham / MRE graph rung

`simulator/chemistry/ellingham_thermo.py` now contains two Ni oxidation segments:

- `1100 <= T < 1728 K`: `2 Ni(s) + O2 -> 2 NiO(s)`
- `1728 <= T <= 2000 K`: `2 Ni(l) + O2 -> 2 NiO(s)`

The 1728 K split is the source's nickel melting transition. The source reports
`DeltaGf` in kcal per mol NiO. The code comment records the normalization used by
the shared Ellingham graph:

```text
DeltaG[kJ/mol O2] = 2 mol NiO/mol O2 * 4.184 kJ/kcal * DeltaGf[kcal/mol NiO]
```

Least-squares fits to the tabulated source rows produce:

| Rail | DeltaH, kJ/mol O2 | DeltaS, kJ/mol/K/mol O2 | max source-grid residual |
|---|---:|---:|---:|
| Ni(s), 1100-1728 K | -465.852324599 | -0.167750743402 | 0.174 kJ/mol O2 |
| Ni(l), 1728-2000 K | -495.556320989 | -0.184940556128 | 0.074 kJ/mol O2 |

Both fits are constrained through the identical tabulated 1728 K value, so
the solid/liquid rails are exactly continuous at the phase boundary.

`simulator/mre_ladder.py` maps `NiO -> Ni`, so the canonical MRE reference at
1873.15 K is now graph-derived, authoritative, liquid-Ni-basis, and
`0.386418627 V`. The old static anchor remains `0.39 V` only as the generic
fallback literal.

Focused tests pin all 11 tabulated 1100–2000 K rows, both metal phases, graph authority,
voltage, provenance sidecars, published/fallback ladder construction, and
import-cycle coverage.

### 2. Cheap MRE-fidelity residual

The canonical command initially exposed a pre-existing stale test:
`tests/test_electrolysis.py` expected a mutable 130-hour full-run trajectory to
reach a Cr2O3/SiO2 overlap at exactly hour 43. Current HEAD completes that run,
while deterministic provider tests already cover the real multi-oxide detector.

The end-to-end test now injects the typed refusal at the hourly transaction seam.
It continues to validate `RunExecutor` refusal status and diagnostic propagation,
terminal-refusal rollback of an injected material-state mutation, no poisoned hour, zero committed snapshots, and
`atom_ledger.assert_balanced()`. Runtime fell from about 293 seconds to the
single-hour envelope used by the canonical gate.

## Grounding references

- `REF-056`: A.D. Mah and L.B. Pankratz, *Thermodynamic Properties of Nickel and
  Its Inorganic Compounds*, U.S. Bureau of Mines Bulletin 668 (1976), NiO(s)
  table p. 28, rows 1100-2000 K. Public scan:
  <https://stacks.cdc.gov/view/cdc/220136/cdc_220136_DS1.pdf>
- B.S. Hemingway (1990), *Thermodynamic properties for bunsenite, NiO,
  magnetite, Fe3O4, and hematite, Fe2O3, with comments on selected oxygen buffer
  reactions*, *American Mineralogist* 75, 781-790. This remains the independent
  lower-temperature/background NiO reference; it was not used to fit the new
  1728-2000 K rail: <https://msaweb.org/AmMin/AM75/AM75_781.pdf>

No coefficient was selected to preserve simulator or golden outputs.

NIST-JANAF's public nickel index contains elemental Ni tables but no condensed
NiO thermochemical table. The implementation therefore uses the primary,
phase-resolved U.S. Bureau of Mines NiO table recommended by the project's
existing source-harvest notes, and records that non-JANAF exception explicitly
as `REF-056`; NIST elemental data alone cannot derive the NiO formation row.

## Deferred to 0.7 by owner direction

- full `DECOMP_VOLTAGES` / dGf(T) ladder work beyond this NiO row;
- within-tick redox decoupling changes;
- Mg Antoine certified-range work;
- primary-source replacement of the reconstructed high-temperature MnO row;
- Fe2O3 full-reduction naming/thermodynamics beyond its existing reference-only disposition.

## Verification

Requested canonical command:

```text
.venv/bin/python3 -m pytest tests/test_physics_ground_truth.py tests/test_electrolysis.py -k "ni or nio or mre or ellingham" -n0 -q
42 passed, 37 deselected in 9.32s
```

Companion MRE ladder/import gate:

```text
.venv/bin/python3 -m pytest tests/test_mre_ladder.py tests/test_mre_table_coverage.py tests/test_mre_voltage_sequence_yaml.py tests/test_ellingham_import_no_cycle.py -k "ni or nio or mre or ellingham or import_order" -n0 -q
60 passed in 10.45s
```

Additional gates: Ni phase-continuity/source tests `7 passed, 7 deselected`;
reference registry and generated HTML `9 passed`.

Mass/atom balance is explicitly asserted after typed-refusal rollback in the
canonical `tests/test_electrolysis.py` case.

## Golden impact and staging posture

This is golden-affecting: the NiO canonical rung moves from the non-authoritative
static `0.390000000 V` fallback to an authoritative graph value of
`0.386418627 V` at 1873.15 K (delta `-0.003581373 V`), and source/provenance
fingerprints change. No golden fixture or `data/corpus_version.yaml` was changed.
All task files are staged for controller-side batching/rebaseline; `.venv` remains
an untracked worktree symlink and is not staged.
