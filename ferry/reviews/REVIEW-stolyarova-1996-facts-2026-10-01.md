# REVIEW — printed facts: review/stolyarova-1996-facts

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z15` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `d445bb6fe29436f924bb73e4d08f37566c5c4cec` (parent green `91567188d58ff4ecc091e76c47884285a5747f45`)
- **Commit:** `Record Stolyarova 1996 phase and apparatus evidence` — extract + regenerated extracts-v2 sibling only
- **Date:** 2026-10-01 ~17:32 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Policy gate:** `POLICY-regolith-main-2026-10-01-use-values-first.md` — wrong-number guards; do not invent data; d-032 Option A (no equipment FK from extract)
- **Corpus:** `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1996/` (PDF + MinerU OCR + `_audit/`)
- **Prior:** `REVIEW-stolyarova-1996-delta3-2026-10-01.md` (LAND `7f66b915a…` TEST-ONLY pin)
- **REQ:** `REQ-review-stolyarova-1996-facts-d445bb6f-2026-10-01.md` — regen confirm 130/33/0; sibling byte-equal; quote/locator guards; why-33

## Tip shape

| ancestor | role | present |
| --- | --- | --- |
| `91567188d` | green (parent) | **yes** (= tip^) |
| `d445bb6fe` | ONE commit (facts) | **yes** |

`git diff --stat 91567188d d445bb6fe` → **2 files**, both under this source only:

- `data/literature/extracts/stolyarova-1996-cao-alumina-silica-kems.yaml`
- `data/literature/extracts-v2/stolyarova-1996-cao-alumina-silica-kems.yaml`

No other extracts / works / tests. **PASS.**

## Attack (1) — single-source regen; sibling byte-equal

Canonical lift: `Migrator(ROOT)._migrate_extract(<this source>.yaml)` then `dump_yaml` of that source’s observations (same dump path the full migrator uses for extracts-v2). Full `scripts/battery_migrate.py` not run (rewrites whole store; VPS constraint).

| check | result |
| --- | --- |
| Live regen obs count | **312** (163 `p_partial` + 137 `activity` + 6 `ion_intensity` + 6 unavailable) |
| Committed sibling SHA-256 | `d990d4a984227a6f4f44d01f9b9a6405e1312edeeacbd664908260971df8cc64` |
| Live regen SHA-256 | **identical** |
| `cmp` / byte-equal | **yes** (1 446 455 bytes) |

**Sibling byte-equal: YES.** **PASS.**

## Attack (2) — store-side census 130 / 33 / 0

Live migrate at tip + `effusion_regime_unverified` on all 163 `p_partial` rows (experiment method = Knudsen effusion; Ag calibration grounded):

| bucket | count | notes |
| --- | ---: | --- |
| Pressure rows | **163** | Ca 30 + Al 23 + AlO 10 + SiO 30 + SiO2 15 + O 55 |
| Identity / species-phase unknown | **0** | all 163 `species.phase` = `value: g` |
| O reaction missing | **0** | all 55 O rows carry reaction + `reaction_as_printed` |
| Effusion refuse | **33** | `effusion_regime_unverified` |
| Effusion pass (would reach engine) | **130** | 163 − 33 |
| Activity rows | **137** | all still `reference_state` unknown (physical phase of pure-oxide standards not printed) |

**Confirmed: 130 / 33 / 0.** Matches STORE-SIDE claim. **PASS.**

### Why the 33 still refuse (one primary check)

**Check:** `effusion_regime_unverified` → primary `in_cell_partial_pressure_sum` (route `in_cell_fallback`).

**Printed facts missing (so Kn / diameter / background routes cannot pass):** orifice Knudsen number not printed; orifice diameter / area ratio typed `not_published`; chamber/background `total_pressure_Pa` typed `not_published`. Calibration *is* grounded (Ag vapor-pressure standard + ion-current comparison), so the gate falls through to the in-cell partial-pressure-sum fallback (Drowart et al. 2005 ~10 Pa ceiling; no defensible orifice diameter).

**Among the 33:**

| sub-reason | n | detail |
| --- | ---: | --- |
| Incomplete printed species coverage (sum only a lower bound; missing Al at that composition) | **25** | cannot verify Σp ≤ 10 Pa |
| Σp exceeds 10 Pa at one composition | **8** | same-point sum **11.0504 Pa** (SiO ~11.04 Pa dominates); Ca/Al/AlO/SiO/SiO2 + three O rows at that composition |

Nothing in this tip can clear those 33 without inventing orifice geometry or background pressure the paper does not print. **Expected residual.**

## Attack (3) — no measured value / unit / scale / condition / derived_from change

v1 extract tip vs green `91567188d` on all 25 observation bundles:

- Pressure point totals **163**; activity **137** — unchanged.
- `values.points` / quantity / units / scale / `T_range_K` / `derived_from`: **0 diffs** on pressure + activity.
- Annotation-only changes (phase → `gas`; reaction / reaction_as_printed / standard_state / reservoir unknowns; locator notes; apparatus MI 1201 wording; calibration reference_substance; pressure_environment not_published blocks) are the point of this tip.

**Values: PASS.**

## Attack (4) — wrong-number / quote+locator guards (PDF + MinerU OCR pp. 16–19)

| claim | locator | page evidence | result |
| --- | --- | --- | --- |
| Curved brackets = gas; square = condensed | p. 16 footnote | OCR: “chemical formulae enclosed in curved brackets correspond to components in the gas phase, whereas those in square brackets refer to those in the condensed phase.” | **PASS** |
| Applies to Table 2 species behind the 87 phase rows | Table 2 = vapor partial pressures (P_Ca, P_Al, P_AlO, P_SiO, P_SiO2) | Footnote is paper-wide; Table 2 columns are gas-phase partial pressures of those species; tip types `phase: gas` on all five measured pressure bundles (108 pts; 87 were the green identity-phase bucket after excluding the 21 measured rows already in the effusion partition) | **PASS** |
| Eq. (4) `(MoO3) = (MoO2) + (O)` → p_O | p. 18 | OCR Eqs 4–6 block; extract `reaction_as_printed` + note on `stolyarova_1996_table2_pO_1933k` | **PASS** |
| Eq. (5) `(AlO) = (Al) + (O)` → p′_O | p. 18 | attached to `…_pO_prime_1933k` | **PASS** |
| Eq. (6) `(SiO2) = (SiO) + (O)` → p″_O | p. 18 | attached to `…_pO_double_prime_1933k` | **PASS** |
| Gas reference / reservoir left unknown | O rows | `standard_state` / `reservoir` tagged unknown “not printed” — correct; paper does not print p° / reservoir | **PASS** |
| MI 1201 apparatus | p. 17 | “magnetic mass spectrometer model MI 1201 (produced at the Electron Microscope Plant, Sumy, Ukraine), which was modified…” | **PASS** |
| Ion current comparison + Ag vapor-pressure standard | pp. 17–18 | “were obtained by the ion current comparison method”; “The vapor pressure of silver was used as a standard.” | **PASS** |
| Orifice / background / sweep not printed | p. 17 Experimental | typed `not_published` with locators — no invented dimensions | **PASS** |
| Activity standards over individual oxides at 1933 K | p. 19 Table 1 caption | “Over Individual Oxides CaO, Al₂O₃, and SiO₂ at 1933 K”; physical phase left unknown | **PASS** |
| Mo cell inference untouched | benches | still inferred Mo from prior LAND; not claimed printed | **PASS** |
| No false O lineage parents | O blocks | `derived_from` unchanged empty vs green (POLICY: incomplete lineage not blocking) | **PASS** |

Nothing recorded as printed that is absent from the page. **PASS.**

## Attack (5) — scope / invent-data / VPS

- Diff confined to the two Stolyarova YAML paths.
- No equipment FK invented (d-032 Option A).
- Full W3 / pytest suites not run; single-source migrate + effusion gate + v1 value diff + OCR quote checks only.

## P0 / P1 / P2

- **P0:** none
- **P1 (wrong-number):** none
- **P2:** none blocking. Non-blocking note: 33 effusion refusals remain by design (missing orifice/background; 8 rows also Σp > 10 Pa); 137 activity rows still reference-state unknown (physical phase of pure-oxide standards not printed).

VERDICT: LAND d445bb6fe29436f924bb73e4d08f37566c5c4cec

— regolith-empirical
