# N16 NEW EXTRACT — AQ-acquired OA PDF batch B

**Branch:** `empirical/n16-aq-oa-batch-b-2026-09-23`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `db0facdc727fb279d7ced01f02a9e73016c70c13`
**Worktree:** `/workspace/repos/wt/slot-n16`
**Date:** 2026-09-23 (America/Toronto, EDT)
**PDFs:** `/workspace/ferry-inbox/acquired/` (private; not committed)

## Identify first

| Corpus / file stem | PDF p1 identity | Match? |
| --- | --- | --- |
| `lpsc-2024-bennu-pyrolysis-vandam` | Mojarro et al., *Early OSIRIS-REx… pyrolysis of samples returned from asteroid Bennu*, 55th LPSC (2024) **#1219** (authors Mojarro/Aponte/Dworkin/Glavin/Elsila/Connolly/Lauretta). **No Van Dam author** — corpus stem is a mislabel | Yes for PDF identity; citation rebuilt from PDF p1 |
| `metsoc-2019-6005` | Shornikov & Yakovlev, *Mass spectrometric study of perovskite evaporation from Knudsen cell*, MetSoc 2019 #6005 | Yes |
| `ntrs-19730008085` | Manuel, Srinivasan, Hennecke, Sinclair, *Noble Gases in the Moon*, NASA-NGR-26-003-057 annual report, Sept 1972 | Yes |
| `ntrs-20250004626` | Yu et al., *Improving molten regolith electrolysis with zirconia-based hollow anode technology*, Acta Astronautica / NTRS 20250004626, DOI `10.1016/j.actaastro.2025.06.028` | Yes |

## STEP 0

| Paper | author/`rg` | DOI | Prior extract? |
| --- | --- | --- | --- |
| `lpsc-2024-bennu-pyrolysis-vandam` | No Mojarro / LPSC-2024-1219 / OREX-500002 extract | none (LPSC abstract) | No |
| `metsoc-2019-6005` | Other Shornikov extracts (`kems-033`, `kems-029`) — **different papers** | none (MetSoc abstract) | No perovskite / MetSoc 6005 |
| `ntrs-19730008085` | No Manuel / NGR-26-003-057 / 15601.64 extract (Ryugu noble-gas extracts are different) | none (NTRS report) | No |
| `ntrs-20250004626` | No literature_extract for this DOI/NTRS. Parallel `mre_measurements.yaml::yu_2025_hollow_anode_measurements` + works alias cite same DOI/NTRS — **not** a literature_extract duplicate | `10.1016/j.actaastro.2025.06.028` unique among extracts | No extract YAML |

All four STEP 0 PASS → extract.

## Validate

```
OK: 4 extract file(s) valid
OK: 4 extract file(s) valid   # --check-fidelity-match
```

0 new errors. Validator: `/workspace/repos/wt/slot-n16/.venv/bin/python tools/validate_literature_extracts.py` (venv → main `.venv`).

## Per-paper status

### 1. `lpsc-2024-bennu-pyrolysis-vandam` — LANDED (context-only; identity note)

**Extract:** `data/literature/extracts/lpsc-2024-bennu-pyrolysis-vandam.yaml`

**Identity:** PDF is Mojarro et al. LPSC 2024 #1219, not a Van Dam paper. Corpus stem retained.

**What landed (printed numbers + locators; 0 scored observations):**
- Samples OREX-500002-0 (QL) / OREX-800031-0 (TAGSAM); ~1 mg × 2 subsamples
- Standard pyrolysis 600 °C; wet-chemistry pyrolysis 250 °C; MTBSTFA/DMF (4:1), 5 µL, 85 °C / 1 h
- Instrument: CDS 6200 / TRACE 1600 / TSQ 9610; MRM + full scan 50–500 m/z
- QL alanine+glycine quantitation signals **>100×** co-analyzed Murchison

**Not extracted:** Qualitative PAH / amino-acid / N-heterocycle detection lists (no absolute concentrations).

### 2. `metsoc-2019-6005` — LANDED (measured thermo; Table 1 typed absence)

**Extract:** `data/literature/extracts/metsoc-2019-6005.yaml`

