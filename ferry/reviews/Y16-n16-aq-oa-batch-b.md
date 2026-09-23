# Y16 — CROSS-AUDIT: N16 AQ-OA batch B (regolith-empirical)

**Lane audited:** N16 (`empirical/n16-aq-oa-batch-b-2026-09-23`)
**Tip:** `db0facdc727fb279d7ced01f02a9e73016c70c13` (unchanged; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N16-aq-oa-batch-b.md`
**Worktree:** `/workspace/repos/wt/slot-n16` @ tip
**PDFs:** `/workspace/ferry-inbox/acquired/{lpsc-2024-bennu-pyrolysis-vandam,metsoc-2019-6005,ntrs-19730008085,ntrs-20250004626}.pdf`
**Date:** 2026-09-23 (America/Toronto, EDT)
**Auditor:** distinct subagent from N16 extractor (blind adversarial)

## Verdict

**P0: 0** — every attacked printed number matches the PDF; MetSoc Table 1 mol% and Yu FactSage stay typed-absent (no leak into scored values).
**READY.** Tip unchanged; already on `origin/empirical/n16-aq-oa-batch-b-2026-09-23` (= `db0facdc7`). Nothing to push.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 1 |
| P3 | 3 |

## Method

1. Tip `db0facdc7`. Re-ran `/workspace/repos/wt/slot-n16/.venv/bin/python tools/validate_literature_extracts.py` (+ `--check-fidelity-match`) on all four → `OK: 4 extract file(s) valid` twice.
2. `sha256sum` of ferry acquired PDFs vs extract `extraction.method` provenance strings — all four match.
3. `pdftotext -layout` full texts under `/workspace/ferry-inbox/reviews/_y16_pdf_text/`; `pdftoppm` 200 dpi of Manuel PDF pp.35–38 under `_y16_png/` (visual Table 1 + Table 4).
4. Walked every numeric under `fidelity_samples` and `species.*.observations|context[].values` (including nested `rows`). Attack focus: Manuel Table 1 / Table 4 grids; Yu Tables 1–5; MetSoc ΔfH/ΔfS/ΔHfus; Mojarro setpoints; typed-absence leaks (MetSoc Table 1 mol%; Yu FactSage).
5. STEP 0 `rg` across `data/literature/extracts/` + parallel `mre_measurements.yaml` check for Yu DOI.

P0 rule (X1–X9 / Y calibration): **P0 only if a wrong number or wrong species lands**. Locator coarseness ≤ P2; naming / trailing-zero soft / citation rebuilt from DOI ≤ P3.

## Identity / STEP 0 / PDF ↔ provenance

| Corpus | PDF identity | Extract | sha256 | Match |
| --- | --- | --- | --- | --- |
| `lpsc-2024-bennu-pyrolysis-vandam` | Mojarro et al., *Early OSIRIS-REx… pyrolysis… Bennu*, 55th LPSC (2024) **#1219** (1 p.; internal title `1219.PDF`). **No Van Dam author** — stem mislabel | `lpsc-2024-bennu-pyrolysis-vandam.yaml` LANDED (context; 0 scored) | `9444343099d69f6e02b8671e679d20a118e59b03678bcd4d5fe727cbc008d69b` | OK (= extract provenance); citation rebuilt from PDF p1 |
| `metsoc-2019-6005` | Shornikov & Yakovlev, *Mass spectrometric study of perovskite evaporation from Knudsen cell*, MetSoc 2019 #6005 (LPI Contrib. 2157) | `metsoc-2019-6005.yaml` LANDED (3 scored) | `c09c421f62accbed788a44e15e17c42caed290a500c45503d154a44d7efe3c67` | OK |
| `ntrs-19730008085` | Manuel, Srinivasan, Hennecke, Sinclair, *Noble Gases in the Moon*, NASA-NGR-26-003-057 annual report, Sept 1972 (84-page scan) | `ntrs-19730008085.yaml` LANDED (2 scored) | `e4d432b54a2a59968e3626f091a976bd6a9c7e92123fbf9d025833551ea04b79` | OK |
| `ntrs-20250004626` | Yu et al., *Improving molten regolith electrolysis with zirconia-based hollow anode technology* (NTRS OA / accepted MS, 40 pp.; authors Yu/West/Stokes/Harder/Reidy/Dominguez/Faber). DOI `10.1016/j.actaastro.2025.06.028` | `ntrs-20250004626.yaml` LANDED (5 scored) | `ba46da63295be1cf1774bc268b6a63b632a199a61f1f8d663449676bc96b3ab5` | OK PDF identity; journal vol/pages in citation not printed on held NTRS face (see P3) |

**STEP 0:** No prior Mojarro / LPSC-2024-1219 / OREX-500002, MetSoc-2019-6005 / perovskite Knudsen CaTiO₃, Manuel / NGR-26-003-057 / 15601.64, or `literature_extract` for DOI `10.1016/j.actaastro.2025.06.028`. Hits on `kems-015` / `kems-019` / `lebrun-2013` are incidental “perovskite” mentions — different papers. Parallel `observations-v2/mre_measurements.yaml::yu_2025_hollow_anode_measurements` cites same DOI — MRE store, not extract duplicate (N16 note correct).

## Per-source audit

### 1. `lpsc-2024-bennu-pyrolysis-vandam` — PASS (context-only)

**PDF:** 1-page LPSC abstract. **Landed:** sample IDs + pyrolysis setpoints + instrument/derivatization + QL vs Murchison relative signal; `observations: []`.

| field | landed | printed (p1) | match |
| --- | --- | --- | --- |
| authors / abstract # | Mojarro…Lauretta; 1219 | same | OK |
| QL / TAGSAM IDs | OREX-500002-0 / OREX-800031-0 | same | OK |
| subsample mass / count | ~1 mg; 2 | “Two ~1 mg subsamples” | OK |
| standard pyrolysis T | 600 °C | “standard pyrolysis at 600°C” | OK |
| wet-chemistry pyrolysis T | 250 °C | “wet chemistry pyrolysis at 250°C” | OK |
| pyroprobe / GC / MS | CDS 6200 / TRACE 1600 / TSQ 9610 | same | OK |
| MS modes | MRM + full scan 50–500 m/z | same | OK |
| silylation | MTBSTFA/DMF (4:1); 5 µL; 85 °C / 1 h; capped 2 mL vial | same | OK |
| QL alanine+glycine vs Murchison | “more than 100x” | “more than 100x relative to … Murchison for alanine and glycine” | OK |
| PAH / AA detection lists | not scored | qualitative only | OK |

### 2. `metsoc-2019-6005` — PASS (measured thermo; Table 1 absent)

**PDF:** 1-page MetSoc abstract. **Scored:** 3 (`gibbs_table` ×2 + `transition_point`).

| field | landed | printed | match |
| --- | --- | --- | --- |
| ΔfH | −39.88 ± 0.54 kJ/mol | “‒39.88±0.54 kJ/mol” | OK |
| ΔfS | 3.15 ± 0.28 J/(mol·K) | “3.15±0.28 J/(mol×K)” | OK |
| melting T / ΔHfus | 2241 ± 10 K; 47.61 ± 1.84 kJ/mol | same; “per 1 mol compound” | OK |
| T windows / cell | 1791–2241 K perovskite; 2241–2441 K melts; Mo Knudsen; Hertz–Knudsen | same | OK |
| vapor species list | Ca, CaO, Ti, TiO, TiO₂, O, O₂, CaTiO₃ | same (Fig. 1 caption) | OK |
| **Table 1 gas-phase mol%** | **TYPED ABSENCE** (`model_output`) | “calculated from the values of the oxide activity” | OK — no mol% cells in `observations` |
| Figs 1–2 | TYPED ABSENCE (`figure_only`) | curves only | OK |

### 3. `ntrs-19730008085` — PASS (Table 1 + Table 4 full grids)

**PDF:** scanned annual report. **Scored:** 2× `concentration_series` (Appendix I Table 1 He/Ne/Ar stepwise; Table 4 cumulative % for 15601.64). Visual confirm on `_y16_png/manuel-35.png` / `manuel-38.png`.

**Fidelity pins:** 700 °C ⁴He content **450000** ×10⁻⁸ cc STP/g; 700 °C ⁴He cumulative **79.388%** — both match PDF.

**Table 1 (all 15 T-rows + Air):** isotope-ratio strings and He4/Ne22/Ar36 contents match visual/OCR (0 mismatches in programmatic compare). Dashed Ar38 @ 100–200 °C correctly omitted. Sample mass **0.9861 g**; hold **30 min**; 100–1500 °C / 100 °C steps match methods prose.

**Table 4 (all 15 T-rows):** He4/Ne22/Ar36/Kr84/Xe132 cumulative % match visual grid (0 mismatches), including 100 °C Ar36 **0.000021** (= `2.1e-05`) and 700 °C block 79.388 / 69.521 / 7.621 / 5.114 / 1.763.

**Typed absences:** Tables 2–3 (Kr/Xe) OCR unreliable — confirmed garbled text layer; Appendix II/III out of lane — OK.

| soft | detail |
| --- | --- |
| **P2** | Method context locator `pdf_page_index: 8` is wrong: PDF p8 is Appendix I title only. Sample mass **0.9861 g** prints on **PDF p12**; “thirty minutes” / Ti getters on **PDF p13**. Numbers themselves correct. |

### 4. `ntrs-20250004626` — PASS (Tables 1–5; FactSage absent)

**PDF:** 40-page born-digital NTRS copy. Page indices for Tables 1/2/3/4/5 = PDF pp. **7 / 15 / 21 / 24 / 28** — match extract locators.

| Table | attacked cells | match |
| --- | --- | --- |
| 1 LHS-1 oxides | 51.2 / 2.7 / 1.6 / 12.8 / 26.6 / 0.6 / 2.9 / 0.5 / 0.1 / 0.1 | OK |
| 1 Apollo 16 #64501 | 45.3 / 4.2 / 4.9 / 17.2 / 27.7 / 0.4 / 0.4 / 0.1 / `< 0.1` / `-` | OK |
| 2 (1/3/12 h) | products Fe / Fe,P,Si / Fe,P,Si,Ti,Mn; Faradaic **47/53/42%**; O₂ **0.070/0.237/0.749 g**; extraction **0.4/1.2/3.7%** | OK |
| 3 BOL+1/3/12 h at% | full 4×11 grid (Si…O) incl. 12 h Fe **0.00**, Si **15.23**, O **58.97** | OK |
| 4 YSZ recession | 22±10 @ 3 h / 54±12 @ 5 h / 85±19 @ 14 h (time @ 1600 °C); electrolysis 1/3/12 h | OK |
| 5 R @ 1600 °C | 1.54→1.44 (−6.5%); 1.59→1.12 (−29.6%); 1.46→0.78 (−46.6%) | OK |
| method | 1600 °C; 0.5 A; 100 sccm Ar; ~20 g LHS-1; 10.5 wt% YSZ; ~10 / ~1 cm²; Mo / Pt; HPR-20; ±1 h pre/post hold | OK |
| **FactSage 8.3** | **TYPED ABSENCE** | §2.3.4 / §3.4 / Fig. 6 — **no FactSage tokens in observation `values`** | OK |
| Figs 2–5, 7 | TYPED ABSENCE | figure-only | OK |

## Soft findings (not blocking READY)

| # | sev | item |
| --- | --- | --- |
| 1 | **P2** | `ntrs-19730008085` method context `pdf_page_index: 8` → should be **12–13** (mass / hold+getters). Values OK. |
| 2 | P3 | Corpus stem `lpsc-2024-bennu-pyrolysis-vandam` mislabels Mojarro LPSC 1219 (N16 already notes; citation correct). |
| 3 | P3 | Yu extract citation stamps Acta Astronautica **235, 723–735**; held NTRS PDF face does not print vol/pages (DOI/NTRS identity OK). |
| 4 | P3 | Yu method locator `pdf_page_index: 8` is §2.2.2 start; 0.5 A / 100 sccm / ~10 cm² print on **PDF p9** (section-start locator coarseness). |

## P0 findings

**None.**

## Commit / push

- Tip `db0facdc727fb279d7ced01f02a9e73016c70c13` unchanged (no fix commit).
- `git ls-remote origin refs/heads/empirical/n16-aq-oa-batch-b-2026-09-23` = same SHA — **already pushed; no FF push needed.**

**VERDICT: READY · P0=0 P1=0 P2=1 P3=3 · tip: `db0facdc7` (unchanged)**
