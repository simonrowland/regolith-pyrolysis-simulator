# Z1 — FIDELITY AUDIT nasa-cea-thermo — 2026-09-22

**Extract:** `data/literature/extracts/nasa-cea-thermo.yaml` (1615 `gibbs_table` obs / 1615 species)
**PDF:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/nasa-cea-thermo.pdf`
**PDF identity:** NASA/TP—2002-211556 *NASA Glenn Coefficients for Calculating Thermodynamic Properties of Individual Species* (McBride, Zehe, Gordon; Sept 2002), 295 pp
**sha256:** `0ab4154b0fdeac29581f8d047a4cb6836c138c0b41da8f28990ef7d3e2756765`
**Repo:** `/workspace/repos/regolith-pyrolysis-simulator`
**Branch:** `empirical/z1-nasa-cea-thermo-2026-09-22` (from `origin/work-v064-green`)
**Base tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` — **unchanged; no fix commit; no-push**
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** Z1 fidelity seat (BACKLOG 4)

## VERDICT

**PASS — P0=0.** Tip unchanged. No extract patch. **no-push.**

| severity | count | notes |
| --- | ---: | --- |
| **P0** (wrong number stored) | **0** | All 1615 obs match merged `thermo.inp` coefficients / MW / ΔfH / T-segments |
| P1 | 0 | — |
| P2 | 1 | Coverage: 148 condensed species currently selected by `mc2_cea_full_ingest.select_records` are absent from the extract (halide/metal condensed; see Coverage) |
| P3 | 2 | (1) only 1 `fidelity_samples` pin for 1615 obs; (2) locators cite `thermo.inp` records, not TP appendix D page numbers |

## Method

1. Checked out `empirical/z1-nasa-cea-thermo-2026-09-22` @ `origin/work-v064-green`.
2. `pdftotext -layout` → Appendix B (Tables B1/B2 ΔfH°/MW at 298.15 K) + Appendix D (`thermo.inp` listing).
3. Re-parsed repo `data/literature/compilations/nasa-glenn/source/thermo.inp` with `tools.vp_cea_ingest.parse_thermo_inp` + `_merge_same_name_records` (CEA repeats names across λ-transitions).
4. **Full mechanical fidelity:** every extract observation vs merged `thermo.inp` (MW, ΔfH₂₉₈, ref code, every segment Tmin/Tmax + 7 a-coeffs + b1/b2).
5. **Sampled n=30** species for human PDF checks (gases + condensed; alphabet / oxide / condensed spread), plus extended n=62 structural sample.
6. Tmax-aligned Appendix D coefficient compare (ignores known 200→300 K floor retarget in post-2002 `thermo.inp`).
7. Table B1 ΔfH sweep for all extract gases present in B1 (n=1170).
8. Validator: `tools/validate_literature_extracts.py data/literature/extracts/nasa-cea-thermo.yaml` → `OK: 1 extract file(s) valid`.

**Ground truth for P0:** extract declares `source.version: thermo.inp@2026-08-01-cea-sweep` and locators point at `thermo.inp` records. The TP PDF is the 2002 printed snapshot of that file; post-2002 CEA updates are expected PDF↔store diffs, not transcription errors.

## Source shape (why this is not a page-table paper)

| field | value |
| --- | --- |
| `extraction.method` | `tools/vp_cea_ingest.py + tools/mc2_cea_full_ingest.py` from `thermo.inp` |
| observation type | 1615× `gibbs_table` (NASA-9 polynomials) |
| phases | gas 1021 / condensed_solid 303 / condensed_liquid 215 / condensed 76 |
| P° | gases 100000 Pa; condensed mostly 101325 Pa (CEA header convention) |
| units | `NASA CEA polynomial (Cp/R, H/RT, S/R); delta_f_H in J/mol` |

No fO₂ / experimental P/T series — pure standard-state coefficient records.

## Sample ledger (n=30 primary)

Checks: value (ΔfH, MW, coeffs), unit (J/mol), sign, T-segment bounds, P°, mapping (`cea_name` ↔ locator.record), locator presence.

