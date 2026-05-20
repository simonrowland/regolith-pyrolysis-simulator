# SiO Engine Stress-Test Convergence

STATUS: chunk `sio-stresstest`

Scope: test-only per-engine SiO isolation regime. No engine, simulator, wall-deposit, evaporation, equilibrium, or YAML coefficient changes.

## Step Assertions

| step | test file | assertion type | anchors |
| --- | --- | --- | --- |
| VAPOR_PRESSURE | `tests/chemistry/test_sio_step_vapor_pressure.py` | literature + cross-engine + fO2-regime guard | §25-bis SoF2018/MinerU SiO at 1873 K; `tests.chemistry.corpus_fixtures` lunar 12022 proxy at 1900 K; builtin Antoine expected divergence |
| EVAPORATION_FLUX | `tests/chemistry/test_sio_step_evaporation_flux.py` | engine math + YAML alpha surface | `data/vapor_pressures.yaml` SiO alpha 0.04; §25-bis VapoRock pressure anchor 0.3824 Pa |
| CONDENSATION_ROUTE | `tests/chemistry/test_sio_step_condensation.py` | internal placement + Phase 3-bis trend | Chunk A Stage 3 capture requirement; Phase 3-bis wall-temperature trend |
| WALL_DEPOSIT | `tests/chemistry/test_sio_step_wall_deposit.py` | internal H-K sign + Phase 3-bis threshold | Phase 3-bis 1050/1400 C no-suppress threshold; H-K `P_local > P_sat(T_wall)` sign |
| DISPROPORTIONATION | `tests/chemistry/test_sio_step_disproportionation.py` | internal atom balance | Steurer 1985 Eq. 18 semantics: per 1 mol SiO, credit 0.5 mol Si + 0.5 mol SiO2 |
| Chain coherence | `tests/chemistry/test_sio_chain_coherence.py` | internal closure identity | Phase 3-bis evolved SiO invariant and mol-native terminal closure |

## Anchor Values

### VAPOR_PRESSURE

Source anchors:

- §25-bis SoF2018/MinerU Fig. 3: `grid-25-sio:sof2018-mineru@1873K:SiO`, expected `2.820e-1 Pa`, documented observed `3.824e-1 Pa`, status `pass`.
- `tests.chemistry.corpus_fixtures.GRID_25_FEEDSTOCKS`: `lunar_mare_basalt_12022_proxy`.
- `tests.chemistry.corpus_fixtures`: lunar 12022 proxy `(1900.0, "SiO") = 1.5490e-1 Pa`.

Live guard values from this worktree:

| T_K | intrinsic fO2 log10(bar) | VapoRock p(SiO) Pa | builtin Antoine p(SiO) Pa | log10(Antoine/VapoRock) |
| ---: | ---: | ---: | ---: | ---: |
| 1873.0 | -7.955018829486054 | 0.37178538697687874 | 9.363618384979468 | 1.4011513898502117 |
| 1900.0 | -7.748537530135168 | 0.5916880864399161 | 10.234831755836613 | 1.237987883050788 |

Guards:

- VapoRock p(SiO) must remain inside the one-decade documented literature envelope at 1873 K and 1900 K.
- builtin Antoine must remain about 1.4 dex high against VapoRock at 1873 K. This is a relationship guard, not an Antoine authority claim.
- simulator vapor pressure must use intrinsic Kress91/IW-like melt fO2, not the old hard-vacuum floor. The test checks `fO2_log ~= -7.95` at 1873.15 K and rejects drift toward `-9.0`.

### EVAPORATION_FLUX

Source anchors:

- `data/vapor_pressures.yaml`: `oxide_vapors.SiO.evaporation_alpha.value = 0.04`.
- §25-bis pressure anchor: `P_sat(SiO) = 0.3824 Pa`.

Guard values:

| alpha | H-K flux kg/hr |
| ---: | ---: |
| 0.04 | 0.8384291054679249 |
| 0.02 | 0.41921455273396246 |

The test fixes `P_sat`, overhead partial pressure, surface area, stir factor, molar mass, and stoich, then asserts halving alpha halves flux and alpha 0.04 reproduces the anchored H-K flux.

