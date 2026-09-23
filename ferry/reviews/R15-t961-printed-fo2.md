# R15 — printed per-run log fO2 for four sources (t-961)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `d4f91337f` on `origin/review/t961-printed-fo2` (NOT landed on green; tip of that branch)  
**Files:**  
- `data/literature/extracts/holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml`  
- `data/literature/extracts/sossi-2020-cu-zn-isotope-evap-formalism.yaml`  
- `data/literature/extracts/kems-140-heck-2025.yaml`  
- `data/literature/extracts/thomas-2022-chlorine-bonding-silicate-melts.yaml`  
- `tests/battery/test_printed_fo2.py`  

**Intent:** Holzheid Table 3, Sossi 2020 Table 1, Heck Table 1, and Thomas Table 2 print a numeric log fO2 on each run. Those cells sat under `rows` / `runs`, or (Thomas) in the unscored `context` container, so the migrator never exploded them into per-run points. Tip renames the per-run tables to the `series` key the landing machinery iterates; Thomas Table 2 becomes an observation; each log lands as printed — no buffer conversion, no bar/atm shift.

**Attack surface:** derived stamped printed; unit/frame shift; wrong table key move; range collapsed.

**Method:** Detached worktree at `d4f91337f`. Static read of tip extract diffs + `empty_value_from_payload` / `_emit_exploded_point` / `collect_printed_oxygen` / `_merge_printed_oxygen` on tip `migrate.py` (machinery pre-landed; this commit is extract+tests only). Bit-compared every scalar `log_fO2` / `log10_fO2` / `logfO2` cell green↔tip for the four files. Live migrate of all four sources; inspected exploded `point_conditions`, inferences, waypoint `oxygen_condition`, and value kinds. Fidelity-sample match on all four. Pytest `tests/battery/test_printed_fo2.py`. Mode: **ran-tests**.

Cannot read papers — code-path and stamp-integrity only.

---

## Findings

No P0 / P1 / P2 on the named claim.

### P3 — `_OXYGEN_TABLE_KEYS` still omits historical `runs` spelling

**Evidence (file:line on `d4f91337f`):**

- `migrate.py:5336` — `_OXYGEN_TABLE_KEYS = frozenset({"series", "rows", "points"})` — no `"runs"`.
- `migrate.py:4288–4293` — `empty_value_from_payload` explodes only `values.series` (not `rows` / `runs`).
- Heck tip is the rename that makes explosion work (`kems-140-heck-2025.yaml:176` `runs` → `series`). Pre-tip `runs` never exploded; parent fallback with `skip_tables=True` also would not skip a `runs` list, so disagreeing per-run logs would refuse uniqueness rather than land a collapsed value (safe-fail, not a stamp).

**Why P3:** Latent defense-in-depth. Tip correctly renames the live Heck table. A future extract that reuses `runs` would silently skip explosion again. Not live on this commit’s four tables.

