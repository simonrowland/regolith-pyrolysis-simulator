# N1: one owner of pure-sodium saturation pressure

Seat `worktree/nacap-0545` at `9934e07f4`, plus this change. The study commit stays at `refs/keep/manual/nacap-study` and is not on this branch.

Labels: **measured** is a production-path or test-process number from this seat. **derived** is the published equation evaluated here, independent of the runtime. **literature** is a printed handbook value.

## What changed

Pure-sodium saturation pressure has one owner: `engines/builtin/sodium_saturation.py::sodium_saturation_pressure`. The coefficients live only in `data/vapor_pressures.yaml` `metals.Na.pure_component_saturation` (`equation: browning_potter`).

Liquid, T ≥ 370.98 K: Fink & Leibowitz, ANL/RE-95/2 (1995) §1.2 Eq. (1), Browning–Potter,

```
ln(P/MPa) = 11.9463 − 12633.73/T − 0.4672 ln T
```

Certified on 864–2499 K. Outside that band the same equation is evaluated and flagged. The notice includes Fink's own statement that the extrapolation at 400 K differs by 24% from the LMFBR equation (yaml `extrapolation_caution`).

Solid, T < 370.98 K: the same liquid pressure corrected by the handbook fusion enthalpy,

```
ln(P_s/P_l) = −(ΔH_fus/R) (1/T − 1/T_m)
```

ΔH_fus = 2598 J/mol from the same handbook, Table 1.1-4 (4752 − 2154 at the printed 371 K transition). The solid branch is always flagged. The ratio is 1 at T_m. At 313.15 K it is 0.855949 (**derived**). A non-positive temperature is an error. A non-positive pressure is an error. There is no silent 0 and no Antoine pole. 40 °C returns a flagged solid pressure.

The Rodebush row (`A=7.46077`, `B=1873.728`, `C=−416.372`, 924–1118 K) is gone as a runtime source. The Lamoreaux & Hildenbrand melt coefficients (`A=11.342243`, `B=12140.316409`, `C=−163.701`, `fit_target: standard_reaction_term`) are unchanged.

Callers of the one function, not second formulas:

- Wall saturation, `simulator/condensation.py::_pure_sodium_saturation_pa`, before the legacy Antoine path.
- Battery static-equilibrium scorer, `simulator/battery/score.py::_predict_static_sodium_saturation`.
- Species-rail pure-component pressure, when the saturation block is present.
- Normal-boiling-point inversion, bisection of the same function.

## Melt path

The controller check on the two pure-component curves (**derived** from the printed equations):

| T | Rodebush Antoine | Browning–Potter |
| --- | --- | --- |
| 1156 K | 0.846 bar | 1.02534 bar (102533.73 Pa), certified |
| 1723 K | 10.635 bar | 31.031 bar |
| 1723.15 K |  | 31.050 bar (3.10497 MPa), certified |

Fink at the normal boiling point is within the handbook 1154.7 ± 1.3 K: the 101325 Pa root is 1154.691 K (**derived**). The Antoine curve, used about 600 K above 924–1118 K, is about 2.9× low at 1723 K.

That curve was the wall saturation, not the melt evaporation. The live melt rail is `P = P_ref(L&H) × a_NaO0.5 × (pO2/p_ref)^(−0.25)`. It does not multiply activity by pure-Na P_sat. **Measured** on the production path: evolved Na is identical before and after on all four windows (ratio 1.0). Capture, eta, and the Stage-4 ceiling moved because the wall pressure moved. This is not a >3× melt change, and the two consumers already use different forms, so there is no question for the controller.

## Census

Stage-4 wall is 523.15 K (250 °C). Wall P_sat there went from 8.18249228197721e-11 Pa (Rodebush, **measured** before) to 0.26924186120243376 Pa (Fink, **measured** after; matches the equation). Operating pressure during the C2A window is 10 mbar. `final_p_mbar: 1.0` is the terminal `C3_K` hour after the window. Evolved kg is overhead-gas credits. Captured kg for C2A is the reagent inventory. Credits equal debits on every closed run.

