# REVIEW — redox chunk 3b (publish oxygen exchange diagnostics)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip:** `6ce76a9693b85924633af517475b75277beb6556` on `empirical/review-stack2-redox-c4-c3b` (tracks `review/stack2-redox`)
- **Range:** `04f9f7f93..6ce76a969` (one commit)
  - `6ce76a969` Publish oxygen exchange diagnostics
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c4-c3b-from-regolith-physics-2026-10-01.md`; design r3 §6 chunk 3 / G8 / E0–E5 consumer contract; follow-up for the seven headspace failures left after chunk 3
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/c4-report.md` (chunk 3b section)
- **Prior LAND:** chunk 3 on `d43b027d1`; chunk 4 LAND on `04f9f7f93` (this seat, separate review)
- **Date:** 2026-10-01 ~00:13–00:28 ET
- **Mode:** read-only for product code after mutations reverted; extracts not edited; no force-push. Targeted tests only (no full W3).

## Scope

Chunk 3 left vapour reading the committed interface (G8) but the exchange path no longer published `_last_oxygen_interface_diagnostic`, and the reader dropped the SSO-R config-presence check. Chunk 3b: capture the exchange diagnostic beside the committed interface; reader does config-presence only (typed refusal restored, no root).

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| Exchange publishes diagnostic + committed interface | `core.py` ~10023–10026 capture `_oxygen_interface_state`; ~10212–10227 apply diagnostic then set `interface_pO2_bar = committed` (gas if \|d\|≤NOOP else `finite_transfer['interface_pO2_bar']`). |
| Vapour reads committed value only | `_interface_pO2_bar` (~6581–6587): `_require_oxygen_exchange_config()` then `return float(reservoir.interface_pO2_bar)` — no root. |
| Typed refusal restored | `test_interface_po2_refuses_missing_sso_r_exchange_config` **passed**. |
| Seven headspace AttributeError / refusal failures fixed | Selected seven **passed** in headspace suite (25/25). |

## Acceptance

| Check | Observed |
| --- | --- |
| Diagnostic published after exchange | Finite two-film / Fe-free / directional tests read `_last_oxygen_interface_diagnostic` — green |
| Reader config refusal | Missing `sso_r` → `OxygenInterfaceConfigurationError` — green |
| Nonzero consumer rails see committed ≠ gas | `test_internal_equilibrium_uses_interface_for_all_surface_release_consumers` — green |
| **Same-solve: diagnostic interface == vapour committed** | **FAILED** — see attack |

## Attack — same solve?

**No.** On a fully-ferric (M3 / ferrous-free) release that commits nonzero transfer:

| Field | Value |
| --- | --- |
| `reservoir.interface_pO2_bar` (vapour / G8) | `1.3968e-05` |
| `shadow_oxygen_transfer['interface_pO2_bar']` | `1.3968e-05` (matches committed) |
| `_last_oxygen_interface_diagnostic['interface_pO2_bar']` | `1e-06` (= transport / gas) |
| `limiting_regime` in diagnostic | `gas_side_ferrous_free_lower_bound` |

Probe reproduced; `tests/test_mass_balance.py::test_fully_ferric_fe2o3_release_publishes_committed_interface_root` **fails** on tip with the same numbers.

**Root cause:** diagnostic is captured via `_oxygen_interface_state` **before** `_redox_handover_transfer_override = transfer_mol` (~10029). For ferrous-free, `_oxygen_interface_state` early-exits when `|_handover_o2_transfer_mol()| ≤ NOOP` (~4681–4687) and publishes **gas**. The committed endpoint still comes from `finite_transfer` (correct for G8). Apply-then-overwrite (~10223–10227) leaves the diagnostic dict holding gas while vapour gets the shadow endpoint.

At base `d43b` the same mass_balance test failed earlier (`redox_buffer_status == ''` — diagnostic not published). Chunk 3b restores publication but stamps the wrong interface into the diagnostic for this regime. Worker’s 153-test four-file run did not include `test_mass_balance.py`.

**P1 fix direction (for physics, not applied here):** set handover override before capturing the diagnostic, or stamp `interface_diagnostic['interface_pO2_bar']` from `committed_interface_pO2_bar` / `finite_transfer` before apply, so the published diagnostic describes the same solve vapour reads.

## Attack — high-ratio Kress limit rewrite

| Case | Old | New |
| --- | --- | --- |
| `target_fe3_fraction=0.999999` | `exhausted` / `gas_side_redox_buffer_exhausted` | no release transfer; interface at gas; `available`; Fe₂O₃ ≤ NOOP |

**Not a physical weakening.** The fixture’s committed ledger is ferrous; a cached high-ferric `melt_intrinsic_fO2_log` cannot mint Fe₂O₃ moles for release. Asserting mole-truth (no release, interface at gas, Fe₂O₃ ≤ NOOP) is the design relation. The old `exhausted` label described a reader-built ratio state that disagreed with the ledger. Retained checks (low-ratio hold, Fe-free vs capacity-exhaustion distinction elsewhere) still cover directional capacity.

## Targeted tests (VPS, `-o addopts=`)

| Suite | Result |
| --- | --- |
| `tests/test_headspace_transport.py` | **25 passed** |
| `tests/test_redox_authority_floor.py` + controlled + overhead | **128 passed** (shared with chunk 4) |
| Seven selected headspace nodes | all green inside the 25 |
| `test_fully_ferric_fe2o3_release_publishes_committed_interface_root` | **FAILED** (P1) |
| Other targeted mass_balance O2-exchange nodes | **passed** (uptake / vacuum / trace paths) |

## Mutations (temporary local edits, reverted)

1. **Suppress diagnostic publication** (skip `_apply_oxygen_interface_diagnostic`).  
   - **Goes red:** finite two-film + Fe-free/capacity tests — `AttributeError: _last_oxygen_interface_diagnostic`.
2. **Suppress reader config check**.  
   - **Goes red:** `test_interface_po2_refuses_missing_sso_r_exchange_config` — DID NOT RAISE.

Restored; product tree clean at tip.

## Non-blocking / out of scope

- SiO magnitude / RH03[24] STUDIO-REGEN unchanged.
- High-ratio rewrite documented above — accepted as mole-truth, not REVISE grounds.
- Consumer test correctly checks committed ≠ gas but does **not** assert diagnostic≡committed; that gap let the P1 through the worker’s selected seven.

## Verdict

VERDICT: REVISE

### P1
1. Published `_last_oxygen_interface_diagnostic['interface_pO2_bar']` must equal the committed interface vapour reads for the **same** exchange, including ferrous-free / fully-ferric nonzero release. Discriminator: `tests/test_mass_balance.py::test_fully_ferric_fe2o3_release_publishes_committed_interface_root` (committed `~1.40e-5` vs diagnostic gas `1e-6`). Fix capture/handover ordering or stamp the diagnostic from the committed/`finite_transfer` endpoint before publish.

### P2
- None blocking beyond P1. Consider adding an explicit headspace/authority assertion that `diagnostic['interface_pO2_bar'] == reservoir.interface_pO2_bar` after every nonzero commit (would have caught this without mass_balance).

— regolith-empirical
