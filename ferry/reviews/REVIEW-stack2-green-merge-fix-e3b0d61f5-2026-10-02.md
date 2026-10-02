# REVIEW — stack2 green-merge-fix (REVIEW OF RECORD)

- **Reviewer:** regolith-empirical (frontier REVIEW OF RECORD)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `e3b0d61f59aa5edd48bdb0c255a103c956cfa9bc` on `review/stack2-green-merge-fix`
- **Range:** `3c040a4cf..e3b0d61f5` (9 non-merge commits; merge base `3c040a4cf` = stack2 `c5674a2d2` + green `91567188d`)
- **REQ:** `/workspace/ferry-inbox/REQ-stack2-green-merge-fix-from-regolith-physics-2026-10-02.md`
- **Bundle:** `regolith-physics-stack2-gmf-2026-10-02/` (`green-merge-fix-report.md`, `gmf-followup-report.md`, `new-vs-green-3bb8c5496.txt`)
- **Ack:** `STATUS-req-stack2-green-merge-fix-acked-2026-10-02.md`
- **Date:** 2026-10-02 ~19:25–20:00 ET
- **Mode:** read-only for product extracts; reviewing product code; targeted VPS unit tests only (no full W3 / full pytest). No push of empirical feature branches. No Mac listen pools.

## Scope

Fix the 39 NEW studio-suite failures of stack2 vs green. Controller rejected `e02254266` (routed M3 +30 speciation key into vapour activity). Tip `e3b0d61f5` reverts that routing: M3 Fe vapour activity from melt iron inventory at the **committed interface pO2** via Kress, flagged `fe_activity_from_interface_in_ferrous_free_melt`; mutation route +30 → red.

Out of scope (per REQ): later merge with redox c7 LAND `364d37511` + evaporation batch-reuse CPU fix before a studio suite.

## Attack results

### (1) NOT-FIXED: net of e02254266+e3b0d61f5, does ANY path still feed +30 to a_FeO / vapour activity / SulfSat? — **PASS (no)**

**Code (tip):** `simulator/core.py` `_refresh_vapor_pressures_from_kernel` nulls `intrinsic_fO2_log` when `speciation_regime == 'ferrous_free_lower_bound'` before dispatch (`control_inputs['intrinsic_fO2_log']=None`, `fO2_log=None`). Diagnostic records the nulled key (`melt_redox_speciation_key.fO2_log is None`) while freeze-gate/PT-0 consumers retain the bound via `_melt_redox_speciation_key()`.

**Seat probe (fully ferric M3 fixture, key=30.0):**
```
SPECIATION 30.0 bound ferrous_free_lower_bound
DISPATCH fO2_log= None intrinsic= None iface= 1e-08
DIAG source_reaction_fO2_log10= None
DIAG Fe activity_basis= kress91_interface_ferrous_free_melt
DIAG activity_flag.code= fe_activity_from_interface_in_ferrous_free_melt (fO2_log=-8.0)
Fe act matches iface Kress (0.2215); differs from Kress@+30 (1.91e-7)
EQ a_FeO= unavailable / ferrous_free_lower_bound_has_no_melt_equality
SULFSAT calls=[] status=not_evaluated reason=ferrous_free_lower_bound
ATTACK1_HITS NONE
```

**Pin:** `test_ferrous_free_vapor_activity_uses_committed_interface_pressure` **passed** (asserts dispatch +30 null, Fe activity = iface Kress ≠ 30, SulfSat not_evaluated).

No residual path feeds +30 into a_FeO equality, vapour activity, or SulfSat.

### (2) Optimizer pricing of flagged/bounded wall numbers — **PASS**

Commits `a57d1f35f` / `5750ec9f5` / `f31781f09`:

