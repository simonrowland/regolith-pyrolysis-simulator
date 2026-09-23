# Z13 FIDELITY AUDIT — AQ1 OA PDFs (fedkin / miller / banchor)

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z13 — three extracts that now have AQ1-acquired OA PDFs (fill free seat)  
**Repo / branch:** `regolith-empirical` @ `empirical/z13-aq1-oa-pdf-fidelity-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-r29` (recycled free slot; detached → branch from green)  
**PDFs:** `/workspace/ferry-inbox/acquired/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection (AQ1 OA fill)

| Extract | PDF | Disposition |
| --- | --- | --- |
| `kems-005-fedkin-2006` | `kems-005-fedkin-2006.pdf` (UChicago author OA) | **AUDITED** |
| `kems-019-miller-armatys-2013` | `kems-019-miller-armatys-2013.pdf` (Bentham Open) | **AUDITED** |
| `banchor-matsui-naito-1986` | `banchor-matsui-naito-1986.pdf` (J-STAGE OA) | **AUDITED** |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-005-fedkin-2006 | 14 α obs (+11 fidelity pins) | **ALL 14** (+ full Table 3 this-work 12 cells + Table 4 8 cells + Na/K abstract) | **0** | Table 2 start comps; Yu H₂ α_Na/α_K; Cohen H₂ best-fit α; Fig. 1–13 trajectories figure-only | none |
| kems-019-miller-armatys-2013 | 28 (qualitative review + method window) | **22+** quotes/fields (Al₂O, silicates, borates, SiO map, Over 30, Ti₁₀O₁₉, Ir/2200 K, 10⁻⁵–10 Pa / >2500 K, small-α, Chatillon, Zaitsev Fe–Si–P, Popović PZT, Na₂O–SiO₂ [63], …) | **0** | Suppl. Tables 1–7 true-absence from held article (already declared) | none |
| banchor-matsui-naito-1986 | 1 stub (`Ban86_equations`) | **ALL 1** | **0** (no numeric payload) | Table 1 A/B coeffs now readable in AQ PDF but **not stored**; Tables 3–5 / Figs 1–4; extract note still describes wrong system (VO1.022 / Berkowitz) | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. `pdftotext -layout` (+ `-raw` for Fedkin Table 4) under `/workspace/ferry-inbox/reviews/_z13_audit/{fedkin,miller,banchor}/`.
2. Selective `pdftoppm -png` 150 dpi for Fedkin Table 3 (p.212 / PDF p.7) and Table 4 (p.219 / PDF p.14); Banchor Table 1 page (PDF p.3).
3. Dump all species observations → JSON; cell-by-cell / quote-by-quote vs PDF.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
5. `validate_literature_extracts.py` **not re-run** (no extract edits).

---

## 1. kems-005-fedkin-2006 (Fedkin, Grossman & Ghiorso 2006, GCA 70:206–223)

**PDF:** 18 pp. OA author PDF. Text layer good; Table 3 layout-extractable; Table 4 confirmed on page render.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 5 (`Fe`, `Mg`, `SiO`, `Na`, `K`) |
| Observations | **14** (all `type: alpha`) |
| fidelity_samples | 11 |
| table_metadata | 3 (T1 species list + …) |

### Sample = ALL 14 (+ full T3/T4 grids)

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `fedkin_2006_fe_hashimoto_langmuir_table3` | Table 3 p.212 | Fe this-work **0.23/0.25/0.24/0.23 ±0.02** @ 1973–2273 K; mid pin 0.24 | yes |
| `fedkin_2006_mg_hashimoto_langmuir_table3` | Table 3 | Mg **0.24/0.28/0.28/0.27 ±0.01** | yes |
| `fedkin_2006_sio_hashimoto_table3_complete_b1` | Table 3 | SiO **0.12/0.17/0.20/0.21 ±0.01**; mid 0.17 @2073 K | yes |
| `fedkin_2006_sio_hashimoto_langmuir_table3` | Table 3 | incomplete placeholder; `alpha: null`; superseded by complete_b1 | yes (intentional) |
| `fedkin_2006_*_class_b1` (Fe/Mg/Na/K) | Table 3 / Yu prose | pins 0.24 / 0.24 / 0.26 / 0.13 match source pins | yes |
| `fedkin_2006_t4_mg_this_work_inverse` | Table 4 p.219 | Mg **0.14±0.01 / 0.19±0.01** @2073/2273; tagged `model_output_not_measurement` | yes |
| `fedkin_2006_t4_mg_alexander_2001_inverse` | Table 4 | Mg **0.19 / 0.16** | yes |
| `fedkin_2006_t4_sio_this_work_inverse` | Table 4 | SiO **0.07±0.01 / 0.08±0.01** | yes |
| `fedkin_2006_t4_sio_alexander_2001_inverse` | Table 4 | SiO **0.11 / 0.08** | yes |
| `fedkin_2006_na_yu_langmuir` (+ class_b1) | abstract / §4.2 | α_Na = **0.26 ± 0.05** vacuum @1723 K; range 0.21–0.31 | yes |
| `fedkin_2006_k_yu_langmuir` (+ class_b1) | abstract / §4.2 | α_K = **0.13 ± 0.02**; range 0.11–0.15 | yes |

**Alexander (2004) Table 3 columns** also match the printed quote retained under `corrections[]` (not double-pinned as observations).

### Coverage

- Tables **3–4** (primary α products) fully represented.
- Not extracted (acceptable gaps for this seat): Table 2 starting compositions; Yu H₂ α_Na=0.042±0.020 / α_K=0.017±0.002; Cohen et al. H₂ best-fit α_Fe=0.055±0.010 / α_Mg=0.07±0.05 / α_SiO=0.08±0.01; figure trajectories.

### P0 / P1

- **P0: 0**
- **P1 (latent, not fixed):** Fe `alpha_range` [0.21, 0.27] is the ±σ envelope of this-work means (0.23–0.25), not the bare mean span — documented, not a wrong stored α. Mg class-B1 pin uses 0.24 (1973 K) while per-T band is 0.24–0.28 (note already says so).

---

## 2. kems-019-miller-armatys-2013 (Miller & Armatys 2013, Open Thermodynamics J. 7:2–9)

**PDF:** 9 pp. Bentham Open. Review article — almost no primary numeric vapor-pressure tables in the held PDF; Suppl. Tables 1–7 are true-absence (publisher landing).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 7 (`Al`, `Ca`, `Fe`, `K`, `Na`, `SiO`, `Ti`) |
| Observations | **28** (mostly qualitative `rate_series` / `alpha` statements + quoted supersessions) |
| fidelity_samples | 5 |
| deepening | unsettled (deep3); documents true-absence |

### Sample (≥20)

Quoted / structured fields checked against PDF pages 2, 5, 6, 7, 9:

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| KEMS P window | generally between **10-5 and 10 Pa** | §2.1 p.2 same | yes |
| T ceiling | **above 2500 K** | same | yes |
| Ir cell | Hilpert Ir cells **up to 2200 K** | p.6 same | yes |
| Small α | “small vaporization coefficient, particularly for the refractory oxides” | p.6 same | yes |
| Over 30 glasses | **Over 30** borate/silicate glasses | p.7 same | yes |
| SiO map | SiO(g), O₂/O(g) | p.7 same | yes |
| Gaseous silicates | CaSiO₃, BaSiO₃, SrSiO, AlSiO | p.7 same | yes |
| Al₂O major | Al₂O(g) major Al species | p.7 same | yes |
| Borates | Ca(BO₂)₂, SrBO₂, BaBO₂; ABO₂; A₂(BO₂)₂ | p.7 same | yes |
| Ti₁₀O₁₉ | Ti10O19 among pure oxides | p.6 same | yes |
| Chatillon | silica + metal phosphates activities | p.7 same | yes |
| Steam hydroxides | SiO₂, V₂O₅, TcxOy in H₂O | p.6 same | yes |
| Zaitsev Fe–Si–P | non-metal (B, Si, or P) + Mn, Cr, Fe | p.5 same | yes |
| Na₂O–SiO₂ [63] | title “Thermodynamics of Na₂O-SiO₂ melts” | p.9 biblio (layout `Na2O-SiO 2`) | yes |
| Popović PZT [55] | PbO-ZrO₂-TiO₂ | p.9 biblio | yes |
| Suppl. 1–7 absence | true-absence declared | p.7 / p.8 “Supplementary material is available…” | yes |

### Coverage

Held article has **no primary numeric KEMS tables** — data live in publisher supplementary Tables 1–7 (unseen). Extract correctly refuses to invent table CSVs. Qualitative vapour-species map + method windows are what the OA PDF supports.

### P0 / P1

- **P0: 0**
- **P1: 0** in sampled statements

---

## 3. banchor-matsui-naito-1986 (Banchorndhevakul, Matsui & Naito 1986)

**PDF:** 10 pp. J-STAGE OA. **Identity (AQ1):** correct work is *J. Nucl. Sci. Technol.* **23(10) 873–882**, DOI `10.1080/18811248.1986.9735071` / `10.3327/jnst.23.873`. Sidecar DOI `…9735017` / citation pages **602–611** were wrong (that is a **different** same-year paper by the same authors, cited as ref. (4) inside this PDF).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 1 (`VO_VO2`) |
| Observations | **1** stub `Ban86_equations` (`type: gibbs_table`) |
| Numeric values | **none** (note only: “numeric A,B coefficients were NOT recovered by OCR”) |
| fidelity_samples | 1 (mirrors stub) |

### Sample = ALL 1

Stub claims Table 1 equations over **VO1.022(s)** and **V(s)+VO(s)** with Berkowitz comparison. **AQ PDF Table 1** (p.875) is instead:

> Equations for partial vapor pressures of V(g) and VO(g) over **vanadium-oxygen solid solution**  
> `log(P/Pa) = −A×10⁴/T + B` for O/V = 0.145, 0.083, 0.060, 0.035, 0.001 (+ V extrapolated)

Example cells (PDF render): O/V 0.145 → V A=2.56±0.04 B=13.50±0.40; VO A=2.71±0.08 B=13.50±0.40; T 1860–1973 K. Abstract T band **1855–2117 K**.

**No wrong stored number** — there is no numeric payload. extracts-v2 already marks the observation `unavailable`.

### Coverage / identity (not P0; not invented here)

| Gap | Notes |
| --- | --- |
| Table 1 A/B grid | Now readable in AQ PDF; **not landed** (would be deepening, not a P0 fix) |
| Tables 3–5 / Figs 1–4 | ΔfH / dissociation / oxygen partials — not in extract |
| Citation pages | Extract still says **602–611**; should be **873–882** (AQ1 identity correction) |
| Stub note system | Still says VO1.022 / Berkowitz — describes the *other* 1986 paper, not this PDF |

### P0 / P1

- **P0: 0** (no ledger-reaching number present to be wrong)
- **P1 (latent, not fixed):** wrong citation page range; stale VO1.022/Berkowitz note; empty Table 1 pending a dedicated land/deepen seat

---

## Git

- Branch: `empirical/z13-aq1-oa-pdf-fidelity-2026-09-23` from `origin/work-v064-green`
- Files touched in git: **none**
- **No PDFs in git**
- Push: FF to origin (tip = green base)
- Audit scratch only under `/workspace/ferry-inbox/reviews/_z13_audit/` (not committed)
