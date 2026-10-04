# REPORT — b693/b691 scorer-gates green merge + residual census

**From:** regolith-empirical  
**To:** regolith-main  
**At:** 2026-10-04 ~13:17 EDT  
**BACKLOG:** item 2 (`review/b693-b691-scorer-gates`)  
**Branch:** `review/b693-b691-scorer-gates`  
**Worktree:** `/workspace/repos/wt/slot-06`  
**Local tip:** `043759ba35b1765d61e2da71921f16a56f50f92d`  
**Remote tip (`git ls-remote origin refs/heads/review/b693-b691-scorer-gates`):** `043759ba35b1765d61e2da71921f16a56f50f92d`  
**Match:** **yes**

---

## Verdict

**DONE.** Green merged (merge, not rebase), tip pushed, ls-remote verified. Six FIX-FIRST findings remain addressed at ancestor `90d1c55d1`. Three pregate failures remain green on the merged tip. Live `score_store` residual census run on available engines; unavailable engines marked **NOT RUN**.

---

## History / push

| Check | Result |
|---|---|
| Working tree | clean |
| FIX tip `90d1c55d1` ancestor of HEAD | yes |
| Green merge `05b6309d3` ancestor | yes (merge commit `96163a646`) |
| Tip `8089eadbf` (= `origin/work-v064-green` at seat time) ancestor | yes (merge commit `043759ba3`) |
| Prefer merge over rebase | yes — two explicit merge commits, no rebase |
| Force-push | no |
| Push | `90d1c55d1..043759ba3  HEAD -> review/b693-b691-scorer-gates` |

First-parent path (excerpt):

```
043759ba3 Merge commit '8089eadbf…' into review/b693-b691-scorer-gates
96163a646 Merge commit '05b6309d3…' into review/b693-b691-scorer-gates
90d1c55d1 fix(battery): address b-693/b-691 review FIX-FIRST findings
c42aca18a fix(battery): restore streamed all_numeric parity and measured zero-grid
…
75e94a16c test(battery): pin b-693 figure_only/reactive and b-691 dual headline
```

---

## Six review findings (status on tip)

Prior FIX report: `REPORT-FIX-scorer-gates-review-findings-2026-10-04.md` @ `90d1c55d1`. Re-verified on green-merged tip via targeted tests (below).

| # | Finding | Status on `043759ba3` |
|---|---|---|
| 1 | [P1] Headline A in Markdown + JSON; B independent of A | **fixed** — all_numeric emitted on typed/stream/payload paths; measured zero-grid independent (`report_engine_names` measured-only) |
| 2 | [P1] Figure band never borrows; mapped dex read | **fixed** — no measured-band fallback for figure; `_mapped_reading_uncertainty_dex` / `_dex_width_from_mapping` |
| 3 | [P1] Catalogue composition does not erase figure band | **fixed** — figure/reactive keep band while staying out of certified |
| 4 | [P2] All-numeric non-DEX median/IQR | **fixed** — `metric_operation_for_identity`; measured schema unchanged |
| 5 | [P2] Injected-predictor contract | **fixed** — `bench=` only when `predict is None`; stub callers unchanged |
| 6 | [P2] `figure_only` notice when quantity unknown | **fixed** — `_figure_only_notice` attaches even if quantity unknown; live census shows figure_only notices on Schaefer–Fegley / Thomas–Wood / Pahlevan / JGR-Mars / De Maria / … |

---

## Pregate failures (status on tip)

Prior: `REPORT-impl-scorer-gates-pregate-failures-2026-10-04.md` @ `c42aca18a`.

| Test | Status |
|---|---|
| `test_streamed_scoring_is_byte_identical_to_legacy_fixture` | **pass** |
| `test_streamed_unrailed_measured_tier_matches_legacy_zero_grid` | **pass** |
| `test_admitted_model_derived_rows_emit_residuals_per_imcc_engine` | **pass** |

---

## Engine availability (open_battery_engine probe)

Probe: `simulator.diagnostic_helpers.binary_pot_battery.open_battery_engine` under `if __name__ == "__main__"` guard in the census script.

| Engine | Available | Notes |
|---|---|---|
| `openimcc` | **yes** | scored |
| `internal-analytical` | **yes** | scored |
| `vaporock` | **no** | **NOT RUN** — VapoRock module / warm-pool init fail |
| `alphamelts` | **no** | **NOT RUN** — AlphaMELTS unavailable on VPS |
| `thermoengine` | **no** | **NOT RUN** — ThermoEngine unavailable on VPS |
| `magemin` | **no** | **NOT RUN** — MAGEMin binary missing / init fail |

`engines/engines.local.toml`: **does not exist** in this worktree.

Stale committed `data/battery/score-report.md` / `score-summary.json` were **not** used as residual evidence.

---

## Live `score_store` residual census

**Method:** production `load_score_context(sources=…)` + `score_store(ctx, engines=(OPENIMCC, INTERNAL_ANALYTICAL), include_diagnostics=True)` on tip `043759ba3`. Default predict path (no injected handles).

**Scope:** 12 figure / reactive / De Maria sources named in the review-of-record groups (not full store; 16GB VPS constraint).

