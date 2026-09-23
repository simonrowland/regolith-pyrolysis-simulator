# R20 — N1 run-level engine commissioning notice

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `3a78247647c9d2f437dcbdd2c1d5f0fb4938dc46` on `origin/review/n1-run-commissioning` (detached)  
**Worktree:** `/workspace/repos/wt/slot-06` @ detached `3a7824764`  
**Branch tip message:** Report out-of-band engine commissioning beside run and optimizer numbers.

**Files (tip commit):** `engines/engine_commissioning.py`, `simulator/core.py`, `simulator/evaporation.py`, `simulator/optimize/evaluate.py`, `simulator/optimize/objective.py`, `simulator/runner/__init__.py`, `web/report_viewer/panels/p11-deliverables.js`, `tests/test_engine_commissioning_run_notice.py`, `tests/test_web_panel_p11.py`

**Intent:** When any engine step carries `authority=extrapolated` outside the certified band, fold per-step commissioning notices into **one** run-level `engine_commissioning_notice` on the run report (`run_metadata` + product-classification markdown), product summary, P11 deliverables panel, and optimizer run-reference trace — without changing numbers, objectives, or prune/feasibility. In-band runs omit the key.

**Attack surface:** A number changed; an in-band run flagged; a surface that shows ledger/yields without the notice; optimizer treating extrapolated as infeasible.

**Method:** Static read of tip diff vs green for the nine tip files; adversarial probes (inject notice post-run, empty aggregate, malformed plain-notice drop, multi-authority omit, freeze-gate-style note helper); live instrumented C0 lunar hour-1 note-call trace; pytest on tip worktree. Mode: **ran-tests**.

---

## Findings

### P3 — P15 equipment diagram shows product kg without the commissioning banner

**Evidence (file:line on `3a7824764`):**

- `web/report_viewer/panels/p15-equipment-diagram.js:146–150,278` — reads `product_classification` silica `class_total_kg` / stage product mass; no `engine_commissioning_notice` read
- `web/report_viewer/panels/p15-equipment-diagram.js:537–539` — metal product yields from summary without commissioning flag
- Contrast: `p11-deliverables.js:71–76,660–664` and `commissioningBanner` (`:82–104`) render the run-level notice beside class totals

**Why P3 (defense-in-depth, not wrong numbers):** Claimed consume surfaces (P11, markdown, `run_metadata`, product summary, optimizer trace) carry the notice. P15 is a second visual of the same kg claims; an operator who only looks at the equipment diagram can miss `authority=extrapolated`. No score/ledger mutation.

**Fix direction (optional):** Reuse `commissioningBanner` (or a shared helper) above P15 product readouts when `terminal.product_classification.engine_commissioning_notice` is present.

---

## Residual notes (not severity)

- **Product ledger intentionally notice-free.** Tests assert `"engine_commissioning_notice" not in sim.product_ledger()` (`tests/test_engine_commissioning_run_notice.py:111,182`). Ledger stays numbers-only; report layers sit beside it — matches claim surfaces, not a miss.
- **Freeze-gate path is latent.** `data/setpoints.yaml:1930` has `freeze_gate.enabled: false`. Tip notes commissioning on liquidus **failure** only (`simulator/evaporation.py:2134–2146`). An `ok` liquidus that still carries `commissioning_notice` is not folded into run steps. Irrelevant on default campaigns until the gate flips; equilibrium noting via `_record_equilibrium_status` (`simulator/core.py:8970`) is the live path.
- **`evaluated_temperature_C` stripped** by `_plain_commissioning_notice` (`engines/engine_commissioning.py:509–541`) when aggregating — run-level payload keeps reason/band/failed/authority only.
- R1 VapoRock refuse-path false notice is **fixed** on this tip’s ancestry (`84c1d2939`; assess after empty-composition refusal in `vaporock.py:1825–1834`).

---

## Attack checklist

| Attack | Result |
| --- | --- |
| A number changed | **Fail (safe).** Injecting a step notice after a finished hour-1 C0 lunar run left `final_state` / `per_hour_summary` / classification / product-summary numbers / `compute_objectives(...).as_mapping()` byte-identical after stripping the notice key (`tests/test_engine_commissioning_run_notice.py:170–181` + live probe). Aggregation is additive metadata only. |
| An in-band run flagged | **Fail (safe) on live equilibrium path.** Clean hour-1 C0 lunar: `engine_commissioning_run_notice() is None`; no key on `run_metadata` / product_classification / trace / product_summary. Instrumented run: one `note()` call with `has_notice=False` (diagnostics = vapor-pressure zero reason only). Absence is not a notice (`commissioning_step_from_diagnostics`). |
| Surface shows ledger/yields without the notice | **Claimed surfaces covered; one secondary gap → P3.** Present on `run_metadata`, product-classification markdown (`**Engine commissioning**` after `**Class totals**`), `product_summary`, optimizer `_cache_trace_payload` / `_run_reference.trace`, P11 banner. Ledger deliberately omitted. P15 kg readouts lack banner (P3 above). |
| Optimizer treating extrapolated as infeasible | **Fail (safe).** Notice attached only in `_cache_trace_payload` / `product_summary`; not consulted by feasibility/prune. Injected notice kept `decided_backend_status="ok"` and left objectives unchanged. Doctrine remains predict-and-flag (`engines/engine_commissioning.py:3–8`). |

---

## What looks sound

- Single aggregate: `aggregate_engine_commissioning_notice` dedupes by reason/failed/band/authority; `count` = steps with notices; `first_hour`/`last_hour` from step hours (`engines/engine_commissioning.py:443–508`).
- Hour stamp `melt.hour + 1` matches post-step snapshot convention; pinned by unit test (`core.py:8980–8984`, test `:103–105`).
- Reset clears `_engine_commissioning_steps` on re-init (`core.py:936,1423–1426`).
- Failure reports still attach (`runner/__init__.py:4989`).
- Markdown insertion is non-numeric; falls back to append if `**Class totals**:` missing (`:3331–3358`).
- Multi-authority omits top-level `authority` (probe confirmed) so a mixed set cannot launder a single label.

---

## Tests

Tip worktree at `3a7824764`, repo `.venv` via `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/test_engine_commissioning_run_notice.py` + `tests/test_web_panel_p11.py`: **27 passed**
- Adversarial probes (empty aggregate, malformed plain drop, post-run inject number/objective parity, multi-authority omit, freeze-gate-style helper): **passed**

No push. No fix patch (optional P15 banner only).

---

## Verdict rationale

Named attacks do not land a live wrong number, false in-band flag on the equilibrium path, or optimizer prune on `extrapolated`. Claimed report/optimizer/P11 surfaces carry one run-level notice; ledger exclusion is intentional. One secondary UI gap (P15) remains defense-in-depth. **LAND.**

VERDICT: R20 | LAND | P0=0 P1=0 P2=0 P3=1 | ran-tests
