# CrO2(g) speciation implementation - 2026-05-18

## Directive

Add CrO2(g) as a distinct gas species end-to-end and add a dedicated Cr
condenser stage so chromium is collected separately as a physical product
stream.

## Source convergence

VapoRock coverage is present. The adapter species set now exposes `CrO2`, and
the upstream JANAF0 row is:

`CrO2(g),g,2,Cr,1,2,1100,6000,57.85928,0.190282,-0.038347,0.002643,-2.578022,-99.71476,328.5921,-75.312,JANAF`

Fallback Antoine-like fit, retained in `data/vapor_pressures.yaml`:

`log10(P_Pa) = 12.9245114 - 23732.9593 / T_K + 0.5 log10(a_Cr2O3) + 0.25 log10(pO2_bar)`

Fit domain: 1500-2200 K. VapoRock/JANAF0 thermodynamic row domain:
1100-6000 K.

Sossi-Moon fixture used:
`docs-private/deep-research/literature/_isotope-theory/sossi-2018-moon-volatile-loss-cr/benchmark-fixture.yaml`.

Relevant fixture statements:

- `CrO2(g)` is the predominant Cr-bearing gas species above IW buffer.
- Significant `CrO2(g)` occurs above 1500 K.
- `CrO2_g` beta coefficient is 0.57.
- The fixture is isotope/equilibrium evidence, not a Hertz-Knudsen coefficient
  source.

## Engineering path

Chosen path: Option A, high-temperature Cr2O3 harvest.

Rationale:

- The available evidence supports `CrO2(g)` as a high-temperature Cr-bearing
  vapor under lunar/IW-relevant conditions.
- The implemented physical product is stable chromia:
  `2 CrO2(g) -> Cr2O3(s) + 0.5 O2(g)`.
- No source found here supports making metastable mid-temperature `CrO2(s)` the
  default product stream.

Chosen condenser range: 1100-1300 C, setpoint 1250 C.

## Schema and routing

Data additions:

- `data/species_catalog.yaml`: `CrO2` with atoms `{Cr: 1, O: 2}`.
- `data/vapor_pressures.yaml`: `oxide_vapors.CrO2`, parent oxide `Cr2O3`,
  VapoRock/JANAF0 row metadata, fallback fit, pO2 exponent, activity exponent,
  valid range, and condensation products.
- `data/setpoints.yaml`: `condensation_temperatures_C.CrO2 = 1250` and a new
  Cr Oxide Harvester stage.

Ledger additions:

- New terminal account:
  `terminal.chromium_condensed_oxide_stored`
- Allowed terminal species:
  `Cr2O3`

Train topology:

0. Hot Duct
1. Fe Condenser
2. Cr Oxide Harvester, 1100-1300 C, target species `Cr`, `CrO2`
3. SiO Zone
4. Alkali/Mg Cyclone
5. Vortex Dust Filter
6. Turbine-Compressor
7. Turbine Outlet Monitor

The Cr stage accepts both legacy `Cr` vapor and new `CrO2` vapor so chromium no
longer bypasses the dedicated stage into the SiO zone. `CrO2` condensation
credits chromia to the terminal chromium account and returns the O2 coproduct to
`process.overhead_gas`.

## End-to-end trace

Trace harness:

- Feedstock: `lunar_mare_low_ti`
- Campaign: `C2A`
- Duration: 12 h
- Hot anchor: 1500 C start, matching the existing hot-window test convention.
- Explicit pO2 buffer: 1.0 kg O2 added to both ledger and run mass input so
  `CrO2` oxygen uptake is ledger-closed.

Trace result:

- Initial Cr: 47.406067861421796 mol
- Final Cr: 47.406067861421796 mol
- First-hour `CrO2` flux: 0.004259138509587783 kg/h
- Terminal chromia after 12 h: 0.05351278316558326 kg `Cr2O3`
- Terminal offgas `CrO2` after 12 h: 1.0464003095934315e-05 kg
- Stage 2 collected: 1.0586977312716788 kg `Cr` and
  0.05351278316558326 kg `Cr2O3`
- Max mass-balance closure: 1.0221593801144299e-13 %

Test anchor:
`tests/chemistry/test_chromium_cro2_provider_behavior.py::test_lunar_mare_c2a_hot_cro2_trace_closes_chromium_atoms`

## Test gates

Current focused gates:

- `pytest tests/test_mass_balance.py -q`: 3 passed.
- `pytest tests/chemistry/test_writer_purity.py -q`: 2 passed.
- `pytest tests/chemistry/test_corpus_anchored_parity.py -q`: 204 passed,
  96 skipped.
- `pytest tests/test_overhead_accounting.py -q`: 23 passed.
- `pytest tests/test_runner_smoke.py -q`: 15 passed after updating the
  intentional `lunar_mare_low_ti_C0_24h` golden drift.
- `pytest tests/chemistry/test_chromium_cro2_provider_behavior.py -q`: 4
  passed.
- `pytest tests/test_feedstock_inventory.py -q`: 39 passed.

- `pytest tests/ -q`: 738 passed, 96 skipped.

## Open findings

- Sossi-Moon reproduction: fixture supports CrO2(g) speciation and isotope
  beta factors, but it does not provide a direct HK evaporation-rate fit.
- CrO/CrO3 status: VapoRock data contains additional chromium vapor species,
  but this chunk intentionally adds only `CrO2`; no dedicated CrO/CrO3 route
  was added.
- Section 25 impact: the §25 corpus parity gate remains the regression anchor;
  the focused corpus gate passed after this change.
- The standard runner starts `C2A` from ambient, so a literal 12 h C2A runner
  reaches only low temperature. The CrO2 end-to-end trace uses the same hot
  anchor convention already used by the headspace pO2 e2e tests.