| Metric | Count |
|---|---:|
| Observations loaded | 1111 |
| Comparison candidates | 510 (figure_only 264, measured_direct 243, measured_reduced 3) |
| Engine residual rows | **1042** (521 × openimcc + 521 × internal-analytical) |
| Status | **all 1042 refused** |
| Numeric residuals | **0** |
| `score_eligible` | **0** |

### Residuals by engine × source

| source | internal-analytical | openimcc | total |
|---|---:|---:|---:|
| kems-007-costa-2015 | 79 | 79 | 158 |
| kems-022-demaria-1971 | 41 | 41 | 82 |
| kems-023-demaria-1973 | 43 | 43 | 86 |
| kems-029-yakovlev-shornikov-2011 | 4 | 4 | 8 |
| kems-137-bischof-2023 | 28 | 28 | 56 |
| kems-138-bischof-2023 | 16 | 16 | 32 |
| pahlevan-2026-protolunar-volatile-outflows | 51 | 51 | 102 |
| jgr-p-2024-mars-sam-clay-sulfate-ega | 50 | 50 | 100 |
| schaefer-fegley-2011-vaporization-earth | 60 | 60 | 120 |
| thomas-wood-2021-chlorine-silicate-melts | 111 | 111 | 222 |
| ta-dacko-conradt-low-p-transpiration | 38 | 38 | 76 |
| kems-017-stolyarova-2013 | 0 | 0 | 0 (no candidates in this load) |

### Refusal reasons (all engines combined)

| reason | n |
|---|---:|
| identity_unknown | 636 |
| identity_incomplete | 220 |
| unsupported | 182 |
| effusion_regime_unverified | 4 |

### `figure_only` notice rows (finding 6 live check)

| source | residual rows with `figure_only` notice |
|---|---:|
| schaefer-fegley-2011-vaporization-earth | 120 |
| thomas-wood-2021-chlorine-silicate-melts | 144 |
| pahlevan-2026-protolunar-volatile-outflows | 102 |
| jgr-p-2024-mars-sam-clay-sulfate-ega | 100 |
| kems-022-demaria-1971 | 60 |
| kems-138-bischof-2023 | 4 |
| ta-dacko-conradt-low-p-transpiration | 2 |

**Residual summary:** on available engines, this scoped live run produces **zero numeric / certified residuals**; every scored row refuses on typed identity/unsupported/regime gates. Figure sources now carry `figure_only` notices (finding 6). Engines vaporock / alphamelts / thermoengine / magemin are **NOT RUN** (unavailable; no `engines.local.toml`).

Full-store / six-engine Mac Studio green gate: **not run** on this VPS — ASK if required.

---

## Targeted tests (VPS, ≤300s, no full W3)

```
pytest -n0 \
  tests/battery/test_b693_b691_scorer_gates.py \
  tests/battery/test_score.py::test_streamed_scoring_is_byte_identical_to_legacy_fixture \
  tests/battery/test_score.py::test_streamed_unrailed_measured_tier_matches_legacy_zero_grid \
  tests/battery/test_score.py::test_admitted_model_derived_rows_emit_residuals_per_imcc_engine
→ 18 passed in ~25.3 s

pytest -n0 tests/battery/test_silent_fills.py \
  -k 'reactive or Re or cell_material or missing_fO2 or graphite or rhenium'
→ 46 passed, 7 deselected in ~2.2 s
```

Symbols touched by FIX (`_mapped_reading_uncertainty_dex`, `_dex_width_from_mapping`, `_figure_only_notice`, `metric_operation_for_identity`) — each defined once under `simulator/battery/score.py` (rg).

---

## D-062 reviewer checklist

1. **Is there a second copy of any logic this change adds or edits, anywhere in the repo?**  
   **No.** `rg` shows single definitions: `metric_operation_for_identity` @ score.py:656, `_mapped_reading_uncertainty_dex` @ :1903, `_dex_width_from_mapping` @ :1934, `_figure_only_notice` @ :2635. Green-merge seat adds no new logic (merge commits only).

2. **Is a rule, threshold or physics computation done in a presentation or wiring layer?**  
   **No.** Band/headline/reactive policy lives in `simulator/battery/score.py` (and Notice contract in `records.py`); report renderers consume payload fields.

3. **Does any new import cross a forbidden layer (or create a cycle)?**  
   **No.** FIX touched `score.py` / `records.py` / battery tests only; this seat’s tip commits are merges of green `05b6309d3` and `8089eadbf` with no new imports from empirical.

4. **Is every behaviour-preserving move backed by a pin committed before the move?**  
   **Yes.** Tests-only pin `75e94a16c` is an ancestor of HEAD; FIX `90d1c55d1` and pregate `c42aca18a` sit after the pin; green merges after FIX.

5. **Did the change relax a guard, add a baseline entry, or move code into a module whose guards are already red?**  
   **No.** Injected-predictor contract restored (stricter than the broken tip). Narrow Notice allow for empty `affected_quantities` is FIGURE_ONLY-only. No baseline entries added in this seat. Store-staleness regen guards were pre-existing review notes, not relaxed here.

---

## Artifacts

- This REPORT  
- `STATUS-b693-b691-scorer-gates-green-merge-2026-10-04.md`  
- Prior: `REPORT-FIX-scorer-gates-review-findings-2026-10-04.md`, `REPORT-impl-scorer-gates-pregate-failures-2026-10-04.md`

**Self-certify LAND?** No — main commissions the review of record.