- **Bounded refusals priced WITH flag:** `_coating_source_flux_upper_bounds` / `_coating_refused_flux_bounds` attach upper bounds; `product_summary` retains `warning` + `authoritative=False` + message `"priced from vapour-flux upper bounds …"` (`objective.py`). Physics docstring: *"Missing species/rate evidence remains missing, never zero."*
- **Flagged coating retained:** `5750ec9f5` keeps numeric `coating_status=="warning"` instead of overwriting to unavailable when resinter margin is unavailable.
- **Flagged wall-deposit outputs:** `f31781f09` prices status-bearing non-authoritative wall quantities (`priced_status_bearing_quantity` / `has_priced_wall_quantity`) without declaring them authoritative.

**Pins:** `test_refused_wall_saturation_flux_bound_keeps_coating_priced` **passed**; wall sticking `-k 'refus|bound|…'` **9 passed**; optimizer evaluate coating/wall narrow **30 passed**; `test_budget_three_stub_e2e_writes_artifacts_and_round_trips_winner` **passed**; `test_cli_help_unknowns_and_budget_one_stub_run` **passed** on tip (was tip-fail in green-merge report before f317).

Mandate §4 corollary holds on the attacked paths: refused never priced as silent zero; flagged quantities priced with flag.

### (3) e7bd0cd52 gas partial vs scheduled pO2 — **PASS**

Commit is a **test/assertion** correction only (`tests/test_runner_preset_bridge.py`): enforcement records scheduled boundary; `per_hour_summary[].pO2_bar` is realized overhead-gas O2 partial (`snapshot.overhead.composition['O2']`), with `0 ≤ pO2_bar ≤ P_total_bar`. Production runner already separated these (Phase C P1 comment at `build_per_hour_summary`).

**Consumers (tip):**
| Surface | Source |
| --- | --- |
| `per_hour_summary.pO2_bar` | overhead gas partial |
| `pO2_enforcement_by_hour` | scheduled/achieved setpoint |
| vapour `control_inputs.pO2_bar` | `_vapor_pressure_transport_pO2_bar` (headspace transport / commanded) |
| vapour `interface_pO2_bar` | `_vapor_pressure_dispatch_pO2_bar` → committed `reservoir.interface_pO2_bar` |

No consumer found that now reads scheduled pO2 where gas partial or interface is required. **Pin:** `tests/test_runner_preset_bridge.py` **13/13 in suite batch** (full preset+SSO-R+redox batch green).

### (4) M3 Fe vapour channel ineligible — silent or flagged in hour output? — **ANSWERED (flux silent; activity flagged on VP provenance)**

**Known model gap confirmed:**
- Catalog rule `Fe` parents=`['FeO']`, `request_rule_kind=source_inventory_present`.
- Seat: `FeO` mols absent → `cat.build_request(ledger)` **omits Fe** (`Fe in request? False`; request sample Si/SiO only). Omission ≠ refused channel answer.
- Hour `vapor_species_kg_hr` drops |rate|≤1e-12 and has no Fe key → **silent zero flux** for Fe when other volatiles exist (no per-species INELIGIBLE in `vapour_batch_summary`).

**Flagged surface (activity, not flux eligibility):** tip `e3b0d61f5` computes Fe vapour **activity** at committed interface pO2 with provenance `activity_flag.code=fe_activity_from_interface_in_ferrous_free_melt` (seat redispatch shows Fe Pa finite + flag). That flag lives on vapour-pressure numerator provenance / provider warnings — **not** promoted into hour `vapor_species_kg_hr` as an eligibility notice.

Non-blocking known gap for this REVIEW OF RECORD (REQ asked silent-vs-flagged; not a fix). Optional follow-up: surface request-omission of Fe under M3 on the hour summary.

### (5) a8cefc8ba merge-tree clean into this tip? — **PASS (clean; not an ancestor — expected)**

- `a8cefc8ba` (**Assert KEMS parity band exclusion**) is **not** an ancestor of HEAD (`merge-base --is-ancestor` exit 1).
- Merge-base with tip = green `91567188d`. Commit sits on the parallel KEMS/extract / `review/t1075-ia-kems-parity` line (descendant of green), not on stack2-green-merge-fix.
- `git merge-tree --write-tree HEAD a8cefc8ba` → tree `8c2eac3f9c…`, **zero** `changed in both` / CONFLICT markers.
- Expected: sibling line; REQ already notes later merge with redox c7 `364d37511` + evap batch-reuse before studio. Not a problem for this tip.