**Scored:**
| Quantity | Printed | Type | Locator |
| --- | --- | --- | --- |
| ΔfH (perovskite formation) | −39.88 ± 0.54 kJ/mol | `gibbs_table` | Results |
| ΔfS (perovskite formation) | 3.15 ± 0.28 J/(mol·K) | `gibbs_table` | Results |
| Melting | 2241 ± 10 K; ΔHfus 47.61 ± 1.84 kJ/mol | `transition_point` | Results |

**Typed absences (not ingested):**
- **Table 1** gas-phase mol% — abstract states *calculated from oxide activities* → model/derived
- Fig. 1 lg pᵢ curves; Fig. 2 activities at 2250 K — figure-only, not digitized

**Method context:** Knudsen Mo cell; perovskite 1791–2241 K; CaO–TiO₂ melts 2241–2441 K; Hertz–Knudsen.

### 3. `ntrs-19730008085` — LANDED (Appendix I 15601.64 He/Ne/Ar + cumulative)

**Extract:** `data/literature/extracts/ntrs-19730008085.yaml`

**Scored `concentration_series`:**
- **Table 1** (PDF p35): He/Ne/Ar isotope ratios + ⁴He / ²²Ne / ³⁶Ar contents (×10⁻⁸ cc STP/g) for 100–1500 °C stepwise heating of lunar fines **15601.64** (0.9861 g; 100 °C steps; 30 min holds)
- **Table 4** (PDF p38): cumulative % release of ⁴He, ²²Ne, ³⁶Ar, ⁸⁴Kr, ¹³²Xe vs T

**Typed absences:**
- Appendix I Tables 2–3 (Kr, Xe) — scan/OCR unreliable on this PDF
- Appendix II (15271.65) and Appendix III (Nature 1972 Xe ms) — out of this lane

**Fidelity pins:** 700 °C ⁴He content 450000 ×10⁻⁸ cc STP/g; 700 °C ⁴He cumulative 79.388%.

### 4. `ntrs-20250004626` — LANDED (measured tables; FactSage typed absence)

**Extract:** `data/literature/extracts/ntrs-20250004626.yaml`

**Scored `composition_series`:**
| Table | Content |
| --- | --- |
| 1 | LHS-1 vs Apollo 16 #64501 major oxides wt% (secondary compilation) |
| 2 | 1/3/12 h: cathodic products; Faradaic 47/53/42%; O₂ 0.070/0.237/0.749 g; extraction 0.4/1.2/3.7% |
| 3 | Post-electrolysis EDS at% (BOL + 1/3/12 h) |
| 4 | YSZ recession 22±10 / 54±12 / 85±19 μm at 3/5/14 h @ 1600 °C |
| 5 | Cell R @ 1600 °C: 1.54→1.44 (−6.5%); 1.59→1.12 (−29.6%); 1.46→0.78 (−46.6%) |

**Method context:** 1600 °C; 0.5 A; 100 sccm Ar; ~20 g LHS-1; 10.5 wt% YSZ hollow anode; Mo cathode; Pt collector.

**Typed absences:** FactSage 8.3 O₂-removal model (§2.3.4 / §3.4 / Fig. 6 / S7–S8); Figs 2–5, 7 not digitized.

**Note:** Parallel `mre_measurements.yaml::yu_2025_hollow_anode_measurements` already cites this DOI/NTRS; this is the literature_extract.v1 landing for SOURCE_STATUS `ntrs-20250004626`.

## Refusals

| Paper | Reason |
| --- | --- |
| *(none refused)* | All four STEP 0 PASS; all four LANDED |
| Content parked / typed absence | MetSoc Table 1 (calculated); Manuel Kr/Xe tables (OCR); Yu FactSage model |

## Commit

```
db0facdc7 extracts: N16 AQ-OA batch B — Mojarro LPSC 1219, Shornikov MetSoc 6005, Manuel 1972 noble gases, Yu 2025 hollow anode
```

4 extract YAML files added. Private PDFs not committed. Branch pushed to `origin/empirical/n16-aq-oa-batch-b-2026-09-23`.

## Optional follow-ups (not done)

- Remap / note corpus stem `lpsc-2024-bennu-pyrolysis-vandam` → Mojarro LPSC 2024 #1219 in SOURCE_STATUS
- Higher-quality scan or journal reprints for Manuel Appendix I Tables 2–3 (Kr/Xe) and Appendix II/III
- migrate + readiness / engine_point once I1 is green; sync literature extract with existing `yu_2025_hollow_anode_measurements` MRE store