| # | extract key | cea_name | PDF locus | ΔfH vs B1/B2 | App D coeffs (Tmax-aligned) | notes |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | Ag | Ag | B1 + App D p53 | OK 284900 | OK (a3=2.5, b1=33520.0237) | Tmin store 300 vs PDF 200 |
| 2 | AL | AL | B1 + App D | OK 330000 | OK | Tmin drift |
| 3 | Fe | Fe | B1 + App D | OK 415471 | OK | Tmin drift |
| 4 | SiO | SiO | B1 + App D | OK −98842.418 | OK | Tmin drift |
| 5 | SiO2 | SiO2 | B1 + App D | OK −322073.477 | OK | Tmin drift |
| 6 | MgO | MgO | B1 + App D | OK 32261.307 | OK | Tmin drift |
| 7 | TiO2 | TiO2 | B1 + App D | OK −305430 | OK | Tmin drift |
| 8 | FeO | FeO | B1 + App D | OK 251040 | OK | Tmin drift |
| 9 | H2O | H2O | B1 + App D | OK −241826 | OK | Tmin still 200 (matches PDF) |
| 10 | O2 | O2 | B1 + App D | OK 0 | OK | Tmin 200 |
| 11 | CO2 | CO2 | B1 + App D | OK −393510 | OK | Tmin 200 |
| 12 | AL2O3 | AL2O3 | B1 + App D | OK −546890.530 | OK | Tmin drift |
| 13 | CaO | CaO | B1 + App D | OK 38005.308 | OK | Tmin drift |
| 14 | Na | Na | B1 + App D | OK 107500 | OK | Tmin drift |
| 15 | K | K | B1 + App D | OK 89000 | OK | Tmin drift |
| 16 | SO2 | SO2 | B1 + App D | OK −296810 | OK | Tmin drift |
| 17 | CrO | CrO | B1 + App D | OK 186581.318 | OK | Tmin drift |
| 18 | Ni | Ni | B1 + App D | OK 430116.605 | OK | Tmin drift |
| 19 | Mn | Mn | B1 + App D | OK 282400 | OK | Tmin drift |
| 20 | Ar | Ar | B1 + App D | OK 0 | OK | Tmin 200 |
| 21 | AL2O3_a | AL2O3(a) | B2 | OK −1675700 | OK | condensed; P°=101325 |
| 22 | MgO_cr | MgO(cr) | B2 | OK −601600 | OK | |
| 23 | CaO_cr | CaO(cr) | B2 | OK −634920 | OK | Tmin drift |
| 24 | TiO2_cr | TiO2(cr) | B2 | OK −944000 | OK | |
| 25 | Fe_a | Fe(a) | B2 + App D | OK 0 | OK | 4 merged λ-segments |
| 26 | Fe_L | Fe(L) | App D | (B2 liquid row absent in parse) | OK vs thermo.inp | Ref-Elm ΔfH=0 |
| 27 | SiO2_L | SiO2(L) | App D | — | OK vs thermo.inp | |
| 28 | Cr2O3_I | Cr2O3(I) | App D | — | OK vs thermo.inp | 3 merged intervals |
| 29 | (WO3)3 | (WO3)3 | App D | — | OK vs thermo.inp | polymeric gas |
| 30 | FeCL3 | FeCL3 | B1 + App D | **PDF drift** | coeffs follow **new** thermo.inp | see below — **not P0** |

**Extended sample:** +32 more keys (oxides, condensed underscored ids, spread indices) — same pattern; no wrong stored numbers vs `thermo.inp`.

## Full-store mechanical result

| check | result |
| --- | --- |
| extract vs merged `thermo.inp` (1615/1615) | **OK** (MW, ΔfH, ref, all segments/coeffs) |
| Table B1 ΔfH for extract gases in B1 (1170) | **1169 OK / 1 drift (FeCL3)** |
| Tmax-aligned App D coeffs (30-spot) | **all match**; only Tmin 200→300 floor diffs where PDF still prints 200 |

### FeCL3 (PDF vs store — documented drift, not P0)

| | PDF TP-2002 (B1 / App D) | extract / current `thermo.inp` |
| --- | --- | --- |
| ΔfH₂₉₈ | −1059.104 kJ/mol (−1059104 J/mol) | **−253130 J/mol** |
| citation | Chase,1998 p879 | **Hwang & Rabinowitz 2019** |
| ref code | j… | **h10/19** |

Store matches the claimed `thermo.inp` evaluation. This is a post-2002 CEA database revision, not a digitization error.

### Tmin floor 200 → 300 K

Widespread for many species in today’s public `thermo.inp` (ingest code comments cite Snyder 2021 T-range floor). Coefficients and ΔfH still match the PDF when segments are aligned on Tmax. **Not P0.**

## Coverage

**In scope and present (regolith-critical condensed):** AL2O3(a/L), MgO(cr/L), CaO(cr/L), SiO2(a-qz/b-qz/L), TiO2(cr/L), Fe.947O(cr/L), Fe2O3(cr), Fe3O4(cr), Fe(a/L), Si/AL/Ca/Ti metals, Cr2O3, Ni, etc.

**Intentional / filter absences (not missing-from-paper bugs):**
- Ions (Ag+, Fe+, …) — MC2 `is_ion` skip
- D/T isotopologues — isotope skip
- Most non-feedstock condensed

**P2 coverage gap:** `select_records` today would select **148 additional condensed** species (Ag/Cu/Ba/Cs/Li/… elemental + halide condensed) that are **not** in the extract. Phases: 79 solid / 65 liquid / 4 other. Main gas Table B1 non-ion rows that look “missing” are mostly ions already filtered, D-isotopologues, Air/JP-10, or condensed listed in B1’s gas table by TP layout quirks. **No main B1 gas ΔfH row for a landed gas species is missing from the extract** except the documented FeCL3 *value update*.

Filling the 148 is an ingest-completeness task, not a wrong-number fix — left unpatched in Z1.

## Validator / branch

```
OK: 1 extract file(s) valid
```

Branch `empirical/z1-nasa-cea-thermo-2026-09-22` points at base tip `2e9e17c3d` with **no new commits**. PDF never staged.

## Report line

**sampled n=30 (primary) / 62 (extended) / 1615 (full thermo.inp); P0=0; tip=no-push** (`2e9e17c3d138fdfba9269c493f82974a71153fa5`)
