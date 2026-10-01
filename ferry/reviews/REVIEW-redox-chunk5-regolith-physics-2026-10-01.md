# REVIEW — redox chunk 5 (bounds and absence / speciation-key accessor)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip under seat:** `a9a3cbeeb20928527d2236c65724d04c6afdea93` on `empirical/review-stack2-redox-c5` (tracks `review/stack2-redox`)
- **Range:** `6ce76a969..4bfbf9bba` (one commit)
  - `4bfbf9bba1a12546eb1f31c7ed0075c25f2e881f` Update melt redox domain handling
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c5-c3c-from-regolith-physics-2026-10-01.md`; design r3 §6 chunk 5 / option B; AMEND review `REVIEW-redox-r3-chunk5-amend-2026-10-01.md` (MINOR; P2 M3 key scope)
- **Worker reports:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/c5-report.md` (first worker BLOCKED); `c5b-report.md` (COMPLETE on tip)
- **Prior LAND:** chunk 4 on `04f9f7f93`; chunk 3b REVISE on `6ce76a969` (P1 fixed by chunk 3c, separate review)
- **Date:** 2026-10-01 ~07:55–08:05 ET
- **Mode:** read-only for product code (no mutation left applied); extracts not edited; no force-push. Targeted tests only (no full W3).

## Scope

Chunk 5 (option B + AMEND): M1/M3/M4 store **absent equality** with the bound separate; remove −9 fallback on the equality reader; introduce one flagged `_melt_redox_speciation_key` for liquidus / freeze gate / PT-0 / activity / equilibrium / SulfSat; release rails, exchange, native-Fe, and respeciation must never read the key.

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| Equality absent M1/M3/M4 | `_melt_fO2_from_ledger` returns `None` + `_redox_domain_record(..., fO2_log_lower_bound=...)` for `fe_saturation_bound` / `ferrous_free_lower_bound`; `_current_melt_redox_fO2_log` returns `None` when `_melt_redox_equality_is_absent()` |
| Bound stored separately | Domain record carries `fO2_log_lower_bound`; derived equality scalar stays `None`; runner/panel render bound separately |
| No −9 fallback on equality | `_current_melt_redox_fO2_log` has no −9 path; extraction/MRE pass `None` explicitly (“Do not pass 0 or −9”) |
| Speciation-key accessor | `core.py` `_melt_redox_speciation_key` (~6682): equality → authority `equality`; M4 bound + residual flag; M3 **clamped** via `_freeze_gate_liquidus_fO2_log` (±30); else interface `no_couple` |
| Release / exchange / native-Fe / respeciation never read key | `rg` call sites of `_melt_redox_speciation_key` are liquidus/freeze (`evaporation.py`, `core` gate), PT-0 (`reduced_real_determinism.py`), equilibrium/vapor activity (`equilibrium.py`, `core` vapor dispatch), SulfSat (`core` attach). Interface / `_current_melt_redox_fO2_log` used for exchange drive and release. Native-Fe / respeciation skip on absent equality with regime name |

## Attack results

### (1) Accessor partition

**Mostly holds for pressure / driving-force rails; fails the AMEND M3 consumer contract for a_FeO / SulfSat (see attack 2).**

| Consumer class | Reads | Observed |
| --- | --- | --- |
| SiO / surface release | `_interface_pO2_bar` / transport | M4 zero-commit interface = gas; `test_fe_saturation_bound_never_sets_sio_release_pressure` **passed** |
| Exchange film | `_current_melt_redox_fO2_log` (equality) + private brackets | Absent equality → gas-side limiting regime; not the bound as pressure |
| Native-Fe / respeciation | equality / regime | Skip with `skipped_fe_saturation_bound` / `skipped_ferrous_free_lower_bound`; extent 0 on M4 |
| Liquidus / freeze / PT-0 | speciation key | Intended |
| Equilibrium a_FeO / SulfSat / vapor `intrinsic_fO2_log` | speciation key | Intended by AMEND list — **but** M3 key is the ±30 sentinel (attack 2) |

