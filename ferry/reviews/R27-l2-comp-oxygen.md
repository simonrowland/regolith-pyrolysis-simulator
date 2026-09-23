# R27 — L2 near-ready composition + oxygen (ts1985 / Norris / Ueda / Nakazawa)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`origin/work-v064-green`) ≡ `2e9e17c3d` (L2 base)  
**Commit under review:** `08b28b3b9` on `origin/empirical/l2-near-ready-comp-oxygen-2026-09-22`  
**Stack (base→tip):** `6e9a22a45` O1 C–CO derive → `b4b567cca` ts1985 C–CO token → `a43444177` ts1985 X_Na2O charges → `a44b29479` Ueda N_Co charges → `08b28b3b9` Norris EBT1 + point log fO2  

**Files:**  
- `simulator/chemistry/graphite_c_co.py` (new)  
- `simulator/battery/waypoints.py` (`_c_co_pressure_Pa`, `graphite_c_co_buffer` route)  
- `data/literature/extracts/ts1985.yaml`  
- `data/literature/extracts/norris-2017-earth-volatiles-nature.yaml`  
- `data/literature/extracts/kems-095-ueda-1986.yaml`  
- `data/literature/extracts/kems-169-nakazawa-1976.yaml` (**untouched**)  
- `tests/battery/test_waypoints.py`, `tests/chemistry/test_graphite_c_co.py`  

**Intent:** Close composition + oxygen waypoints for A3 near-ready sources. Land only printed composition maps; C–CO oxygen is DERIVED (O1), never stamped printed. Vacuum KEMS oxygen left GAP. Figure-only compositions refused.

**Attack surface:** invented fO2 for vacuum KEMS; DERIVED stamped printed; wrong mole fractions; Nakazawa figure digitized.

**Method:** Worktree `/workspace/repos/wt/slot-06` at `08b28b3b9`. Static read of O1 route + four extracts vs `2e9e17c3d`. Live `Migrator._migrate_extract` (write=False) on the four sources; first `engine_point` consumer per observation via `engine_point_requests` / `oxygen_condition` / `normalized_composition`. Bit-checked mole-fraction maps and fo2 authority. Pytest focused suite. Mode: **ran-tests**.

Cannot read papers — stamp-integrity and code-path only (EBT1 / Table 2 digits trusted as transcribed).

---

## Findings

No P0 / P1 / P2 on the named claim.

### P3 — flow→block YAML re-emit materializes pre-existing comma-truncated notes

**Evidence (file:line on `08b28b3b9`):**

- `ts1985.yaml:179–181` (and copies on each charge experiment, e.g. `:390–392`) — locator note that was flow-map text `Measurements at 1100, 1200, and 1300 C; …` is stored as `note: Measurements at 1100` plus sibling keys `1200: null` / `and 1300 C; …: null`. Same parse already present when loading green `2e9e17c3d` (flow commas split the mapping); `a43444177` only made it visible in block form.
- `norris-2017-earth-volatiles-nature.yaml:158–159` — same pattern on sweep_gas locator: `note: Flowing CO/CO2 mixture printed` + sibling `but component flow …: null` after the tip’s flow→block expand (`08b28b3b9`).

**Why P3:** Latent source hygiene. Truncated notes do not change migrated `total_pressure_Pa` / fo2 / composition values on this tip (live READY counts match the claim). Not a wrong number today.

**Fix direction (optional):** Quote notes that contain commas before any flow↔block round-trip. Out of scope for landing L2.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Invented fO2 for vacuum KEMS | **Fail (safe).** `kems-095-ueda-1986`: 0 fo2-like keys in extract; live migrate → `oxygen_condition` selected **None** on all 38 obs; no `fO2_control`, no `point_conditions.fO2_*`. Dissolved-O / Y2O3 cell chemistry left as alloy analysis / figure-only (`ueda_1986_fig6_oxygen_vs_N_Co_figure_only`). `kems-169-nakazawa-1976`: file **byte-unchanged** vs base; oxygen None on 5/5. |
| DERIVED stamped printed | **Fail (safe).** ts1985 READY rows select `graphite_c_co_buffer` / **`derived`** only (`waypoints.py:1025–1030`); no `fO2_log`/`fO2_Pa` on any ts1985 `point_conditions` (temperature_K only). Extract token is `buffer: C-CO` with explicit “numeric fO2 is never stamped printed” (`ts1985.yaml:458–472` and per-charge copies). Norris point logs select `observation_fO2_log` / **`printed`** with `inference is None` and values exactly `-7` / `-11` / `-13` (extract `:235`, `:415`, `:478`, `:499`). O1 path never writes a printed log cell. |
| Wrong mole fractions | **Fail (safe) on claimed maps.** ts1985 charges `na2o-sio2-xna2o-{0p40,0p45,0p50,0p55}` land `mole_fraction` Na2O∈{0.40,0.45,0.50,0.55}, SiO2=1−X; matches Table 2 coeff `rows` X_Na2O `{0.4,0.45,0.5,0.55}`. Ueda `ti-co-nco-{0p0…1p0}` land Co=N_Co, Ti=1−N_Co; all 22 Table-2 γ_Ti/γ_Co obs bind to matching `experiment` ids (0 binding mismatches). Norris EBT1 printed wt% map `{SiO2:50.66,…,K2O:0.07}` → `normalized_printed_composition` / derived, Σx=1; NiO=0 and Total omitted as claimed. |
| Nakazawa figure digitized | **Fail (safe).** `git diff 2e9e17c3d...08b28b3b9 -- kems-169-nakazawa-1976.yaml` empty. Obs stay `admission_status: figure_only` / `digitised` not introduced; composition remains string “whole binary” → `unsupported_print_form` on engine_point. |

