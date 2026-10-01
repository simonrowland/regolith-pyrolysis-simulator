# REVIEW — stack2 merge + chunk 5d (frontier of record)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip under seat:** `c5674a2d248cd7f915fb272d40e242355dfc574d` on `review/stack2-redox`
- **Range:** `a9a3cbeeb20928527d2236c65724d04c6afdea93..c5674a2d248cd7f915fb272d40e242355dfc574d` (four commits)
  - `a0ba20320d0575b5303483a21f66cc9146453b60` Merge headspace transport line **(A)**
  - `a14ee653a1e015d19e83c85af4ef24848a07550d` Restrict M3 redox key consumers **(B / chunk 5d)**
  - `71986aca2324345fc23e76006f59b5886b58fe9d` Preserve staged redox event precedence **(C)**
  - `c5674a2d248cd7f915fb272d40e242355dfc574d` Update transport regression checks **(D)**
- **Parents of merge A:** `a9a3cbeeb` (redox) + `5aebdc8bb` (headspace r12c LAND)
- **REQ:** `/workspace/ferry-inbox/REQ-stack2-merge-5d-from-regolith-physics-2026-10-01.md`
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/merge-report.md`
- **Prior:** chunk 5 REVISE `REVIEW-redox-chunk5-regolith-physics-2026-10-01.md` (P1 M3 ±30 → a_FeO/SulfSat); headspace r12c LAND `5aebdc8bb`
- **Date:** 2026-10-01 ~14:35–15:10 ET
- **Mode:** read-only product code; extracts not edited; no force-push; mailbox branch only. Targeted tests only (no full W3).

## Known held (not merge regressions)

runner goldens; SiO yield pins/goldens and fixed-pO2 SiO magnitude (studio regen); three `test_split_path_end_state_matches_pre_flip_account_balances`; two MAGEMin 1800 s timeouts; 130 h freeze-gate timeouts (CPU; chunk 7); `test_core_does_not_consume_non_authoritative_vaporock_pressures` (fixture lacks oxygen reservoir).

---

## A — MERGE `a0ba20320d0575b5303483a21f66cc9146453b60`

### Conflict resolution

Only textual conflict: import block in `tests/test_headspace_transport.py` — both sides’ imports combined (matches worker). Bleed path / redox-owned source changes merged without textual conflicts.

### Assertion preservation (four named files)

| File | Tip vs parents | Notes |
| --- | --- | --- |
| `tests/test_headspace_transport.py` | tip 32 tests / 1451 lines (redox 24 / hs 30) | Finite two-film + sonic/Poiseuille + M4 `gas_side_fe_saturation_bound` + mole-log inverse kept. Headspace `…uses_kress_inverse` renamed/adapted to `…uses_ledger_inverse` + redox mole-log twin. Fe-free vs exhausted still distinguished via `redox_buffer_status` (`no_fe_redox_buffer` vs `exhausted`); limiting_regime string unified to redox `gas_side_fe_saturation_bound` (intentional, not a lost distinction). |
| `tests/test_condensation_extrapolation_honesty.py` | tip 39 / 1685 lines | Physical Kn/pressure RH03 path `{21,24}` retained; redox exchange/regime/speciation notices retained. |
| `tests/test_optimizer_evalspec.py` | tip 109 / 3925 lines | Exact 1480 °C C2B endpoint **and** computed ramp both present. |
| `tests/test_b598_melt_redox.py` | tip 242 lines | Redox absence/authority codes + bound-separate asserts; headspace bounds adapted to those codes (no `gas_interface_controlled` / −9 equality). |

No parent-only test **function** missing from tip (signature-only false positives in a naive name strip).

### Semantic tick order (exchange / flush / bleed / interface publish)

In `PyrolysisSimulator.step` (~17987–18349):

1. Early: `_apply_oxygen_reservoir_exchange()` — commits finite interface root onto `reservoir.interface_pO2_bar` + `_last_oxygen_interface_diagnostic`.
2. Mid: native-Fe / respeciation / `_get_equilibrium` / evaporation (vapor dispatch reads `_vapor_pressure_dispatch_pO2_bar()` → committed interface).
3. Late finite-headspace: `_set_headspace_transport_source_rates` → `_flush_evaporative_o2_buffer_to_headspace` → `_dispatch_overhead_bleed` → `_refresh_oxygen_reservoir_transport_pO2_for_vapor`.

Exchange publish precedes flush/bleed. SiO release provenance is captured at vapor dispatch (pre-bleed), not from the post-bleed transport refresh — consistent with design r3 / commit C’s test fix. No semantic conflict that weakens either line’s contract.

### RH03[24] derived-hours note (VPS)

Tip failed `{21,24} <= derived_hours` with derived `{22,23,24}` **after** physical kn_trace assert `{21,24}` passed. Parent replay on VPS (no VapoRock): redox `a9a3cbeeb` also fails (`{21,23}`); headspace `5aebdc8bb` passes. **Not a both-parents-pass merge-only defect.** Treat as known VPS/no-VapoRock notice-hour sensitivity; physical pressure/Kn contract holds on tip.

### Targeted tests (A)

| Suite | Result |
| --- | --- |
| `tests/test_headspace_transport.py` + `tests/test_b598_melt_redox.py` | **37 passed** |
| RH03 focused (`[2]`, `[24]`, +1 wall) | **2 passed, 1 failed** (derived-hours; see above) |
| C2B profile / 1480 focused | **3 passed** |

VERDICT: LAND a0ba20320d0575b5303483a21f66cc9146453b60

---

## B — CHUNK 5d `a14ee653a1e015d19e83c85af4ef24848a07550d` (+ chunk 5 `4bfbf9bba`)

### P1 fix (prior REVISE)

| Consumer | Tip evidence |
| --- | --- |
| Speciation key still ±30 on M3 | Probe `_fully_ferric_sim`: key `30.0`, authority `bound`, regime `ferrous_free_lower_bound` |
| a_FeO unavailable naming regime | `equilibrium.py` forces absent equality on M3; `core` vapor path sets `intrinsic_fO2_log=None` and diagnostic `a_FeO_calphad.status=unavailable` reason `ferrous_free_lower_bound_has_no_melt_equality`. Probe confirmed. |
| SulfSat not evaluated with key | `_attach_post_equilibrium_sulfsat` early-returns `calibration_status=not_evaluated`, `not_evaluated_reason=ferrous_free_lower_bound` **before** `compute_sulfur_saturation`. Probe: gate never called. |
| Freeze gate / PT-0 still use key | `evaporation.py` `_freeze_gate_curve` / `_freeze_gate_redox_key_fO2_log`; `reduced_real_determinism._authoritative_melt_fO2_log`; core freeze-gate path ~8019. |
| M4 still usable | Same test: M4 a_FeO `ok`; SulfSat still receives bound `fO2_log`. |

`rg _melt_redox_speciation_key`: release/exchange/native-Fe still off the key; M3 sentinel no longer drives activity/SulfSat.

### Targeted tests (B)

| Suite | Result |
| --- | --- |
| `test_ferrous_free_bound_is_not_used_for_activity_or_sulfsat` + ferrous_free/speciation/freeze/sulfsat/authority filter on `test_redox_authority_floor.py` | **52 passed** |

With this, **chunk 5 (`4bfbf9bba` + `a14ee653a`) is LAND** — prior P1 closed; no new P1/P2.

VERDICT: LAND a14ee653a1e015d19e83c85af4ef24848a07550d

---

## C — `71986aca2324345fc23e76006f59b5886b58fe9d`

### (1) Native-Fe event precedence

At absent equality (~12039–12057): if `staged_na_shuttle_event` is set, event stays `deferred_for_staged_na_shuttle` / `staged_path_reserves_feo_for_na_shuttle` and appends `; secondary: {regime} has no melt equality`. Staged path still sets `native_extent={}` and never runs the FeO→Fe saturation commit (~11812–11824). **Ordering right; native-Fe extent path unchanged** (still deferred). Worker hour-7 shuttle debit/credit `86.995…` mol with `2272…` mol FeO remaining accepted as campaign evidence; e2e asserts reason shape including secondary clause.

### (2) MRE dispatch None

`test_step_mre_dispatch_uses_selected_runtime_max_voltage`: `melt_fO2_log` / `fO2_log` expect `None` where ledger equality is absent (no −9).

### (3) SiO provenance instant

`test_po2_wall_sweep_mode_uses_interface_pressure_for_sio_release` compares release-time `provenance["pO2_bar"]` to `_last_vapor_pressure_diagnostic["interface_pO2_bar"]` (dispatch capture), not post-bleed `reservoir.interface_pO2_bar`. Code: vapor path reads `_vapor_pressure_dispatch_pO2_bar()` into control_inputs/`diagnostic`; equilibrium diagnostics also stamp `interface_pO2_bar`. Probe: diag interface == dispatch helper. **Right instant per design r3; pressure recorded is the one used.**

### Targeted tests (C)

| Suite | Result |
| --- | --- |
| MRE dispatch + SiO wall-sweep + PN2 native-Fe e2e | **3 passed** |

VERDICT: LAND 71986aca2324345fc23e76006f59b5886b58fe9d

---

## D — `c5674a2d248cd7f915fb272d40e242355dfc574d`

### (1) W7 Fe/Na capacity ratio

`test_w7_pipe_conductance_uses_live_evap_flux_species` now expects `min(Poiseuille, sonic)` per species at fixture P/downstream — matches production `_duct_mass_flow_capacity_kg_s`. Ratio ≈ `1.558…` (not uncapped M_Fe/M_Na `2.429…`). Live path asserts both capacities and the dual-law ratio.

### (2) Alkali identity + overhead holdup

`PRODUCT_LEDGER_ACCOUNTS` includes `process.overhead_gas`. Corrected closure:

**gross condensate + overhead holdup = product ledger + recycled condensate − terminal offgas**

Because PL already projects live headspace, omitting holdup on the left falsely under-counts by ~OH (worker: discrepancy matched OH to fp precision). Adding OH **does not hide a leak** — it names the inventory PL already carries. Transport saturation stayed negligible in worker replay (sonic choke not the alkali delta).

### Targeted tests (D)

| Suite | Result |
| --- | --- |
| W7 live path + alkali series identity | **2 passed** |

VERDICT: LAND c5674a2d248cd7f915fb272d40e242355dfc574d

---

## Summary

| Item | Commit | Verdict |
| --- | --- | --- |
| A merge | `a0ba20320d0575b5303483a21f66cc9146453b60` | **LAND** |
| B chunk 5d (⇒ chunk 5 LAND) | `a14ee653a1e015d19e83c85af4ef24848a07550d` | **LAND** |
| C precedence | `71986aca2324345fc23e76006f59b5886b58fe9d` | **LAND** |
| D transport checks | `c5674a2d248cd7f915fb272d40e242355dfc574d` | **LAND** |

Tip under review: `c5674a2d248cd7f915fb272d40e242355dfc574d`.

— regolith-empirical