**Fix direction (optional):** Add `"runs"` to `_OXYGEN_TABLE_KEYS` for skip_tables parity, and/or treat `runs` as an alias of `series` in `empty_value_from_payload`. Out of scope for landing this tip.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Derived stamped printed | **Fail (safe).** Every exploded `fO2_log` on the four claimed parents has `inference is None`. Bit-identity green↔tip for all scalar log cells: Holzheid 33, Sossi 116, Heck 81, Thomas 43. C→K temperature lands with typed `celsius_to_kelvin` derivation beside the printed log (Sossi/Thomas); fo2 itself is never rewritten. Heck chamber `inferred: true` 1 atm is refused by `collect_printed_oxygen`. Holzheid experiment buffer is `not_published`; waypoint selects `observation_fO2_log` / `PRINTED` / `-9.63` only. Observation `value` is never the fo2 cell (Holzheid/Heck/Thomas UNAVAILABLE; Sossi isotope_delta UNAVAILABLE — no mapped field). |
| Unit / frame shift | **Fail (safe).** Stored logs equal extract cells (`-9.63`, `-0.68`, `-2.74`, `-7.17`, Heck set `{-0.68,-4.3049,-4.8,-5.3049,-7.3,-7.3144,-9}`, Thomas set `{-7.17,-7.9,-8.2,-6.66}`). No `+ log10(101325/100000)` applied to `fO2_log.state` (R3 contract: annotate, do not rewrite). Sossi units string already says `log10(fO2/bar)`; bare series cells have empty units, allowed by `_OXYGEN_LOG_UNITS`. |
| Wrong table key move | **Fail (safe) on claimed tables.** Holzheid five Table 3 panels `rows`→`series` (33 items → 33 exploded points with fo2). Sossi Cu/Zn Table 1 measured runs `rows`→`series` + `temperature_C`→`T_C` (required: `AXIS_TEMPERATURE_K` accepts `T_C`, not `temperature_C`); fidelity pins updated to `.series[0]` / `T_C`. Heck `runs`→`series` (81 points). Thomas Table 2 hoisted `context`/`experiment_series`/`rows` → `observations`/`gibbs_table`/`series` with `semantics: run_log_not_gibbs_energy` (same parking pattern as Heck T1); fidelity path updated. Residual `rows` elsewhere (Sossi model-derived logK* / zoning; Thomas EPMA `rows_as_printed` / salt standards) are outside the claimed measured-run tables and do not explode — intentional leave. |
| Range collapsed | **Fail (safe).** Per-run logs stay distinct (Holzheid panel0: `-9.63/-9.64/-9.93/...`; Sossi 17 distinct fo2 values; Heck 7; Thomas 4). Sossi alpha regression series carries `logfO2: [-0.68, -3, -5.5]` (list); `_printed_decimal` refuses lists → exploded alpha points get **no** `fO2_log` (band not collapsed to a point). `_unique_oxygen` still refuses disagreeing multi-values. |

---

## What looks sound

- Explosion gate is exactly `values.series` (`migrate.py:4288–4293`, `:7074`); renaming is the minimal lever that opens the R3 printed-oxygen path on `_emit_exploded_point` → `_merge_printed_oxygen(series_item, …)` (`:7345–7351`).
- Exact printed keys only: `_PRINTED_LOG_FO2_KEYS = {log_fO2, log10_fO2, logfO2}` (`:5334`); siblings like `log10_pO2_bar` / buffer tokens are not relabelled printed.
- Thomas hoist is necessary: context never reaches `_emit_exploded_point`. Tip keeps value UNAVAILABLE (`unsupported quantity 'experimental_conditions_and_XAF_parameters'`) and does not stamp CCl / fCl2 / E0 as the observation value or as fo2. No `fO2_Pa` on Thomas points.
- Sossi `T_C` rename is a required companion so temperature lands; fo2 digits untouched.
- New tests pin counts, distinct later-row logs, `inference is None`, waypoint PRINTED, Thomas UNAVAILABLE value, and Heck/Thomas fo2 sets (`test_printed_fo2.py:175–293`).
- Fidelity samples still match on all four extracts.

### Residual notes (not severity)

- Sossi `sossi_2020_*_logKstar_model_runs` still uses `rows` + `temperature_C` with the same printed logfO2 condition cells (model_derived). Out of claim scope; leaving them un-exploded avoids a second fo2 stamp on fitted K* rows.
- Thomas/Heck type `gibbs_table` for run-condition series is a SCHEMA stretch (formation/free-energy label) already established for Heck; mitigated by unsupported `values.quantity` → unknown quantity + UNAVAILABLE value + explicit semantics flag.
- R3 latent issues (`10^n` as log parser; interval overwrite on experiment pO2) are pre-existing on the oxygen machinery and not exercised by these four extracts’ bare numeric cells.

---

## Tests

Tip worktree at `d4f91337f`, repo `.venv` via `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/battery/test_printed_fo2.py`: **9 passed**
- Live migrate probes (four sources, fo2 identity, inferences, waypoint, alpha list-refusal, logK* non-explosion): **passed / attacks failed**
- Fidelity match on four extracts: **OK**

No fix patch. Do not push.

---

## Verdict rationale

The tip does what it claims: rename (and Thomas hoist) so R3’s printed-oxygen collector sees per-run cells; numeric logs are byte-identical to green; nothing derived is stamped `PRINTED`; ranges stay multi-valued or refused. Named attacks miss. One latent P3 on the historical `runs` spelling outside `_OXYGEN_TABLE_KEYS`.

VERDICT: R15 | LAND | P0=0 P1=0 P2=0 P3=1 | ran-tests
