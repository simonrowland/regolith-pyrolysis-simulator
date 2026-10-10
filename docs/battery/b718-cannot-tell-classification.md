# b-718: classification of "cannot tell" Class-B rows

Tip sweep: `8089eadbf` (work-v064-green after b-714). Papers only; no invented facts.

## Tip Class B cannot-tell (4 rows; all kems-010-richter-2007)

Origin map: Type B CAI-like starting glass on `richter_2007_mg_rate_series_geometry`
(`values.composition_wt_pct`; Al2O3 19.39 / CaO 23.12 / MgO 11.48 / SiO2 46).
Paper (Richter et al. 2007 GCA): vacuum evaporation of Type B CAI-like liquids.

| Observation | Paper verdict | Evidence |
|---|---|---|
| `richter_2007_al_quoted_non_loss_bound` | **same material (no)** | Quote is about Ca/Al refractory behaviour in those Type B CAI-like evaporation experiments (same melt system as the origin geometry row). Sibling `al_non_loss_until_mg_exhausted` already carries phase `silicate_melt_Type_B_CAI_like`. |
| `richter_2007_ca_quoted_non_loss_bound` | **same material (no)** | Same quote/context as Al. |
| `richter_2007_mg_quoted_fits` | **same material (no)** | Arrhenius γ fits to the combined vacuum + finite-H2 laboratory data on the Type B CAI-like melts (paper §results / abstract). |
| `richter_2007_sio_quoted_fits` | **same material (no)** | Same γ fits for Si. |

## Pre-b714 attached sweep cannot-tell (13 CSV rows; backlog said ~11)

After b-714, Hastie / Shaw / Wolf left Class B. Paper classifications for the original unclear set:

### Richter (4) — still Class B at tip
See table above → all **same material**.

### Wolf 2023 VapoRock (9) — no longer Class B at tip
Origin was compiled Apollo 12065 lunar basalt composition.

| Observation | Paper verdict | Evidence |
|---|---|---|
| `wolf_2023_abstract_factor3_model_accuracy` | **different (yes)** | Model-wide accuracy claim, not a 12065 melt binding. |
| `wolf_2023_demaria_lunar_kems_scope` | **different (yes)** | De Maria et al. 1971 KEMS scope (12022 and 12065 heated); foreign measurement summary, not “this row is 12065 composition”. |
| `wolf_2023_eq9_sio_sio2_fo2_proxy` | **different (yes)** | Equilibrium-constant proxy equation for the gas model. |
| `wolf_2023_hastie_kems_scope_and_uncertainty` | **different (yes)** | Hastie slag/illite/SRM glasses, not lunar basalt 12065. |
| `wolf_2023_illite_and_srm_activity_fits` | **different (yes)** | Illite and SRM 621 compositions (Fig. 1). |
| `wolf_2023_lunar_vaporock_activity_fits` | **same material (no)** | Lunar basalt VapoRock activity fits tied to Apollo 12065 (paper Fig./§ comparing VapoRock to De Maria on 12065). |
| `wolf_2023_table2_model_compositions` | **different (yes)** | Table of multiple model compositions. |
| `wolf_2023_table3_vaporock_vs_magma22` | **different (yes)** | Model comparison table, not a single 12065 row material. |
| `wolf_2023_vaporock_system_and_species` | **different (yes)** | Model system/species inventory. |

## Tip Class B census (post-classification)

| Bucket | Count | Notes |
|---|---:|---|
| same material (`no`) | 36 + 4 reclassified = **40** | Was 36 extract-`no` + 4 Richter cannot-tell → same |
| different material (`yes`) | **11** | Richter Table 2 natural CAIs (6) + Hashimoto Table 6 JANAF pure-oxide enthalpies (5) |
| cannot tell remaining | **0** | All tip cannot-tell resolved from papers |

PDFs used: `acquired/kems-010-richter-2007.pdf`, `ferry-b6/audit-pdfs/kems-039-wolf-2023-vaporock.pdf`.
