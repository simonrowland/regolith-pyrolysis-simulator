# Z21 FIDELITY AUDIT — kems-190-wu-1993 / wilkerson-2021-jsc1a-tga-ms-poster

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z21 — BACKLOG 5, last 2 remaining extracts of Z13–Z42 B5 PDF set  
**Repo / branch:** `regolith-empirical` @ `empirical/z21-wu-wilkerson-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z21`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**CLAIM:** `/workspace/ferry-inbox/reviews/_z21_audit/CLAIM.txt`  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

Assigned pair (obs count on `data/literature/extracts/*.yaml`; both <20 → sample = ALL):

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `kems-190-wu-1993` | 16 (+7 fidelity pins) | **AUDITED** (ALL) |
| `wilkerson-2021-jsc1a-tga-ms-poster` | 17 (+3 fidelity pins) | **AUDITED** (ALL) |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-190-wu-1993 | 16 | **ALL 16**; Table 1 **all 14 rows / 73 numeric cells**; Table 2 **all 84 γ cells**; Table 3 **all 12 cells**; eqs 6–11 coeffs; both peritectics | **0** | Figs 1–11 phase/activity diagrams figure-only (declared) | none |
| wilkerson-2021-jsc1a-tga-ms-poster | 17 | **ALL 17**; Conclusions table **ALL 3×(value±unc)**; release windows; thermal programs; hypothesis table; figure-only admits | **0** | TG/MS curve coordinates figure-only; JSC-1A composition not printed; vacuum absolute-P figure axis only | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. CLAIM first at `_z21_audit/CLAIM.txt`; worktree `slot-z21` @ green base `2e9e17c3d`.
2. `pdftotext -layout` → `_z21_audit/pdf_text/{wu-1993,wilkerson-2021}.txt`.
3. `pdftoppm` 150–300 dpi for Wu pp.28/32/33/34 (PDF 3,7–9) and Wilkerson pp.4–8,11; visual Read + selective crops.
4. Dump all species observations → `_z21_audit/json/*_obs.json`; cell-by-cell / quote-by-quote vs PDF.
5. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
6. `validate_literature_extracts.py` **not re-run** (no extract edits).

---

## 1. kems-190-wu-1993 (Wu, Eriksson, Pelton & Blander 1993, ISIJ Int. 33:26–35)

**PDF:** 10 pp. J-STAGE scan (HyperGEAR). Journal pages 26–35 = PDF pages 1–10. Text layer OCR noisy; tables checked on 300 dpi PNG.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 7 (`MgO`, `FeO`, `SiO2`, `Mg2SiO4`, `Fe2SiO4`, `MgSiO3`, `FeSiO3`) |
| Observations | **16** |
| fidelity_samples | 7 |
| corrections | 13 (Bowen & Schairer 1935 cite; FeO construction admissions) — not re-audited as P0 source |

### Sample = ALL 16 (+ table grids)

#### Table 1 (p.28) — thermodynamic properties vs elements @ 298.15 K

All **14 phase rows** A/B + Cp coeffs matched image (cal, note 1 cal = 4.184 J):

| Phase (T/K) | A | B | Spot coeffs | OK? |
| --- | ---: | ---: | --- | --- |
| MgO(l) 298–3098 | **−130340.58** | 6.4541207 | a 17.398557; b −0.751; d −70.79326; e 0.013968958 | yes |
| MgO(s) 298–3098 | −143761.95 | 6.4415388 | a 14.605557; c −1.4845937 | yes |
| FeO(l) 298–1644 / 1644–5000 | −56081.06 / −59216.32 | 18.753713 / 13.977535 | a −4.3079527 / 16.3 | yes |
| FeO(s) 298–1644 / 1644–5000 | −63535.43 / −66670.69 | 14.219836 / 9.4436587 | same Cp pattern | yes |
| SiO2(l) 298–1996 / 1996–5000 | −214339.36 / −221471.21 | 12.148448 / 2.3702523 | a 19.960229 / 20.5 | yes |
| SiO2(tr)/(cr) | −216788.99 / −216629.36 | 10.880425 / 11.001147 | tr a 18.0145; c −14.240189 | yes |
| Mg2SiO4(s) 298–2161 | **−520482.62** | 22.468913 | a 57.036654; d −478.31286; e −0.27782811 | yes |
| Fe2SiO4(s) 298–2200 | −354104.43 | 36.073145 | a 59.495249; d −459.81238 | yes |
| MgSiO3(s) 1257–1830 | −368906.91 | 16.117975 | a 39.813456; d −286.94742 | yes |
| FeSiO3(s) 298–1900 | −285462.23 | 22.916628 | a 40.406448; c −5.0127103 | yes |