No key consumer still substitutes −9. Key consumers do **not** refuse on M3; they take the clamped float.

### (2) M3 clamped edge confined to liquidus/PT-0? — **NO → REVISE**

AMEND P2 (`REVIEW-redox-r3-chunk5-amend-2026-10-01.md`): post-clamp M3 edge is a liquidus/PT-0 sentinel (±30); **a_FeO on M3 must stay unavailable**; do not evaluate SulfSat at the sentinel as measured pO₂.

Tip `_melt_redox_speciation_key` for `ferrous_free_lower_bound` always returns `_freeze_gate_liquidus_fO2_log(bound)` (finite ±30), never `None`.

Probe on `_fully_ferric_sim()` at 1400 °C:

| Channel | Value |
| --- | --- |
| Raw mole-log bound | ~78.57 |
| Speciation key | **30.0** (`bound` / `ferrous_free_lower_bound`) |
| Equality reader | `None` |
| `calphad_ferrous_feo_activity_diagnostic(fO2_log=30)` | **status `ok`**, `a_FeO_authoritative ≈ 1.15e-7` |
| SulfSat `compute_sulfur_saturation(... fO2_log=)` | **30.0** (captured) |

Equilibrium.py chooses activity from speciation-key `raw_intrinsic_fO2_log`, not from equality — so M3 never takes the `intrinsic_fO2_log is None → a_FeO unavailable` branch. SulfSat always feeds the key into the gate when melt is present.

**This is the AMEND P2 contract violation named in the REQ as a REVISE item.**

### (3) Residual Kress ferric 0.0231 at bound vs ledger 0

Worker (lunar hero hour 60 / 1220 °C): Kress q(bound) = 0.02307066, ledger = 0.

Independent tip probe (10 wt% FeO, no Fe₂O₃, 1220 °C): residual ≈ **0.0165** (~1.65%). AMEND residual band was O(10⁻²) / prior ~1.4–1.9%. Worker 2.31% is composition-dependent but **same order**; R-a still forbids writing it. Flag fields `kress_ferric_fraction_at_bound` / `ferric_fraction_residual` present on M4 key. **Consistent — not REVISE.**

### (4) Hero Ca/Al/Ti retention vs r12c metric

Worker “approximately 100%” is **not** the r12c melt-retained fraction. It matches ledger **closure** (input≈terminal residual ≪ 5e-14), which is always ~100% when the run closes.

r12c metric: `retained_melt_mol_atoms / feedstock_input_mol_atoms` from `yield_disposition.ideal_train_melt_boundary.rows`.

From Mac `docs-private/reviews/2026-10-01-redox-c5/hero_tip.json` (225 h lunar C2A, internal-analytical; artifact `kernel_commit_sha` is a dirty/unpushed Mac object, not tip SHA — treat numbers as the worker’s completed hero payload, not a re-run on this seat):

| Element | Tip melt-retained | r12c tip | r12c baseline |
| --- | ---: | ---: | ---: |
| Al | **0.921351907** | 0.921000943 | 0.921013586 |
| Ca | **0.953727020** | 0.953075164 | 0.953094524 |
| Ti | **0.546134304** | 0.543442670 | 0.542136615 |

vs historical stack heroes (Al ~0.921 / Ca ~0.953 / Ti ~0.54): **nothing moved into “~100%”**. Tip is within a few ×10⁻³ of r12c (Ti +~0.0027). **No base-`6ce76a969` hero artifact on disk**; worker claimed SiO tip−base = 0; VPS did not re-run 225 h. Base exact retention **not independently established here**.

### (5) Regime hours M0 76 / M2 1 / M4 1 / M5 147

Worker census accepted as event/domain diagnostics (not re-derived from `fe_redox_split.redox_domain.basis` alone — early hours omit domain while solid; hour 61 shows Kress inverse with native-Fe event `skipped_fe_saturation_bound`).

