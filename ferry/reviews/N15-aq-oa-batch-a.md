# N15 NEW EXTRACTS — AQ OA batch A (keep-busy)

**Branch:** `empirical/n15-aq-oa-batch-a-2026-09-23`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `98d00a740c3b04c8a0353d3ebf34fc6d44ff39b0`
**Worktree:** `/workspace/repos/wt/slot-02`
**Date:** 2026-09-23 (America/Toronto, EDT)
**PDFs:** `/workspace/ferry-inbox/acquired/` (private; not committed)
**Pushed:** yes → `origin/empirical/n15-aq-oa-batch-a-2026-09-23`

## Validate

```
OK: 2 extract file(s) valid
```

Files: `kems-178-riekert-1981.yaml`, `knight-gca2009.yaml`.
Validator: repo `.venv` → `tools/validate_literature_extracts.py`. 0 new errors.

## Policy (N15 / same as N1–N14)

STEP 0 identity + no-duplicate (`rg` corpus + remote N branches); extract **printed measured** data only; model outputs → typed absence; never invent numbers; never commit private PDFs.

## Per-paper status

### 1. kems-178-riekert-1981 — LANDED

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No primary extract. `kems-049-kato-1993-ms-review` only *cites* Riekert/Lamparter/Steeb 1981 as a Cu–Si KEMS reference. |
| STEP 0 DOI | `10.1515/zna-1981-0505` — unique; matches sidecar + Crossref |
| PDF ↔ sidecar | Match by title/authors/DOI/pages. PDF p1: G. Riekert, P. Lamparter, S. Steeb — *The Determination of Thermodynamic Properties of melts from the Cu-Si-System by Mass Spectrometry*; Z. Naturforsch. 36a, 447–453 (1981). sha256 `1050e37f292fa124d6b3ad82ab8f8d5668a177a3629f8cc38ebf3cafd1e5c782` (acquired; sidecar was locate-only / sha null) |
| Extract | `data/literature/extracts/kems-178-riekert-1981.yaml` |

**What landed (printed numbers + locators):**
- Bench: AEI MS 702; SiC-coated graphite Knudsen cell; W heater; W–Re 3/25 thermocouple; E = 18 eV; 5 kV; 10 µA trap; M/ΔM = 960; σ_Cu = 3.29 Å² / σ_Si = 4.18 Å² @ 18 eV; h(⁶³Cu)=69.09% / h(²⁸Si)=92.21%
- **Table 1 (p.449)** — measured ln(I⁺_Cu/I⁺_Si) grid (7 x_Cu × T; 35 rows) — `class_tag: measured`
- **Table 2 (p.450)** — least-squares A,B for ln(I⁺_Cu/I⁺_Si)=A·(10⁴/T)+B — `derived`
- **Table 3 (p.450)** — a_Cu, a_Si, γ, ln γ at 1700 K (Belton–Fruehan) — `derived`
- **Table 4 (p.450)** — µᴱ_Cu, µᴱ_Si, ΔGᴱ (kJ/mol) at 1700 K — `derived`

**Typed absences:** Figs 1–9 (`figure_only`); §6 Regular + Schmid–Schürmann agglomerate model (Si₄ / Cu₄ / Cu₄Si; MLCs 18.86 / 1.67 / 10.07) — `model`.

### 2. knight-gca2009 — LANDED

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Knight 2009 / DOI extract. Related but distinct: `kems-010-richter-2007` (same residue campaign; Mg isotopes + rates; no Si α table), `kems-037-richter-2002`, N4 LPSC abstracts. |
| STEP 0 DOI | `10.1016/j.gca.2009.07.008` — unique (Crossref VOR GCA 73:6390–6401). Sidecar was locate-stub (doi null; WiscSIMS URL); PDF is LLNL-JRNL-414068 OA preprint of the same paper. |
| PDF ↔ sidecar | Title/authors/venue match Crossref + WiscSIMS target. PDF: Knight, Kita, Mendybaev, Richter, Davis, Valley — *Silicon isotopic fractionation of CAI-like vacuum evaporation residues*. sha256 `8952cf1e0a800c43b7a53c3e3101b6f1017d34e4c384ec905d324fa14489c4dc`. Sidecar present:no / sha null at B4; identity by citation + OA path. |
| Extract | `data/literature/extracts/knight-gca2009.yaml` |

**What landed:**
- Benches: Hashimoto vacuum furnace (Ir loops; P < 10⁻⁹ bar; 1600/1800/1900 °C) + WiscSIMS Cameca IMS-1280 (external repro 0.32 / 0.52 ‰ for δ²⁹Si / δ³⁰Si)
- **Table 1 (PDF p.25)** — CMAS standards + starting glass + 21 residues: oxides, Si loss %, ²⁷Al⁻/²⁸Si⁻, δ²⁹Si/δ³⁰Si measured + matrix-corrected — `measured`. Oxides noted as reprinted from Richter et al. 2007a.
- **δ²⁵Mg column** — footnote 4 Richter et al. 2007 — `attributed`
- **α²⁹Si = 0.98985±0.00044**, **α³⁰Si = 0.98045±0.00074** (2σ, combined-T) + per-T alphas — `derived`

**Typed absences:** inverse-√mass SiO comparator curves (`model`); CAI precursor reconstructions (`model`); Figs 1–9 (`figure_only`); R3-09 microcrystalline (not analyzed).

### 3. ta-dacko-conradt-low-p-transpiration — REFUSED (STEP 0 duplicate)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | **HIT:** `origin/empirical/n2-hastie-dacko-2026-09-22` already ships `data/literature/extracts/ta-dacko-conradt-low-p-transpiration.yaml` (tip `2f92f46b9`; Y2 audit READY, P0=0). |
| STEP 0 DOI | none (same as N2) |
| PDF ↔ sidecar | Match. sha256 `6ce8060672079f92e8260dab3280790a90b341761f94a2293fa23212f042680a` = B3 sidecar / N2 PDF. |
| Action | **No new extract.** Do not duplicate under same `source_id`. |

## Refusals summary

| Paper | Refused content | Reason |
| --- | --- | --- |
| kems-178-riekert-1981 | Figs 1–9; §6 Regular / Schmid–Schürmann model | figure_only / model |
| knight-gca2009 | Figs 1–9; √mass α curves; CAI precursor back-calc; R3-09 | figure_only / model / not analyzed |
| ta-dacko-conradt-low-p-transpiration | entire paper | STEP 0 — already extracted on N2 |

## Commit

```
98d00a740 extracts: N15 Riekert 1981 Cu-Si KEMS Tables 1–4 + Knight 2009 Si α / Table 1; refuse Dacko (N2 dup)
```

Files added (2). Private PDFs not committed (`*.pdf` gitignored; `ferry/` untracked). Branch pushed FF.

## Optional follow-ups (not done this lane)

- Remap SOURCE_STATUS `kems-178-riekert-1981` / `knight-gca2009` located→extracted; leave `ta-dacko-…` to N2 merge.
- migrate + readiness / engine_point once I1 green.
- Y-audit seat for N15 (Table 1 ion-ratio grid + Knight α 0.98985 / Table 1 residue rows).
