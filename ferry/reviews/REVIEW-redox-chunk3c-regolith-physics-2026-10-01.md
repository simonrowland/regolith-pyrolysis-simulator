# REVIEW — redox chunk 3c (publish committed interface diagnostic)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip:** `a9a3cbeeb20928527d2236c65724d04c6afdea93` on `empirical/review-stack2-redox-c5` (tracks `review/stack2-redox`)
- **Range:** `4bfbf9bba..a9a3cbeeb` (one commit)
  - `a9a3cbeeb20928527d2236c65724d04c6afdea93` Publish committed interface diagnostic
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c5-c3c-from-regolith-physics-2026-10-01.md`; chunk 3b P1 fix
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/c5b-report.md`
- **Prior:** chunk 3b **REVISE** on `6ce76a969` — diagnostic held gas while vapour read committed shadow endpoint on fully-ferric nonzero release
- **Date:** 2026-10-01 ~07:55–08:05 ET
- **Mode:** read-only product code; targeted tests only (no full W3).

## Scope

Stamp `_last_oxygen_interface_diagnostic` from the **committed** interface endpoint (and matching flux/root fields on nonzero transfer); consumer asserts diagnostic ≡ committed; `test_fully_ferric_fe2o3_release_publishes_committed_interface_root` must pass.

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| Diagnostic stamped from committed endpoint | `core.py` ~10315–10336: `committed_interface_diagnostic = dict(interface_diagnostic)` then overwrite `interface_pO2_bar` with `committed_interface_pO2_bar`; on nonzero transfer also copy `interface_flux_mol_m2_s`, `interface_root_clamped`, `interface_root_residual_mol_m2_s` from `finite_transfer` before `_apply_oxygen_interface_diagnostic` |
| Finite-transfer root fields available | Same commit adds `interface_root_clamped` / `interface_root_residual_mol_m2_s` into the finite-transfer / last-root payloads (~4400, ~5494) so the diagnostic can mirror the accepted root |
| Consumer asserts diagnostic == committed | `tests/test_headspace_transport.py` `test_internal_equilibrium_uses_interface_for_all_surface_release_consumers`: asserts `sim._last_oxygen_interface_diagnostic['interface_pO2_bar'] == interface_pO2_bar` |
| Fully-ferric discriminator green | `tests/test_mass_balance.py::test_fully_ferric_fe2o3_release_publishes_committed_interface_root` **passed** on tip |

## Attack — same solve? (chunk 3b P1)

**Yes on tip.** Chunk 3b failed with diagnostic gas `1e-6` vs committed `~1.40e-5` because capture preceded handover override / applied the pre-commit diagnostic dict. Chunk 3c overwrites the published diagnostic’s interface (and flux/root fields) from the committed/`finite_transfer` endpoint before apply. Discriminator green; headspace consumer assertion green.

## Targeted tests (VPS, `-o addopts=`)

| Suite | Result |
| --- | --- |
| `test_fully_ferric_fe2o3_release_publishes_committed_interface_root` | **passed** (~2.7 s) |
| `test_internal_equilibrium_uses_interface_for_all_surface_release_consumers` | **passed** |
| `tests/test_headspace_transport.py` (full file, shared with chunk 5 seat) | **25 passed** inside the 106-file batch |

## Mutations

None required on this seat for 3c; the discriminator itself is the proof. Product tree clean at tip (only `.slot-busy` untracked during seat).

## Non-blocking

- Chunk 5 REVISE (M3 key → a_FeO/SulfSat) is independent of this publish fix.
- SiO magnitude / RH03[24] / 130 h freeze-gate timeouts remain known base issues.

## Verdict

VERDICT: LAND a9a3cbeeb20928527d2236c65724d04c6afdea93

— regolith-empirical