| Window | Evolved kg (same before and after) | Captured before | Captured after | eta before | eta after | Stage-4 ceiling before | Stage-4 ceiling after |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Lunar C2A, 18 h window (21 h with preheat), `lunar_mare_low_ti` | 0.07261040333666878 | 0.014881368655035015 | 0.014087767771334704 | 0.20494816129908228 | 0.19401858582185122 | 0.9999999999844641 | 0.9488807235511703 |
| CI C2A, same window, `ci_carbonaceous_chondrite` | 0.10840574854674005 | 0.01905867919339653 | 0.018187257657636697 | 0.1758087504481298 | 0.1677702326809275 | 0.9999999999871251 | 0.9576355584830746 |
| Mars C2A, same window, `mars_basalt`, additives `C=30 kg` | 0.6121183384541364 | 0.06334842075185707 | 0.06178582854144784 | 0.10349048014447537 | 0.10093771850960025 | 0.9999999999926545 | 0.9758294923965658 |
| Lunar C0, paused at hour 8, campaign `C0B`, 1040 °C, 9 mbar | 1.7276805429693198e-05 | train 1.7276805421922775e-05; offgas ~0; reagent 0 | train 0; offgas 1.72768054296932e-05; reagent 0 | Stage-4 eta 0.9999999995502398 | Stage-4 eta 0 | 0.9999999995503602 | 0 |

All four after-runs: refused hours 0, wall 523.15 K, p_sat 0.26924186120243376 Pa. Lunar C2A ceiling 0.9488807235511703 matches the study's literature mass-weighted ceiling 0.94888. Capture fell slightly. It did not rise. C0 still pauses at `PATH_AB` hour 8, options A / A_staged / B, recommendation `A_staged` (not applied). Stage-4 inlet equals evolved Na. At 9 mbar the inlet partial pressure is below 0.269 Pa, so f_eq is 0 and the sodium that used to sit in the condensation train leaves as offgas. Mass is conserved.

## Battery vapour rail

Bowles & Rosenblum 1965, NASA TN D-2849, `ntrs-19650014783`, 49 static-capsule rows, 1072–2154 K. All 49 are inside 864–2499 K.

| | Before (Rodebush) | After (Browning–Potter) |
| --- | --- | --- |
| max \|residual\| dex | 0.7112241185203761 | 0.05254874809514329 |
| mean residual dex (model − lab) | −0.4150307405947278 | −0.015995853148461105 |
| authority | | certified, notices empty, basis `pure_component_saturation` |
| worst point | 2146 K, −0.7112 dex | 1930 K, −0.0525 dex |

Sample dex, before → after (**measured**): 1072 K −0.0931 → −0.0417; 1329 −0.2034 → −0.0106; 1448 −0.2824 → −0.0046; 1585 −0.3926 → −0.0185; 1839 −0.5609 → −0.0252; 2004 −0.6541 → −0.0270; 2154 −0.7039 → −0.0027. This set is inside the Browning–Potter fit, so the shrink is expected.

`score_store` is unchanged in status: 49/49 `no_band`, eligible false, refusal `decision_rule_missing`, both before and after. The numeric field tracks the new residual. Nothing was repinned.

`data/vapour_rail_validation_pins.yaml` (VapoRock / DeMaria melt activity) was not edited.

## Pins that move

Not repinned. Listed for the controller.

1. `tests/chemistry/extract_store_reproduction_residual_baselines.yaml` key `Na_normal_boiling_point:Psat=1atm`. Pinned `residual_K` 23.584730718736182, band ±0.05 K, status `mismatch`, literature temperature 1156 K. **Measured** this run: actual 1154.6911473917175 K, residual_K −1.30885260828245, Δ from the pin 24.8936 K. `test_adopted_observation_reproduction[Na__janaf-4th__Na_normal_boiling_point]` failed on that band and was not repinned. The sibling `test_normal_boiling_point_inverts_antoine_in_kelvin` passed: it checks the new root against 1156 K. That assertion is the published-equation check, not a rewrite of the baseline file.

2. Reduced-real production key hash did **not** move. `tests/test_reduced_real_pt0_determinism.py::test_control_quantization_default_production_key_is_byte_identical` passed. Hash remains `4cee08747e68f2c46df68ef2fbbe4b5141cd1395c9345d8315daa83305d5a2df`. The parsed-yaml functional digest of `data/vapor_pressures.yaml` did move, `8873215a06dc19e2cc594780e1122c94f216f0fdb0a4beb9bae1c10010aec06d` → `0a3d9e79df8f0cb296e39fed7facff186fd275989cc00a98f23cfc1bdef822fb` (**measured** via `functional_data_yaml_digest` of the parsed root). `_data_digests` in `simulator/reduced_real_determinism.py` is defined and not called, so this digest is not an input of that replay key. No in-repo pin of the old file digest was found.

Retired Antoine locks that tests no longer assert, because the runtime source is gone. These were rewritten to the published equation or dropped. They are not campaign goldens:

