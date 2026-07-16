# t-335 cache_v2 dictionary sweep

## TL;DR

All eight manifest dictionaries were traced to authoritative registries. The
species dictionary now enumerates the formula catalog, vapor-pressure registry,
and explicit volatile products (29 -> 79 labels), including H2O and H2S. The
phase sweep added the subprocess-table label `alloy-solid` found by the required
real-engine smoke (61 -> 62). Evidence class now follows its defining enum
exactly (5 -> 4; removed the unsupported `unknown` label). Backend, tier, and
notice are defined as enums and enumerated rather than repeated tuples.

Every dictionary refusal on point-carried scientific data is contained as one
typed failure row. Phase, species, species-activity, ThermoEngine liquid
endmember, and backend gaps no longer escape the writer. The four descriptive/distillation
flag dictionaries have no grind-source row carriers; their definition checks
remain loud manifest/config validation, as intended by the round-2 descriptive
contract. Cache identity fields are unchanged. No data or golden files changed.

## Dictionary source and delta

| Dictionary | Authoritative source | Before | After | Delta / disposition |
|---|---|---:|---:|---|
| phase | Subprocess Phase-main vocabulary plus the t-332 ThermoEngine `MELTSmodel.get_phase_names()` canonicalization map | 61 | 62 | Added real subprocess label `alloy-solid`; retained all 54 canonical ThermoEngine labels including `solid alloy` |
| species | `simulator.accounting.formulas.load_species_formulas(data/species_catalog.yaml)` plus `data/vapor_pressures.yaml` sections `metals`, `oxide_vapors`, `foulant_vapor`, plus `CACHE_V2_VOLATILE_SPECIES` | 29 | 79 | Added 49 formula-catalog labels and `H2S`; all requested H2O/CO2/CO/CH4/NH3/HCN/SO2/H2S labels covered |
| thermoengine_liquid_endmember | ThermoEngine MELTSv1.0.2 `Liq.endmember_names` registry frozen with version provenance | 15 | 15 | No value delta; added missing point-output validation |
| regime | `simulator.melt_regime.MeltRegime` | 3 | 3 | Enumerated from enum |
| evidence_class | `simulator.fidelity_vocabulary.EvidenceClass` | 5 | 4 | Removed non-enum `unknown` |
| backend | `CacheV2GridBackend`, shared by CLI choices and writer validation | 2 | 2 | Replaced duplicated tuple/branches with enum enumeration |
| tier | `CacheV2ConfidenceTier` | 3 | 3 | Added defining enum; manifest enumerates it |
| notice | `CacheV2Notice` | 1 | 1 | Added defining enum for the no-notice sentinel |

Current descriptive checksums:

| Dictionary | SHA-256 |
|---|---|
| phase | `bd9f2d8d48b5cea91a9a32c1f18965bcc3cce8b924be5de0aa248e9ef3de7a63` |
| species | `fb76dc7f65c98a4aa2260917b39af957f73ab26eff7f32cf96c8f16a01765fa4` |
| thermoengine_liquid_endmember | `5587e989d32c4801f4efc4c008f3898c59a31f61c170bd036419aaf94b4b4e5d` |
| regime | `d52173e48438458268c85808face5108994925bb7cf63c6f3671509db23c83f8` |
| evidence_class | `7e4c505be3110a3030214d5d50d792c0b11737ca0003537559d56daab065176b` |
| backend | `83bf7e579f486ec9f8be78cdc6eee5e35ff7d3370631af3b1cedaad6ab42e38d` |
| tier | `fd0641c51275ee5b9144b18925f13ebc8ac48bb9c6d160829e40c80e74edc1e0` |
| notice | `5acef923ee71bc5e25db9887343298cd16c035a802923db872c03dbf8a417caf` |

## Containment audit

| Surface | Point carrier | Previous behavior | Current behavior / regression |
|---|---|---|---|
| phase | Generic phase lists/maps/instances | Typed containment existed only for phase | `cache_v2_unknown_phase`; one failure row, scientific namespaces cleared, later points continue |
| species | Generic composition, vapor, and per-instance species maps | `ValueError` escaped the writer | `cache_v2_unknown_species`; one failure row, later points continue |
| species activity | Generic activity-coefficient keys | `ValueError` escaped the writer | Classified under species; `cache_v2_unknown_species`; one failure row, later points continue |
| ThermoEngine liquid endmember | `thermoengine.liquid_activities` keys | Not validated | `cache_v2_unknown_thermoengine_liquid_endmember`; one failure row, later points continue |
| regime | None in grind-source row (derived downstream) | Manifest descriptive only | Definition equality tested against `MeltRegime`; no synthetic row carrier added |
| evidence class | None in grind-source row | Manifest descriptive only | Definition equality tested against `EvidenceClass`; no synthetic row carrier added |
| backend | `output.engine_mode` | Unknown point value or known mismatch raised before persistence | Unknown value becomes `cache_v2_unknown_backend`, stored under the configured/inferred legitimate backend, and later points continue; known opposite-backend results and DB blends remain loud invariants |
| tier | None in grind-source row | Manifest descriptive only | Definition equality tested against `CacheV2ConfidenceTier`; no synthetic row carrier added |
| notice | None in grind-source row | Manifest descriptive only | Definition equality tested against `CacheV2Notice`; no synthetic row carrier added |

No literal `cache_v2 unknown ... refused` raise site remains. Point data is
detected before serialization and converted to a bounded (512-character)
failure row. Manifest dictionary construction and immutable-metadata drift still
fail loud because those are configuration/schema errors, not point data.

## Verification

| Gate | Result |
|---|---|
| Focused dictionary/H2O/continuation regressions | 19 passed |
| `tests/test_grid_pregrind.py -n0` | 71 passed, 1 skipped, 8 warnings; installed ThermoEngine probe skipped after its live transport became unavailable |
| `tests/test_wave10_tooling_resilience.py -n0` | 8 passed |
| `tests/test_alphamelts_backend.py '-n0'` | 155 passed, 1 known red, 20 warnings; the known 75 C live alphaMELTS step timed out at 20 seconds |
| Syntax and whitespace | `py_compile` clean; `git diff --check` clean; ruff/black unavailable in main venv |
| Legacy DB regressions | Metadata-less legacy DB remains readable; immediately-prechange cache_v2 descriptive manifest is identity-validated, then atomically migrated to current dictionaries/checksum before append |

Real scratch smoke: three named `mars_basalt` points at 1500/1600/1700 C,
1 bar, fO2=-11, ThermoEngine backend, epoch 2, persisted through
`GridCacheWriter.write_result` to `/private/tmp/t335-mars-h2o-smoke.db`.
All three rows were `ok`; each stored an H2O liquid activity
(`1.2513061597450669e-15`, `1.1395690991607901e-15`,
`1.0481443552539561e-15`), full raw payload, solver status, and the 54-phase
universe. The preliminary subprocess smoke also proved containment in vivo by
surfacing `alloy-solid` as one typed failure while subsequent points continued;
the phase registry was then completed with that authoritative label.

## Files

- `scripts/grid_pregrind_writer.py`
- `scripts/grid_pregrind.py`
- `tests/test_grid_pregrind.py`
- `docs-private/research/2026-07-16-t335-dict-sweep/findings.md`

READY: docs-private/research/2026-07-16-t335-dict-sweep/findings.md
