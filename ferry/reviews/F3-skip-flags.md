# F3 — skip gates + flag carriers (V3 + V5)

**Repo:** regolith-pyrolysis-simulator  
**Base:** `origin/review/r6-r8-fix` @ `1f8df6cbe`  
**Branch tip:** `1bbe8e8574a50a37deb6b851ab4364aa184838d8` on `origin/empirical/f3-skip-flags-2026-09-22`  
**Worktree:** `/workspace/repos/wt/slot-01`  
**Inputs (CONFIRMED only):**  
- `/workspace/ferry-inbox/verify/V3-s16-host-coupled.md` (9× P2 host-coupled skip defects)  
- `/workspace/ferry-inbox/verify/V5-s14-per-step-flags.md` (8× P1 flag-carrier gaps)

**Intent:** Fresh CI must skip private-resource host-coupled tests instead of ERROR/FAILED; out-of-band engine / SulfSat / rump notices and battery cell authority must reach run and optimizer carriers without changing numbers.

**Method:** One commit per shared root. Cherry-picked confirmed landings for engine commissioning (`3a7824764`) and battery flags (`472febdd3`); implemented V3 skips and V5 SulfSat/rump rollups on the r6-r8-fix base. Focused pytest + mutation proofs on tip. Mode: **ran-tests**.

---

## Commits (tip ← base)

| SHA | Root | Source |
| --- | --- | --- |
| `df3980ef6` | V5 R1 engine commissioning run rollup | cherry-pick `3a7824764` |
| `3657631b3` | V5 R4 battery cell authority harvest | cherry-pick `472febdd3` |
| `7ffafa7c3` | V3 R1 Robie B1452 PDF / pdftotext / MinerU skip | new |
| `841007482` | V3 R2 JANAF private `_raw_table_path` skip | new |
| `da0bfcd90` | V3 R3 ungated `ENGINE_REPORT_PATH` skip | new |
| `c24f9d666` | V5 R2 SulfSat run rollup | new (n1 pattern) |
| `1bbe8e857` | V5 R3 rump-expectation run rollup | new (n1 pattern) |

---

## V3 — host-coupled skips (P2)

### R1 Robie B1452 (`7ffafa7c3`)

- `SOURCE_PDF` / `MINERU_ROOT` now resolve under `REGOLITH_CORPUS_ROOT` (same default as B1544).
- `source_layout` fixture skips when the PDF or `pdftotext` is absent (was `assert` → ERROR).
- MinerU loader skips when the MinerU tree is absent.
- Mutation proof: `test_robie_b1452_source_layout_gate_skips_without_private_pdf`, `test_robie_b1452_mineru_gate_skips_without_private_tree`.

### R2 JANAF private tables (`841007482`)

- `_require_raw_table` skips when the resolved path is not a file (fixture or private corpus).
- Gates the three confirmed tests (`off_grid…`, `grid_membership…`, `non_data_marker…`).
- Mutation proof: `test_janaf_raw_table_gate_skips_without_private_corpus` (monkeypatched missing corpus → `pytest.skip.Exception`).

### R3 engine report (`da0bfcd90`)

- `_engine_report_unavailable()` + skip in `engine_residuals` fixture and before the C5 report read (debit/alpha asserts above the C5 read stay).
- Mutation proof: `test_engine_report_gate_skips_without_private_docs`.

---

## V5 — flag carriers (P1)

### R1 engine commissioning (`df3980ef6`)

Cherry-pick of `review/n1-run-commissioning` @ `3a7824764`: step accumulate → `engine_commissioning_run_notice` → `run_metadata` / product markdown / optimizer trace+summary. Numbers unchanged; in-band runs omit the key.

### R2 SulfSat (`c24f9d666`)

Mirror n1: `_note_sulfur_saturation_step` on post-eq out-of-range/unavailable → `sulfur_saturation_run_notice` → runner `run_metadata`, objective `product_summary`, evaluate trace. Mutation proof in `tests/test_sulfur_saturation_run_notice.py` (inject on/off number parity; attach hook feeds rollup).

### R3 rump expectation (`1bbe8e857`)

`rump_expectation_run_notice` folds `_rump_expectation_warnings` → same carriers. C4/C6 refusal path in `run_executor` unchanged. Mutation proof in `tests/test_rump_expectation_run_notice.py` (warning inject number parity; campaign capture feeds rollup).

### R4 battery authority (`3657631b3`)

Cherry-pick of `review/s7-battery-flags` @ `472febdd3`: harvest commissioning/IMCC onto `EquilibrateCell` and score from cell authority (no hard-coded `bridge`).

---

## Tests (tip `1bbe8e857`)

Repo `.venv` via `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/test_engine_commissioning_run_notice.py` + `tests/test_battery_engine_flags.py` + `tests/test_sulfur_saturation_run_notice.py` + `tests/test_rump_expectation_run_notice.py` + three skip-gate proofs: **15 passed**
- Host-coupled Robie/JANAF/rail nodes under absence: **skipped** (not ERROR)

Pushed: `origin/empirical/f3-skip-flags-2026-09-22`.

---

## Verdict rationale

All confirmed V3 P2 skip gates and V5 P1 flag carriers from the verify inputs are on the tip. Commissioning and battery landings match the referenced review tips; SulfSat and rump mirror the n1 accumulate→run-carrier pattern. No product-number changes claimed or observed in the mutation proofs.

VERDICT: F3 | LAND | P0=0 P1=0 P2=0 P3=0 | ran-tests

READY: /workspace/ferry-inbox/reviews/F3-skip-flags.md