**One M4 hour is plausible:** first worker aborted at hour 60 in an M4-shaped saturation-bound state; completing past that edge can leave a single classified M4 tick before ferric rises above NOOP into M5.

**147 M5 hours:** interior Kress mole-log equality with positive FeO and Fe₂O₃. Hero ferric fractions on liquid hours span ~3e-5 (near-M4 edge) to ~0.9 (oxidized), with many mid-run hours at tens of percent Fe³⁺/ΣFe under certified or extrapolated authority. Not a frozen zero-ferric M4 coast.

### (6) “106 extrapolated / OOD redox hours, 38 floor-fallback hours”

- **106 extrapolated/OOD:** domain `authority` / `authority_level` = `extrapolated` or `status=out_of_domain` on Kress inverse (hero authority count: extrapolated 106, certified 59) — flagged, not silent.
- **Floor fallback:** `_melt_redox_liquidus_floor_fallback` — when liquidus curve authority is unavailable, engages **named** diagnostic `liquidus_unavailable_floor_fallback` with `floor_T_C = KRESS91_LIQUID_CALIBRATION_MIN_T_C`, counted in `melt_redox_gate_floor_fallback_engagement` (hero: 38 hours, hours 187–224). **Not** a silent −9 / bound-as-equality substitute for melt fO₂. Explicit degraded liquidus path.

**Attack 6 does not force REVISE.**

## Targeted tests (VPS, `-o addopts=`)

| Suite | Result |
| --- | --- |
| `tests/test_redox_authority_floor.py` | **51 passed** (~79 s) |
| `tests/test_headspace_transport.py` + controlled O₂ + overhead | **106 passed** (~56 s) |
| freeze-gate `-k 'interface or redox or floor_fallback…'` | **17 passed** |
| web panel Fe-redox bound rendering | **1 passed** (selected) |
| `test_fully_ferric_fe2o3_release_publishes_committed_interface_root` | **passed** (chunk 3c; shared tip) |

Known base failures (not blocking LAND-if-clean; noted for physics): fixed-pO₂ SiO magnitude pin (`tests/chemistry/test_sio_chain_coherence.py`); RH03[24] hours; 130 h `test_c2a_staged_freeze_gate_on_closes_mass_balance` timeouts — same class as prior chunk notices / worker.

## Mutations

No local product mutations left on the seat (tree clean except `.slot-busy`). Worker’s six mutation variants (incl. feeding the key into the SiO release rail) accepted as consistent with tip tests `test_fe_saturation_bound_never_sets_sio_release_pressure` / M4 SiO regression intent; not re-run on VPS.

## Non-blocking notes

- Vapor-pressure dispatch still threads speciation-key `intrinsic_fO2_log` into the kernel while surface release stays on `interface_pO2_bar` — correct partition for SiO; M3 sentinel still pollutes activity channel (folded into P1).
- Hero artifact SHA hygiene: stamp tip SHA on future hero payloads.
- Worker retention wording should say melt-retained fractions, not closure.

## Verdict

VERDICT: REVISE

### P1
1. **M3 speciation-key scope (AMEND P2 / REQ attack 2):** After clamp, the M3 edge must not drive melt/vapour **a_FeO** or **SulfSat** as if it were measured pO₂. Keep equality-absent → `a_FeO` unavailable (`equilibrium.py` must key activity off equality / refuse on `ferrous_free_lower_bound`, not off the ±30 sentinel). SulfSat must not call `compute_sulfur_saturation` with the clamped M3 key (typed not-evaluated / unavailable naming the regime is fine). Discriminator: fully-ferric fixture → `a_FeO` diagnostic `unavailable` with reason naming `ferrous_free_lower_bound`; SulfSat must not receive `fO2_log == 30.0`.

### P2
- None beyond P1. Residual ~0.016–0.023 at 1220 °C stays a flagged M4 prediction (R-a). Floor-fallback remains an explicit liquidus degraded path. Retention reporting hygiene as above.

— regolith-empirical