- `tests/chemistry/test_antoine_runtime_pins.py`: Na hexes `0x1.160a226c438ffp+14` (1000 K), `0x1.ca9a35618cac1p+16` (1200 K), and log hex `0x1.1004b22108244p+2` removed. The out-of-range tangent case is now potassium's unchanged sidecar at 1200 K (`0x1.67dd2379bb9e0p+2`, pressure `0x1.99cce064d4aa2p+18`), not a new sodium hex.
- Species-rail Na pressure at 1156 K no longer asserts 84612 Pa. It asserts the Browning–Potter value.
- Routing wall record for Na at 523.15 K: band (924, 1118) → (864, 2499), and the warning carries the 24% notice.
- Interpolation source string for Na is `data/vapor_pressures.yaml boiling_point_C 883`. The numeric threshold is still 883 °C + 273.15. Potassium is unchanged.
- Battery static-equilibrium Na cases at 1056 K and 1200 K assert the equation, not the old Antoine totals.
- Dew-point tests hand-evaluate the same equation.

No campaign kilogram golden was found or edited.

## Tests

Unit file `tests/test_sodium_saturation.py`: 21 passed. Pins 400 K and 500 K against Fink's printed table (1.80e-10 MPa and 8.99e-8 MPa), 1156 K against ~1.01 bar and the 101325 Pa root 1154.691 K, continuity at 370.98 K, the solid flag at 313.15 K (fusion and 24%), and a flagged number at 40 °C. Equation values in that file are written out, not copied from the function. **Derived** table check: 400 K → 1.8014893e-10 MPa; 500 K → 8.9894875e-08 MPa.

Other files that read Na saturation, foreground, one file at a time:

| File | Result |
| --- | --- |
| `tests/test_alkali_wall_dewpoint.py` | 19 passed |
| `tests/test_interpolation_uncertainty.py` | 12 passed |
| `tests/chemistry/test_antoine_runtime_pins.py` | 17 passed |
| `tests/test_volatile_properties.py` | 58 passed |
| `tests/test_routing_purity.py` | 22 passed |
| `tests/test_physics_ground_truth.py` | 80 passed |
| `tests/chemistry/test_species_rail_differential.py` | 41 passed |
| `tests/test_vr11_instrumentation.py` | 65 passed, 1 skipped |
| `tests/test_doc_anchors.py::test_doc_impl_anchors_resolve` | passed |
| `tests/test_reduced_real_pt0_determinism.py` production-key test | passed, hash unchanged |
| `tests/chemistry/test_builtin_vapor_pressure_provider.py -k sodium` | 3 passed, 161 deselected |
| `tests/battery/test_score.py` Na static-equilibrium tests | 3 passed |
| `tests/test_condensation_extrapolation_honesty.py` cold Na, 417 K, Mg 115 K | 3 passed |
| same file, RH03 hours=2 | passed. Na is flagged, not refused. The coating pareto leaves status `unavailable` when Na is absent from the flux series, and still carries the extrapolation record |
| `tests/chemistry/test_extract_store_reproduction.py -k "inverts_antoine_in_kelvin or Na_normal_boiling_point"` | 1 passed, 1 failed, 2489 deselected, 742 s. The pass is the published-root check. The failure is the unpinned baseline move above |

`tests/chemistry/test_builtin_vapor_pressure_provider.py` as a whole reached 87% with no failure mark, then the parent blocked in `lock_PyThread_acquire_lock` while spawned workers sat in `poll`/`read`. The sodium tests are before that point (3 passed in isolation). The hang is on a later Mn/Si provider test. The process was stopped. `tests/battery/test_score.py` as a whole reached 52% inside a 900 s cap with no failure mark; the Na static-equilibrium cases were then run on their own and passed (3). The full extract-store file reached one failure near 57% and was cut at 92% by a 900 s cap before the summary; the two Na boiling nodes were rerun and are the row above. RH03 hours=24 uses the same sodium assertions as hours=2. Two attempts, the second with nothing else from this seat running, sat idle immediately: the parent waited on workers blocked in `poll`, with no MAGEMin progress, and both were stopped. Hours=2 is the run that executed those assertions.

## Stale citations left in place

Not runtime owners. Left because no test required them and they are outside the physics change:

- `docs/lab-validation-whitepaper.md` lines 92 and 388 still name the Rodebush triple `(7.460770, 1873.728, −416.372)` as the selected Na runtime.
- `docs/references/references.yaml` REF-041 coefficient note still describes the Na pure-component Antoine row.
- `data/vapour_rail_calibration.yaml` anchor `na_nist_webbook_pure` (`may_certify: false`) still cites Rodebush. It is not an evaluator.
- `tests/test_vaporock_backend.py` comment still says Na's range is [924, 1118] K. The assertion is SiO.
- Historical catalog snapshots `tests/fixtures/catalog-pins/vapor_pressures.*.yaml`.

## Out of scope

Collector gas temperature, the nucleation flag, Stefan flow, the K-trap species gate, thermal-window pressure plumbing, and potassium's own extrapolated P_sat do not block this change. They were not touched.
