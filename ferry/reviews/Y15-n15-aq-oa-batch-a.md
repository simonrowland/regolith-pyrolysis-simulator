# Y15 — CROSS-AUDIT: N15 AQ OA batch A (Riekert 1981 + Knight 2009)

**Lane audited:** N15 (`empirical/n15-aq-oa-batch-a-2026-09-23`)
**Author write-up:** `/workspace/ferry-inbox/reviews/N15-aq-oa-batch-a.md`
**Tip:** `98d00a740c3b04c8a0353d3ebf34fc6d44ff39b0` (unchanged; no fix commit)
**Worktree:** `/workspace/repos/wt/slot-02` @ tip
**PDFs (private, never commit):** `/workspace/ferry-inbox/acquired/{kems-178-riekert-1981,knight-gca2009}.pdf`
**Date:** 2026-09-23 (America/Toronto, EDT)
**Auditor:** distinct subagent from N15 extractor (blind adversarial)

## Verdict

**P0: 0** — every attacked printed number matches the PDF; model / figure / Dacko-dup stay typed-absent or refused.
**READY.** Tip unchanged; nothing to push. No `n15b` fix branch.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 0 |
| P3 | 2 |

## Method

1. Tip `98d00a740` = `origin/empirical/n15-aq-oa-batch-a-2026-09-23`. Re-ran `.venv/bin/python tools/validate_literature_extracts.py` on both extracts → `OK: 2 extract file(s) valid`.
2. `sha256sum` of ferry AQ PDFs vs N15 write-up strings — both match (`1050e37f…e5c782` Riekert; `8952cf1e…89c4dc` Knight). Commit tree: **2 YAML only**; no PDF.
3. Riekert: `pdftotext -layout` + `pdftoppm` 200 dpi pages 3–4 (Tables 1–4). Walked all 35 Table 1 rows, all 7 Table 2 A/B, all 11 Table 3 activity rows, all 11 Table 4 μᴱ/ΔGᴱ rows, plus bench constants (E=18 eV, 5 kV, 10 µA, M/ΔM=960, σ, h).
4. Knight: `pdftotext` Table 1 (PDF p.25) + Results p.13 alphas; scripted cell-by-cell compare of 14 CMAS standards + starting glass + 21 residue rows (oxides, Si loss, ²⁷Al⁻/²⁸Si⁻, δ²⁹/δ³⁰ measured + matrix-corrected, uncs, δ²⁵Mg where printed). Combined + per-T α²⁹/α³⁰ vs Abstract/§3.
5. STEP 0 `rg` across `data/literature/extracts/`; Dacko refusal cross-check vs N15 / prior Y2.

P0 rule: **P0 only if a wrong number or wrong species lands**. Omission / label coarseness ≤ P3. Latent ≤ P1.

## Identity / STEP 0 / PDF ↔ sidecar

| Corpus | PDF identity | Extract | sha256 | Match |
| --- | --- | --- | --- | --- |
| kems-178-riekert-1981 | Riekert, Lamparter, Steeb — *Thermodynamic Properties of melts from the Cu-Si-System by Mass Spectrometry*; Z. Naturforsch. 36a, 447–453 (1981); DOI `10.1515/zna-1981-0505`; 7 pp De Gruyter OA | `kems-178-riekert-1981.yaml` LANDED | `1050e37f292fa124d6b3ad82ab8f8d5668a177a3629f8cc38ebf3cafd1e5c782` | OK |
| knight-gca2009 | Knight, Kita, Mendybaev, Richter, Davis, Valley — *Silicon isotopic fractionation of CAI-like vacuum evaporation residues*; GCA 73:6390–6401; DOI `10.1016/j.gca.2009.07.008`; LLNL-JRNL-414068 OA preprint (37 pp) | `knight-gca2009.yaml` LANDED | `8952cf1e0a800c43b7a53c3e3101b6f1017d34e4c384ec905d324fa14489c4dc` | OK |
| ta-dacko-conradt-low-p-transpiration | (not re-extracted) | **REFUSED** STEP 0 — N2 already ships extract; Y2 READY | (N2 PDF sha in N15 write-up) | OK refusal |

**STEP 0:** No prior primary Riekert / Knight extracts. `kems-049-kato-1993-ms-review` only *cites* Riekert/Lamparter/Steeb 1981 (Z. Naturforsch. 36A, 447) — not a duplicate. Related Knight-campaign extracts (`kems-010-richter-2007`, etc.) lack this Si α / Table 1 payload.

## Per-source audit

### 1. kems-178-riekert-1981 — PASS

**PDF:** 7-page Z. Naturforsch. A OA. **Landed:** bench + Tables 1–4; Figs 1–9 / §6 models typed-absent.