---

## Live READY (first engine_point consumer / obs)

| Source | READY | Oxygen selected | Composition selected |
| --- | --- | --- | --- |
| **ts1985** | **13/16** | 14/16 `graphite_c_co_buffer` / DERIVED (e.g. 1473.15 K → log10 fO2 ≈ −10.475); 2 umbrella rows lack T | 13/16 `normalized_initial_composition` (per-X charges + SiO2 Gibbs–Duhem on 0p50); coeff table / γ_Na-in-Pb / prose range honest GAP |
| **norris-2017** | **4/6** | 4/6 `observation_fO2_log` / PRINTED (−7/−7/−11/−13); figure3 range + author definition refuse point | 6/6 `normalized_printed_composition` from EBT1 |
| **kems-095-ueda** | **0/38** | 0/38 (vacuum Y2O3 KEMS — unchanged) | **22/38** `normalized_initial_composition` on Table 2 N_Co charges |
| **kems-169-nakazawa** | **0/5** | 0/5 | 0/5 `unsupported_print_form` (refused) |

Matches the branch claim. Series-level Norris `oxygen_partial_pressure_Pa` **interval** (`1.01325e-8`…`1.01325e-2`) removed on tip (was poisoning engine_point); figure4 list `log_fO2: [-11,-13]` split into two point obs (not collapsed).

### What looks sound

- O1 token gate (`graphite_c_co.py:62–88`) + Jakobsson–Oskarsson CCO algebra; always `WaypointAuthority.DERIVED` (`waypoints.py:1028–1029`).
- `_c_co_pressure_Pa` refuses sweep `alternatives` / inventing CO/Ar fractions (`:881–913`); ts1985 falls back to chamber `1.0 atm` → 101325 Pa (`atm_to_Pa` derivation) as P_CO — consistent with extract note 「CO 分圧は 1 atm」.
- Composition ladder: printed mole_fraction / wt% maps only; no figure digitization for Nakazawa or Norris plotted y-values (`digitised: false` retained).
- Validator: ts1985 + ueda + nakazawa **OK**; norris **3 pre-existing** errors unchanged (model_derived type; two fidelity pins).

### Residual notes (not severity)

- `normalized_*_composition` output authority is always DERIVED by construction (`waypoints.py:504–507`) even when the source map is printed — evidence rank still prefers non-inferred; engine_point READY does not require PRINTED on the normalized route.
- Cannot page-check EBT1 / Table 2 digits against PDFs in this review; attack limited to internal consistency and migrator stamps.

---

## Tests

Tip worktree `08b28b3b9`, repo `.venv`, `-o addopts=`:

- `tests/battery/test_waypoints.py` + `tests/chemistry/test_graphite_c_co.py` + `tests/battery/test_printed_fo2.py`: **84 passed**
- Live migrate probes (READY counts, fo2 authority, mole-fraction identity, Ueda/Nakazawa no fo2, binding): **passed / attacks failed**
- `tools/validate_literature_extracts.py`: ts1985/ueda/nakazawa OK; norris 3 pre-existing

No fix patch. Do not push. Do not commit to mailbox.

---

## Verdict rationale

Claim holds under adversarial live migrate: 13/16 and 4/6 READY as stated; Ueda comps land without inventing vacuum fO2; Nakazawa untouched/refused; C–CO stays DERIVED; Norris point logs stay PRINTED with interval poison removed. Named attacks miss. One P3 on YAML note round-trip visibility.

VERDICT: R27 | LAND | P0=0 P1=0 P2=0 P3=1 | ran-tests

READY: ts1985 13/16; norris 4/6; ueda 0/38 (comps 22/38); nakazawa 0/5 (refused)