Automated compare: **73/73 numeric Table-1 cells OK; 0 mismatches.**

#### Table 2 (p.32) — RT ln γ (J mol⁻¹), liquid standard state

| Block | X rows × T cols | Result |
| --- | --- | --- |
| MgO–SiO2 / γ_MgO | 8 × 3 (absent/paren retained) | **24/24 OK** (pin X=0.4 @ 2000°C = **−30770**) |
| MgO–SiO2 / γ_SiO2 | 8 × 3 | **24/24 OK** |
| FeO–SiO2 / γ_FeO | 6 × 3 | **18/18 OK** (incl. X=0.4 @ 1800°C = **−2107**) |
| FeO–SiO2 / γ_SiO2 | 6 × 3 | **18/18 OK** |

Parenthetical temperature columns recorded in `parenthetical_temperature_columns_as_published` (not stripped).

#### Table 3 (p.33) — solidus Fe2SiO4 wt% along olivine join

| T °C | Exp | Calc | OK? |
| ---: | ---: | ---: | --- |
| **1495** | **41** | **40.5** | yes |
| 1465 | 45 | 45 | yes |
| 1440 | 47.5 | 49 | yes |
| 1410 | 54 | 54 | yes |
| 1318 | 70 | 71.5 | yes |
| 1276 | 80 | 81 | yes |

#### Peritectics + binary model parameters

| Observation | Key numbers vs PDF | OK? |
| --- | --- | --- |
| `wu_1993_reported_ternary_peritectic` | **1306°C**; liq 9/46/45 MgO/FeO/SiO2; ol 68/32 Fe2SiO4/Mg2SiO4; px 62/38 FeSiO3/MgSiO3; cite Bowen & Schairer **1935** [16] | yes |
| `wu_1993_calculated_ternary_peritectic` | **1310°C**; liq **10.5 / 45 / 44.5**; ol **61** Fe2SiO4; px **50** FeSiO3 (p.34) | yes |
| eq 6 solid | gE = **10175** XFeO XMgO | yes |
| liquid ω FeO–MgO | **3347** J mol⁻¹ | yes |
| eq 7 ω MgO–SiO2 | **−86090 −48974 Y + 328109 Y^7** | yes |
| eq 8 η | **−37.656 Y + 125.52 Y^7** | yes |
| eq 9 ω FeO–SiO2 | **−17697 −38528 Y + 842570 Y^5 −1549201 Y^6 + 962015 Y^7** | yes |
| eq 10 η | **−16.736 + 62.76 Y^7** | yes |
| eq 11 pyroxene | gE = **−6694** XFeSiO3 XMgSiO3 | yes |

### Coverage

- Optimized pure-component Table 1, binary Table 2 γ grids, Table 3 solidus reprint, reported+calculated ternary peritectic, and eqs 6–11 represented.
- Figs 1–11 phase diagrams / iso-activity curves correctly left figure-only (`figures_not_digitized`).

### P0 / P1

- **P0: 0**
- **P1 (latent):** none material. Scan OCR of body text is noisy; extract used visual table reading (declared). FeO “hypothetical stoichiometric” construction already admitted as `model_derived` via corrections ledger.

---

## 2. wilkerson-2021-jsc1a-tga-ms-poster (NASA LSSW poster, NTRS 20210020017)