### CONDENSATION_ROUTE And WALL_DEPOSIT

Source anchors:

- Chunk A convergence: C2A SiO must capture at Stage 3 (`stage3_silica_kg > 0`).
- Phase 3-bis convergence: lunar mare no-suppress threshold crosses fast to slow fouling at 1400 C; `sio_wall_deposit_kg = 1.053494e-2` at 1050 C, `1.422324e-3` at 1300 C, and zero at `>=1400 C`.

Live guard values:

| liner T C | sio_evolved_kg | stage3_silica_kg | sio_wall_deposit_kg | terminal_offgas_escape_kg |
| ---: | ---: | ---: | ---: | ---: |
| 1050 | 3.73034175962 | 1.49380768615 | 0.0105349403819 | 0.411875974344 |
| 1300 | 3.73034175962 | 1.49792119286 | 0.00142232367494 | 0.411879523095 |
| 1400 | 3.73034175962 | 1.49856324093 | 0.0 | 0.411880076995 |
| 1500 | 3.73034175962 | 1.49856324093 | 0.0 | 0.411880076995 |

Guards:

- Stage 3 capture remains nonzero for C2A.
- Combined capture (`stage3_silica_kg + sio_wall_deposit_kg`) increases as the wall cools toward the SiO condensation band.
- terminal escape does not increase when the wall is cooled toward the condensation band.
- wall-deposit H-K flux is exactly zero when `P_local <= P_sat(T_wall)` and positive when `P_local > P_sat(T_wall)`.

### DISPROPORTIONATION

Source anchor:

- Steurer 1985 Eq. 18 semantics encoded in `data/vapor_pressures.yaml`: `condensation_products_mol_per_mol_vapor: Si = 0.5`, `SiO2 = 0.5`.

Guard:

- For 1 mol SiO vapor, the condensation-route provider debits 1 mol SiO and credits 0.5 mol Si + 0.5 mol SiO2.
- Computed Si/O atom error is `0.0 %`, below the `5e-12 %` gate.

### Chain Coherence

Source anchors:

- Phase 3-bis invariant: lunar mare no-suppress evolved SiO stays `3.73034175962 kg` across swept wall temperatures.
- AGENTS invariant: mass balance must remain below `5e-12 %`.

Live guard values:

- max closure error in the new chain tests: `7.052814374896685e-13 %`.
- max mass-balance error in the new chain tests: `4.320099833421409e-13 %`.

Identity asserted:

```text
sio_evaporated_mol =
  si_terminal_mol + sio2_terminal_mol + sio_wall_mol + sio_escape_mol
```

## Gate Status

STATUS: focused gate passed

```text
14 passed, 7 warnings
```

STATUS: mass-balance gate passed

```text
5 passed, 7 warnings
```

STATUS: full-suite gate blocked in this worktree

```text
2 failed, 571 passed, 10 skipped, 8 warnings
```

The failing tests are existing corpus parity gates, reproduced directly outside
the new SiO stress-test files:

- `tests/chemistry/test_corpus_anchored_parity.py::test_grid_25_cohort_passes_acceptance_gate`
- `tests/chemistry/test_corpus_anchored_parity.py::test_grid_25_sio_cohort_passes_acceptance_gate`

Observed cause: this worktree has only `docs-private/deep-research/literature/_shared`,
while the parent checkout has the private literature fixture directories. The
SiO-bis grid fixture loader therefore returns zero anchors in this worktree, and
the older grid-25 pass-count gate reproduces its existing residual failure when
VapoRock is available. No new stress-test file is in the failing set.

STATUS: gstack review pass completed

Review finding addressed:

- P1 documentation gap: convergence doc originally recorded focused and
  mass-balance gates only while omitting the full-suite blocker. Fixed in this
  section.

RESULT: discrepancies_found=none

USER-NEED: hydrate the private `docs-private/deep-research/literature/*/benchmark-fixture.yaml` corpus into this worktree or run the final full-suite gate from a checkout that has those fixtures.

BLOCKED: full-suite acceptance in this worktree is blocked by missing private corpus fixtures, not by the new SiO stress-test files.