| field / block | landed | printed | match |
| --- | --- | --- | --- |
| DOI / pages / venue | 10.1515/zna-1981-0505; 447–453; 36(5) | same (p.447 masthead) | OK |
| Bench | AEI MS 702; SiC-coated graphite Knudsen; W heater; W–Re 3/25; E=18 eV; 5 kV; 10 µA; M/ΔM=960 | §3–4 p.448–449 | OK |
| σ_Cu / σ_Si @ 18 eV | 3.29 / 4.18 Å² | “σ_Cu = 3.29 Å², σ_Si = 4.18 Å²” (p.449) | OK |
| h(⁶³Cu) / h(²⁸Si) | 69.09% / 92.21% | same | OK |
| **Table 1 (p.449)** — 35 ln(I⁺_Cu/I⁺_Si) | all 7×T grids (0.098…0.885) | image p.3 cell-for-cell | **OK** |
| **Table 2 (p.450)** A,B | 2.39/−13.58 … 2.25/−10.19 (7 rows) | same | **OK** |
| **Table 3 (p.450)** a,γ @ 1700 K | 11 rows incl. ln γ_Cu(x=0)=1.157 / γ_Si(x=1)=1 | same | **OK** |
| **Table 4 (p.450)** μᴱ, ΔGᴱ kJ/mol vs x_Si | 0…16.354 / 6.785…0 / 0…4.141…0 | same | **OK** |
| class_tags | T1 measured; T2–4 derived; Figs figure_only; §6 model | policy | OK |
| §6 MLCs Si₄/Cu₄/Cu₄Si 18.86 / 1.67 / 10.07 | under `method_class: model` typed-absence note | p.451 | OK (not scored) |

Spot-check Table 1 extremes: x_Cu=0.098 @1776 K → −0.05; x_Cu=0.885 @1534 K → 4.56; x_Cu=0.700 @1641 K → 2.00 — all match. Consistency: Table 3 γ_Cu(x=0)=3.180 → RT ln γ ≈ 16.35 kJ/mol ≈ Table 4 μᴱ_Cu(x_Si=1)=16.354.

### 2. knight-gca2009 — PASS

**PDF:** 37-page LLNL preprint of VOR. Locators use this PDF’s page index (stated in `source.corpus_pdf_note`). **Landed:** dual benches + Table 1 + combined/per-T α; √mass curves / CAI precursors / Figs / R3-09 typed-absent.

| field / block | landed | printed | match |
| --- | --- | --- | --- |
| DOI / citation | 10.1016/j.gca.2009.07.008; GCA 73:6390–6401 | Abstract masthead + Crossref path | OK |
| Hashimoto furnace | Ir 2.5 mm loops; P < 10⁻⁹ bar; preheat 1400 °C; ramp 20 °C/min →1600/1800; 40 °C/min 1600→1900 | §2 p.8 | OK |
| P bound (Pa) | interval high 0.0001 Pa (= 10⁻⁹ bar) | unit conversion note | OK |
| WiscSIMS external repro | 0.32 / 0.52 / 0.25 ‰ (δ²⁹ / δ³⁰ / Δ²⁹) | p.10 | OK |
| Starting glass wt% | 46.00 / 19.39 / 11.48 / 23.12 (SiO₂/Al₂O₃/MgO/CaO) | Experimental + Table 1 | OK |
| **Table 1 standards** (14 CMAS) | oxides + Al/Si + δ measured/corrected + uncs; CMAS-7 outlier note | p.25 | **OK** |
| **Table 1 residues** (21) | T, oxides, Si loss±, n, Al/Si±, δ²⁹/δ³⁰ m±/c±; δ²⁵Mg± where printed (footnote 4) | p.25 | **OK** |
| Blank δ²⁵Mg rows (R3-21, R3-7, R3-5, R3-12, R3-13) | omitted (not invented) | PDF column blank | OK |
| R3-09 | `not_analyzed` microcrystalline | §2 | OK |
| **α²⁹Si combined** | **0.98985 ± 0.00044** (2σ) | Abstract + §3 p.13 | **OK** |
| **α³⁰Si combined** | **0.98045 ± 0.00074** (2σ) | §3 p.13 | **OK** |
| per-T α²⁹ | 0.98978/0.98952/0.99032 (±0.00062/0.00074/0.00070) @1600/1800/1900 °C | §3 | **OK** |
| per-T α³⁰ | 0.98037/0.97986/0.98122 (±0.00106/0.00102/0.00114) | §3 | **OK** |
| Isoplot Model 2 / not forced through origin | noted under combined α | §3 | OK |
| √mass SiO α curves / CAI precursor back-calc / Figs 1–9 | `method_class: model` / `figure_only` | typed absence | OK |
| δ²⁵Mg class | separate obs `attributed` + row provenance `Richter_et_al_2007_footnote_4` | footnote 4 | OK |

Scripted compare: **0 numeric mismatches** across all Table 1 value+unc cells vs `pdftotext` of p.25 (CMAS-7 footnote superscript parsed as `CMAS-75` by regex only — extract correctly stores `CMAS-7`).

### 3. ta-dacko-conradt-low-p-transpiration — PASS (refusal)

N15 correctly refused STEP 0 duplicate of N2 (`ta-dacko-conradt-low-p-transpiration.yaml` on `empirical/n2-hastie-dacko-2026-09-22`, Y2 READY P0=0). No new extract on N15 tip.

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P3 | kems-178 | Bench ionization-cross-section unit stored as `A2` (ASCII) rather than `Å²`; value 3.29/4.18 and locator correct. |
| 2 | P3 | knight-gca2009 | δ²⁵Mg numbers also sit inside the `measured` Table 1 `residue_rows` (with provenance stamp) while a parallel observation carries `class_tag: attributed`. Not a wrong number; dual placement is explicit. |

## Validation

```
.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/kems-178-riekert-1981.yaml \
  data/literature/extracts/knight-gca2009.yaml
→ OK: 2 extract file(s) valid
```

## Push

None. Tip remains `origin/empirical/n15-aq-oa-batch-a-2026-09-23` @ `98d00a740c3b04c8a0353d3ebf34fc6d44ff39b0`. No `n15b` branch.

## Report line

**VERDICT: READY · P0=0 P1=0 P2=0 P3=2 · tip: `98d00a740` (unchanged)**
