# Y14 — CROSS-AUDIT: N14 misc corpus (BACKLOG 4)

**Lane audited:** N14 (`empirical/n14-misc-2026-09-22`)
**Tip:** `668ba43544f70836ade671872f1850332f3395d5` (unchanged; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N14-misc.md`
**Worktree:** `/workspace/repos/wt/slot-n14` @ tip
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/{lpi-001332,gal-2018-pnas-calcium-storage,marcq-2017-magma-ocean-radiation,busemann-2000-phase-q-noble-gases}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N14 extractor (retry after prior seat failure)

## Verdict

**P0: 0** — every attacked printed number matches the PDF; Marcq model outputs stay typed-absent.
**READY.** Tip unchanged; nothing to push.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 0 |
| P3 | 3 |

## Method

1. Tip `668ba4354`. Re-ran `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py` on all four extracts → `OK: 4 extract file(s) valid`.
2. `sha256sum` of ferry B4 PDFs vs sidecars / extract provenance strings — all four match.
3. `pdftotext -layout` on LPI (cover; Cardiff PDF p26 = pub p14; Pomeroy PDF p59 = pub p47), Gal (full + Materials p6), Marcq (HAL OA full). Busemann ADS scan has empty text layer → `pdftoppm` 200–250 dpi + visual read of p1 / p3 (Table 2 + CSSE) / p4 (Ne-Q + blanks); tesseract repeatedly timed out, so numbers confirmed by direct page/crop reads.
4. Walked every numeric under `fidelity_samples` and `species.*.context[].values` (all four have empty `observations`). Attack focus per N14 follow-up: **Table 2 / 1.17% / 45 eV / Marcq typed-absence leak**.
5. STEP 0 duplicate `rg` across `data/literature/extracts/`.

P0 rule: **P0 only if a wrong number or wrong species lands**. Omission / label coarseness ≤ P3.

## Identity / STEP 0 / PDF ↔ sidecar

| Corpus | PDF identity | Extract | sha256 | Match |
| --- | --- | --- | --- | --- |
| lpi-001332 | *PROGRAM AND ABSTRACTS / LPI Contribution No. 1332*; Space Resources Roundtable VIII, Oct 31–Nov 2 2006, Colorado School of Mines; ISSN 0161-5297; 84 pp | `lpi-001332.yaml` LANDED | `21e5777c58d81ca36b6fc89a1de9bb8bbb2d785cbe5cdc2070b76a594c04ba95` | OK |
| gal-2018-pnas-calcium-storage | Gal et al., *Native-state imaging of calcifying and noncalcifying microalgae reveals similarities in their calcium storage organelles*, PNAS 115(43):11000–11005, DOI `10.1073/pnas.1804139115`; sidecar topic `unrelated-pnas-oa-misfile` | `gal-2018-pnas-calcium-storage.yaml` LANDED (context) | `47384f82da878b0590098d9eaf0d3e7b8f828f665ca62aeb4d6b0a8631a36514` | OK |
| marcq-2017-magma-ocean-radiation | Marcq, Salvador, Massol, Davaille — *Thermal radiation of magma ocean planets…*, JGR Planets 122, 1539–1553, DOI `10.1002/2016JE005224` (HAL OA) | `marcq-2017-magma-ocean-radiation.yaml` LANDED (typed absence) | `035268efebf0a0560fa702556796ced68c04dcc95eea3733c724b703089296ff` | OK |
| busemann-2000-phase-q-noble-gases | Busemann, Baur, Wieler — *Primordial noble gases in "phase Q"…*, MAPS 35, 949–973, DOI `10.1111/j.1945-5100.2000.tb01485.x` (25-page ADS scan) | `busemann-2000-phase-q-noble-gases.yaml` LANDED (context) | `a39d24cb1febb73ef264532439d0e4d21ef15480be734b6a705a6214d122a854` | OK |

**STEP 0:** No prior LPI-1332 / Gal-1804139115 / Marcq-2016JE005224 / Busemann-2000 extracts. `busemann-2024-noble-gases-ryugu-bennu` is a different paper that *cites* this 2000 work — not a duplicate. Sidecar already flags Gal as misfile vs Sossi Cr `10.1073/pnas.1809060115`.

## Per-source audit

### 1. lpi-001332 — PASS

**PDF:** 84-page SRR VIII abstracts. **Landed:** volume identity + Cardiff method + Pomeroy run/mass-loss context; `observations: []`; HSC Fig. 1 / 10–15% O₂ model / Figs 1–2 typed-absent.

| field | landed | printed | match |
| --- | --- | --- | --- |
| lpi_contribution_number | 1332 | LPI Contribution No. 1332 (cover) | OK |
| issn_as_printed | 0161-5297 | ISSN 0161-5297 | OK |
| meeting dates / venue | Oct 31–Nov 2 2006; Colorado School of Mines | same | OK |
| Cardiff simulants | MLS1A, JSC1A, ilmenite | same | OK |
| sam_heritage_power_W_approx | 20 | “SAM requires approximately 20W of power” | OK |
| scaleup_solar_reflector_kW | 9.6 | “9.6 kW solar reflector” | OK |
| Pomeroy outgas | 2 h @ 200 °C vacuum oven | “outgassed for a period of two hours … at 200°C” | OK |
| heating_rate_C_per_min_max | 12 | “no more than 12°C per minute” | OK |
| hold_T_C / hold_duration_minutes | 1400 / 20 | “1400°C and held for 20 minutes” | OK |
| rga | MKS PPT RGA | same | OK |
| **non_condensed_mass_loss_pct** | **1.17** | **“non-condensed mass loss of 1.17%”** (MLS-1A @ 1400 °C) | **OK** |
| vaporized_completely | true | “vaporized completly” [sic] | OK |
| Cardiff Fig. 1 HSC / Pomeroy 10–15% O₂ / Figs 1–2 | TYPED ABSENCE | model / figure_only | OK (not ingested) |

Locator: Cardiff `pdf_page_index: 26` = published p.14; Pomeroy `59` = p.47 — confirmed.

### 2. gal-2018-pnas-calcium-storage — PASS (misfile context)

**PDF:** 6-page PNAS OA biology cryoimaging. **Landed:** identity + cultivation + cryo energies; 0 scored; Figs 1–5 typed-absent.

| field | landed | printed (p6 Materials / body) | match |
| --- | --- | --- | --- |
| doi / pages / vol | 10.1073/pnas.1804139115; 11000–11005; 115(43) | same | OK |
| not_the_sossi_cr_pnas_doi | 10.1073/pnas.1809060115 | distinct DOI | OK |
| P_carterae_growth_T_C | 18 | artificial seawater @ 18 °C | OK |
| C_reinhardtii_growth_T_C | 22 | acetate-phosphate @ 22 °C | OK |
| cryoSXT_tomography_energy_eV | 520 | 520 eV | OK |
| Ca_L pre / peak | 342 / 353.2 | 342 eV / 353.2 eV | OK |
| scan 344–360 @ 0.1 eV | same | “344 eV … 360 eV in 0.1-eV steps” | OK |
| cryoSEM_stage_T_C | -120 | “−120 °C … Ultra 55 SEM” | OK |
| EDS_carbon_coat_nm | 6 | “6 nm of carbon” | OK |
| Figs 1–5 / vapour-rail observables | TYPED ABSENCE | out_of_scope | OK |

### 3. marcq-2017-magma-ocean-radiation — PASS (model → typed absence)

**PDF:** HAL OA 1-D radiative-convective **model** paper. **Landed:** single scope context row; `observations: []`.

| check | result |
| --- | --- |
| DOI / venue / pages | 10.1002/2016JE005224; JGR Planets 122, 1539–1553 — OK |
| document_type | radiative_convective_model_paper_no_lab_measurement_tables — OK |
| Figs 1–13 OLR/ε/Tε/spectra | typed_absence `model_output` — OK (not in values) |
| Table 1 “This study” **280** W/m² NL | typed_absence (explicitly named) — OK (not scored) |
| Abstract Key Points ~280 / cloud ≤40% / Tε ~10% | typed_absence — OK |
| Any numeric 280/282/40 in `values:` | **none** — OK (no leak) |

### 4. busemann-2000-phase-q-noble-gases — PASS

**PDF:** 25-page ADS raster (empty text layer). **Landed:** identity + chem prep + **Table 2** + CSSE bench (45 eV, M/ΔM ~550) + furnace blanks + prose Ne-Q; 0 scored; etch-step tables / figures typed-absent.

| field | landed | printed (visual p3–p4) | match |
| --- | --- | --- | --- |
| six meteorites | Cold Bokkeveld … Dimmitt | abstract list same | OK |
| HF/HCl prep | ≥10 cycles; 10.4 M HF / 1.0 M HCl; 6.0 M HCl; AlCl₃; CS₂/acetone | same (Experimental Procedure) | OK |
| Table 2 Cold Bokkeveld | 9.30 g / 276.9 mg / **3.0%** / etch loss 1.0% | Table 2 row | OK |
| Table 2 Grosnaja | 5.68 / 68.3 / 1.2% / 2.0% | same | OK |
| Table 2 Lancé | 6.78 / 68.5 / 1.0% / 12.4% | same | OK |
| Table 2 Isna | 9.87 / 104.8 / 1.1% / 46.7% | same | OK |
| Table 2 Chainpur | 6.05 / 279.9 / 4.6% / null (“Lost”) | same | OK |
| Table 2 Dimmitt | 25.84 / 482.1 / **1.9%** / null (weighing note) | same | OK |
| line / etchant | Au/Pt; 65%, 14.4 M HNO₃ | concentrated HNO₃ CSSE line | OK |
| charcoal traps | −196 / −115 °C | cryogenic charcoal separation | OK |
| **mass_resolution_M_over_delta_M_approx** | **550** | “approximately 550” | **OK** |
| **ionization_energy_eV** | **45** | “only 45 eV” | **OK** |
| procedure blanks (1e-10) | He4=4; Ne20=0.009; Ar40=30; Kr84=0.0003; Xe132=0.0002 | typical procedure blanks block | OK |
| furnace blanks | 11 / 0.5 / 390 / 0.06 / 0.06 | typical furnace blanks | OK |
| std-gas unc. % | 0.5 / 1 / 4 | He,Ne / Ar / Kr,Xe | OK |
| Grosnaja (²⁰Ne/²²Ne)_Q | 10.66 ± 0.04 | same | OK |
| Lancé | 10.17 ± 0.03 | same | OK |
| Isna weighted avg | 10.16 ± 0.12 | ~10.16 | OK |
| Dimmitt upper limit | ≤10.75 ± 0.05 | “10.75 as an upper limit” | OK |
| Chainpur | 10.60 ± 0.06 | prose Ne-Q summary | OK |
| Cold Bokkeveld (²¹Ne/²²Ne)_Q | ≥0.0278 ± 0.0002 | 0.0278 ± 0.0002 | OK |
| Murchison upper (cited) | 0.0294 ± 0.0016 | 0.0294 upper | OK |

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P3 | gal-2018 | `C_reinhardtii_media_as_printed: acetate-phosphate medium` omits printed **Tris-** prefix (Tris-acetate-phosphate); temperature 22 °C still correct. |
| 2 | P3 | gal-2018 | Strain list omits printed *C. reinhardtii* strain ids cc620 / cw15; genus/species + media retained. |
| 3 | P3 | busemann-2000 | `cold_trap_T_C: -25` matches a printed −25 °C figure in blank-degassing / trap prose; label is slightly coarse vs charcoal (−196/−115) wording — value itself is not a wrong number. |

## Validation

```
/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/lpi-001332.yaml \
  data/literature/extracts/gal-2018-pnas-calcium-storage.yaml \
  data/literature/extracts/marcq-2017-magma-ocean-radiation.yaml \
  data/literature/extracts/busemann-2000-phase-q-noble-gases.yaml
→ OK: 4 extract file(s) valid
```

Commit `668ba4354` adds exactly those 4 files (+728 lines); private PDFs not committed.

## Attack checklist

| Attack | Result |
| --- | --- |
| Wrong Pomeroy mass loss (1.17% vs nearby fig numbers) | Fail — only “1.17%” non-condensed mass loss in Results prose |
| Ingest Cardiff HSC / 10–15% O₂ model as observation | Fail — typed_absence; observations empty |
| Leak Marcq Table 1 / abstract **280** W/m² into values | Fail — only in typed_absences; no 280 in `values:` |
| Wrong Busemann ionization (45 vs 100 eV mention) | Fail — landed 45 eV (“only 45 eV”); 100 eV not landed |
| Wrong M/ΔM (~550 vs nearby) | Fail — ~550 matches |
| Wrong Table 2 yield (e.g. Cold Bokkeveld 3.0 ↔ Dimmitt 1.9) | Fail — all six rows match |
| Force null etch mass-loss to 0 | Fail — Chainpur/Dimmitt remain null + note |
| Swap Gal 18 °C ↔ 22 °C or 342 ↔ 353.2 | Fail — cultivation + Ca L-edge match p6 |
| Treat Gal as Sossi Cr / KEMS | Fail — identity row + not_sossi DOI + out_of_scope typed_absence |
| Validator regressions | Fail — 4/4 OK |

## Push

No tip change. Branch `empirical/n14-misc-2026-09-22` @ `668ba4354` remains as N14 left it.
