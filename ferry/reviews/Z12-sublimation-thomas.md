# Z12 FIDELITY AUDIT — sublimation-kinetics / thomas-2022

**Date:** 2026-09-22 (America/Toronto, EDT)  
**Seat:** Z12 (BACKLOG 4) — two extracts (third slot unused)  
**Repo / branch:** `regolith-empirical` @ `empirical/z12-sublimation-thomas-2026-09-22`  
**Base:** `origin/work-v064-green` = `2e9e17c3d`  
**Tip:** `c5c30937907bc718bb0ca60025fa8132f69bd1a1` (one fix commit)  
**Worktree:** `/workspace/repos/wt/slot-y7-n7` (recycled free slot; not mailbox / w3b-green / slot-12)  
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

Remaining BACKLOG 4 candidates after Z1–Z11 write-ups:

| Candidate | Disposition |
| --- | --- |
| kems-020-hastie-1981-nbsir | **SKIP** — owned by Z11 (`Z11-hastie-robinot-sossi.md` / `_z11_audit/hastie`) |
| robinot-2025-promes-review | **SKIP** — Z11 |
| sossi-2020-cu-zn-isotope-evap-formalism | **SKIP** — Z11 |
| sublimation-kinetics-2023-minerals | **AUDITED** |
| thomas-2022-chlorine-bonding-silicate-melts | **AUDITED** |

Only two free sources remain in this backlog slice → seat takes both (<3).

## Verdict

| Extract | Obs / layers | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| sublimation-kinetics-2023-minerals | 19 obs + 2 benches + 2 experiments | **ALL 19** (+ Table 2 full 7×3×2 grid) | **0** | Fig 1 unlabelled BET points not digitised (correct `figure_only`); LMS-1 oxide wt% absent in source; FactSage proxy mole fractions not printed | none |
| thomas-2022-chlorine-bonding-silicate-melts | 3 obs + 4 context | **ALL 43** Table 1 Cl rows + **ALL 43** Table 2 condition/CCl rows (+ apparatus + salt standards spot) | **1 fixed** (AgI/Cl-024 Cl 2.70→2.54) | Partial `rows_as_printed` EPMA snapshot (18 of 43); B2 bench registry not on this base (separate branch); extracts-v2 marks Cl/EPMA quantities unsupported/`unavailable` | tip commit |

**Overall: PASS after fix — P0 cleared on tip. Pushed FF.**

---

## Method

1. `pdftotext -layout` + `-raw` for Thomas multi-column Table 1; selective `pdftoppm` 150 dpi under `/workspace/ferry-inbox/reviews/_z12_audit/`.
2. Full dump of both extract YAMLs → JSON scratch; cell-by-cell Table 2 (Shaw) and Cl/CCl series (Thomas).
3. Fields checked: **value / uncertainty / unit / basis / sign / T·P·duration·fO2 / locator** vs PDF.
4. `uv run python tools/validate_literature_extracts.py`:
   - sublimation → **OK**
   - thomas → **FAIL** 3 pre-existing schema hygiene issues (absolute `provenance_path`, `type: model_comparison` not in enum, fidelity_sample pins `context[]` not `observations[]`) — **not introduced by Z12**; numeric fidelity unaffected (same class as Z10 Lange notes).

---

## 1. sublimation-kinetics-2023-minerals (Shaw et al. 2023, Minerals 13:79)

**PDF:** 12 pp. Vacuum sintering + BET surface-area of LMS-1; FactSage 8.2 p_sat + HKL flux for Fe/Na/K (Apollo 12022 proxy systems).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 5 (`LMS-1`, `12022`, `Fe`, `Na`, `K`) |
| Observations | **19** |
| Benches | 2 (`sintering-furnace`, `bet-analyser`) |
| Experiments | 2 (`lms1-sintering-series`, `lms1-bet-analysis`) |
| fidelity_samples | 8 |