## Targeted VPS results (tip `e3b0d61f5`)

| Nodes | Result |
| --- | --- |
| `test_redox_authority_floor` + `test_fe_redox_kress91` + `test_fe_redox_split_diagnostic` + `test_sso_r_validation_map` + `test_runner_preset_bridge` | **171 passed, 1 skipped** |
| `test_ferrous_free_vapor_activity_uses_committed_interface_pressure` | **passed** |
| `test_vaporock_backend` | **81 passed, 3 skipped** |
| wall sticking refus/bound/pricing pins | **9 passed** |
| optimizer evaluate coating/wall narrow | **30 passed** |
| `test_refused_wall_saturation_flux_bound_keeps_coating_priced` | **passed** |
| `test_budget_three_stub_e2e…`, `test_cli_help_unknowns_and_budget_one_stub_run` | **passed** |
| Seat probes attack (1)/(4) | **as above** |

**VPS residual (non-blocking for attacks):** `test_parallel_composition_target_stub_study_completes` → `completed-no-feasible-winner` on this 16GB box (VapoRock unavailable). Aligns with followup’s residual coating/composition-gate paths; not a +30 / pricing / pO2 / a8cefc8ba failure. **ASK Mac Studio** for full green gate after merge with c7 + evap batch-reuse (per REQ note + green-merge studio regen list).

## Non-blocking notes

- Studio golden regen still owed (`test_runner_smoke`, `test_sio_yield_regression`, cost/recipe goldens) — documented in bundle; out of scope for VPS.
- M3 Fe flux silence in hour output (attack 4) — answered; optional observability follow-up.
- Parallel merge with `364d37511` + evap batch-reuse before studio suite.

## Verdict

**LAND** `e3b0d61f59aa5edd48bdb0c255a103c956cfa9bc`

Attacks (1)–(5) answered with code + targeted tests + seat probes. Controller rejection of +30→vapour routing is closed by `e3b0d61f5`; optimizer flagged/bounded pricing holds; gas partial vs scheduled pO2 consumers correct; a8cefc8ba merge-tree clean as a sibling of this tip.

## Independent re-seat confirmation

Prior seat executor disappeared mid-flight; this REVIEW OF RECORD was re-done from scratch on the same checkout (`slot-b565` @ `e3b0d61f5`) at **2026-10-02 ~19:40–20:05 ET**. Independent code+test evidence confirms the same verdict:

| Attack | Re-seat result |
| --- | --- |
| (1) +30 → a_FeO / vapour / SulfSat | **PASS (no)**: tip nulls `intrinsic_fO2_log` for `ferrous_free_lower_bound`; a_FeO unavailable; Fe activity = Kress@iface ≠ Kress@+30; SulfSat `not_evaluated`/`ferrous_free_lower_bound`. Pin passed. |
| (2) flagged/bounded wall pricing | **PASS**: refused flux upper bounds; missing never zero; `warning` retained; status-bearing wall priced non-authoritative. Pins passed. |
| (3) gas partial vs scheduled pO2 | **PASS**: `e7bd0cd52` test-only; `build_per_hour_summary.pO2_bar` from overhead O2 partial; interface separate. |
| (4) M3 Fe channel hour silence | **ANSWERED**: Fe activity flagged on VP provenance (`fe_activity_from_interface_in_ferrous_free_melt`); hour `vapor_species_kg_hr` / summary lack Fe eligibility notice → flux silence is known non-blocking gap. |
| (5) a8cefc8ba merge-tree | **PASS**: not ancestor; merge-base green `91567188d`; `merge-tree --write-tree` clean vs `a8cefc8ba` and vs moved green `e620b4dd5`. |

Targeted VPS re-seat pins (4): ferrous-free vapour activity, ferrous-free hour split publish, preset-bridge pO2, refused-wall pricing — **all passed**.

**LAND** `e3b0d61f59aa5edd48bdb0c255a103c956cfa9bc` unchanged.

— regolith-empirical (re-seat)