**PDF:** 12-page born-digital workshop poster. Only printed numeric yield table is Conclusions (pdf page 11).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 4 (`H2O`, `CO2`, `SO2`, `JSC-1A`) |
| Observations | **17** (10 measured / 5 figure_only / 2 hypothesis-class) |
| fidelity_samples | 3 |

### Sample = ALL 17

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_h2o_total_loss_wt_pct` (+pin 0.16) | p.11 Conclusions | **0.160 ± 0.014**; caption “Total gas evolutions of non-lunar mineral decomposition in JSC-1A” | yes |
| `…_co2_total_loss_wt_pct` (+pin 0.12) | p.11 | **0.120 ± 0.013** | yes |
| `…_so2_total_loss_wt_pct` (+pin 0.079) | p.11 | **0.079 ± 0.011** | yes |
| `…_h2o_release_window_50_200_C` | p.4 | **H2O @ 50–200°C**; vacuum-equivalent note; MS&lt;500°C lost | yes |
| `…_co2_release_window_500_750_C` | p.4 | **CO2 @ 500–750°C** | yes |
| `…_so2_release_window_1000_C_plus` | p.4 | **SO2 @ 1000°C+**; open T_range_K not invented | yes |
| `…_he_tga_ms_*_page4_figure_only` (H2O/CO2/SO2) | p.4 | caption **He, 2.5°C/min, 1050°C hold, +MS**; amu 18/44/64; admitted figure_only | yes |
| `…_vacuum_ms_h2o_page5_figure_only` | p.5 | caption **vacuum, 2.5°C/min, 1200°C hold, +MS***; H2O/N2/O2/CO2/SO2 traces; figure_only | yes |
| `…_jsc1a_he_thermal_program` | p.4 | He; ramp **2.5**; hold **1050** | yes |
| `…_jsc1a_vacuum_thermal_program` | p.5 | vacuum; ramp **2.5**; hold **1200**; MS&lt;500 lost | yes |
| `…_jsc1a_arh2_thermal_program` | p.6 | **Ar/5H2**; 2.5; 1050; H2O above **800°C** with H2 consumption | yes |
| `…_jsc1a_arh2_750C_2hr_hold_mass_loss` | p.8 | hold **750°C**; **2 hr**; **0.45–0.50 %**; atmosphere text **Ar/H2** (as bullet) | yes |
| `…_idealized_ht_tg_page8_figure_only` | p.8 | He then **Ar/5H2** panels; figure_only | yes |
| `…_suspected_reactions_hypothesis` | p.7 | inert/vacuum + H2 tables; quote “not necessarily all species”; stores printed **CaS2** (poster typography); Sohn/Tian/Kim cites | yes |
| `…_vacuum_bubbling_above_1100_C_qualitative` | p.11 | “in excess of **1100°C**”; also expected in lunar regolith; not_a_yield | yes |

### Coverage

- Only printed numeric yield table fully stored; release windows and thermal-program captions stored; TG/MS curves correctly figure-only; suspected-reaction table labelled hypothesis; composition table true-absence.

### P0 / P1

- **P0: 0**
- **P1 (latent — extract descriptive string, not numeric payload):**
  1. Vacuum page-5 upper plot axis prints **Pressure (10⁻⁵ Torr)**; figure_only `plotted_as_printed` / `absolute_pressure` note say **10^-3 Torr**. Axis was not digitised; no numeric pressure value is stored for ledger use. Latent transcription of the axis exponent only.
  2. Suspected first-reduction product printed as **CaS2** (chemically odd vs CaS in cited lit); extract faithfully quotes poster — correct fidelity, not a silent “fix”.
  3. Page-8 bullet says **Ar/H2** while figure band says **Ar/5H2**; extract keeps bullet string on the mass-loss obs and figure label on the figure_only obs.

---

## Push

```
git push -u origin HEAD:empirical/z21-wu-wilkerson-2026-09-23
```

**Tip SHA:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (= `origin/work-v064-green`; no fix commit required)  
**Force-push:** no  
**PDFs in git:** no  
**Validator:** not re-run (no edits)