### Sample = ALL 19

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `shaw_2023_lms1_sintering_apparatus` | §2.1 p.2 | 2.6 g; 13 mm die; 80 kN / 5 min; 3 pellets; Carbolite GERO HTRH 100-600/18; MoSi2; E3508P10; Edwards E2M1.5; &lt;10⁻³ atm | yes |
| `shaw_2023_lms1_bet_printed_ssa_vs_T` | abstract / §3.1 / §5 | BET **3.29 / 1.04 / 0.09** m²/g (unsintered / 800 / 1150 °C); further Δ&lt;0.002 m²/g 1150→1200; TriStar II PLUS; density 3.03 g/cm³ quoted Isachenkov | yes |
| `…_fig1_ssa_curve_figure_only` | Fig.1 p.5 | correctly `figure_only` / not digitised | yes |
| `…_fig2_micrographs_and_partial_melt` | Fig.2 / §3.1 | partial melt at **1200 °C** = 1473.15 K; panels a–d match | yes |
| `…_quoted_spec_density` | §2.2 | 3.03 g/cm³ | yes |
| `shaw_2023_table1_apollo_12022_composition_snyder` | Table 1 p.4 | SiO2 43.22 … K2O 0.07 — all 10 oxides match | yes |
| `…_fig3/4/5_*_figure_only` | Figs 3–5 | correctly figure-only (De Maria overlay / proxy / rate×area) | yes |
| `shaw_2023_table2_fe/na/k_psat_*` | Table 2 p.7 | **ALL 7×3** p_atm cells match PDF (800–1200 °C) | yes |
| `shaw_2023_table2_fe/na/k_hkl_flux` | Table 2 p.7 | **ALL 7×3** mol/s/m² flux cells match; α_e=1; Langmuir ac·pi removed | yes |
| `shaw_2023_*_max_sublimation_rate_1200C` | §5 / abstract | Fe **0.08**, Na **1.38**, K **1.02** g/h/g | yes |
| `shaw_2023_hkl_ae_unity_assumption` | §2.3.2 | α_e=1 assumption stated | yes |

**Experiment layer:** total_duration 21600 s = printed “heating duration of six hours”; hold 2 h also retained in BET `snapshot_hold_h`; pressure bound &lt;101.325 Pa (=10⁻³ atm) SI-only.

**P0: 0.** Model-derived FactSage/HKL correctly tagged `model_output_not_measurement`.

---

## 2. thomas-2022-chlorine-bonding-silicate-melts (Thomas, Wade & Wood 2023, Chem. Geol. 617:121269)

**PDF:** 13 pp. Piston-cylinder quench glasses + EPMA Cl + XAFS (Table 1–2).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 1 (`Cl`) |
| Observations | 3 |
| Context | 4 (Table 2 series, apparatus, salt standards, qualitative XAFS) |
| fidelity_samples | 1 (Ab/Fo-1 CCl=0.32) |

### Sample

- **ALL 43** `thomas_2022_table1_chlorine_concentrations_all_rows` (Cl wt% + 2σ)
- **ALL 43** `thomas_2022_table2_experimental_conditions_and_xaf` (T_C, P_GPa, duration_h, log_fO2, CCl; spot E0/Emax/R + fCl2)
- Spot: apparatus Boyd-and-England ½″ Oxford; P 5–20 kbar; Ag+AgCl+AgI buffer; salt-standard XAFS rows; fidelity pin CCl=0.32
- Partial: `rows_as_printed` EPMA multi-oxide snapshot (18 narrative rows)

### P0 found and fixed

| ID | Field | Was | PDF | Notes |
| --- | --- | --- | --- | --- |
| AgI/Cl-024 | `Cl_wt_pct` | **2.70** | **2.54(0.06)** | Copied AgI/Cl-025’s 2.70; unc 0.06 was already the correct 024 unc |

### P1 (fixed opportunistically)

| ID | Field | Was | PDF |
| --- | --- | --- | --- |
| Ab/Fo-3 `rows_as_printed` | K2O unc | 0.32(**0.05**) | 0.32(**0.02**) | prose snapshot only; structured Cl row already had 2.75±0.05 correct |

### Table 2 / other — no further numeric mismatches

Ab/Fo-1…Brucite CCl, T, P, duration, log fO2 grids match layout/raw PDF (incl. AgI/Cl-032 P=0.5 GPa / log fO2=−8.20 / CCl=10.87; AgI/Cl-021 P=2 GPa / −6.66 / 0.65). Abstract “44 glasses” vs 43 table IDs already noted in `corrections` (retained, not invented).

**Coverage:** B2 bench/experiment registry lives on `empirical/b2-thomas-2022-bench-2026-09-22`, not on `work-v064-green` tip audited here. extracts-v2 currently refuses Cl/EPMA quantities as unsupported → `unavailable` (so the 2.70 typo was extract-v1 authority; fixed at source).

---

## Git

- Branch: `empirical/z12-sublimation-thomas-2026-09-22` from `origin/work-v064-green`
- Files touched: `data/literature/extracts/thomas-2022-chlorine-bonding-silicate-melts.yaml` only
- **No PDFs in git**
- Push: FF to origin
