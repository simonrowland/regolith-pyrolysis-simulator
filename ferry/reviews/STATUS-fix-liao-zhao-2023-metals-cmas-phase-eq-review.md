# STATUS: fix liao-zhao-2023-metals-cmas-phase-eq-review

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~21:40 ET
REQ: corpus batch 8, section A (main's grok review of 79fed982: 12 rows, 0 mismatches, 30 printed numbers not carried). After this: grok confirm.

| Field | Value |
|---|---|
| Branch | `hunt/liao-zhao-2023-metals-cmas-phase-eq-review` on the mirror `mac-studio-256-1:Repos/regolith-corpus.git` |
| Start (verified on origin before work) | `79fed982b76e6adb3790285e42f408d503348f45` |
| **New tip** (pushed, ls-remote verified, fast-forward) | **`6f72621e1434898ac506784d234ac7973a68e1bb`** |
| Commits | `6f72621e` one fix commit (explicit pathspecs: extract + ledger only; no trailers) |
| Extract sha256 @ tip | `6a422f957159908ab639db76f7953bc3fd48c114e75da5e860a93f47bc52bf28` |
| Ledger sha256 @ tip | `82f56e62e8e932cd1baf6e1e8b6fcc193421f8b074aea0cef39cf2a31859135b` |
| Worktree | sparse (`worktrees/fix-liao-zhao-2023-metals-cmas-phase-eq-review`, main's recipe), removed after delivery |
| Merged into mirror main | no (main merges) |

**items fixed 9/9, printed numbers carried 30/30, no-numeral facts carried 4/4, value corrections 0 (no mismatches existed), hard issues 0**

Counts: corrections 34 (30 printed numbers + 4 no-numeral facts from review section "Also carry"). Plus the extras listed below. Rows: 12 -> 18 context rows (6 added, 4 extended, 8 untouched). No existing value was changed: the diff deletes only lines that were rewritten to be longer (the extraction `method` line, the basicity note, nine pointer strings, the pointer `note`).

## Checks (on the Mac)
| Check | Result |
|---|---|
| validator `--check-fidelity-match` (green `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, `/Users/simonrowland/ci-scratch/regolith-green-ro`, PYTHONPATH there, repo `.venv` python) | **OK: 1 extract file(s) valid** (4 fidelity samples; 1 new: `liao2023_constant_cs_sections.bosh_slag_section_CaO_SiO2_as_printed = '1.5'`, p. 10) |
| Migrator `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(...)` + `finalize()`, cwd = corpus worktree | registry issues **0**, validation issues **0**, **hard issues 0**, observations 0, context **18** (`context_by_work['10.3390/met13040801']`), queue 1 (`extract yielded no observations`, expected for a review) |
| `python -m pytest tools/test_ledgers_valid.py -q` (corpus worktree) | **717 passed** |
| `rg` for `/Users/` and `/private/` in the extract and ledger | no hits |
| `tools/build_index.py`, `tools/migrate_pilot_extracts.py` | not run |
| `engines/engines.local.toml` in the green checkout | **exists** (`/Users/simonrowland/ci-scratch/regolith-green-ro/engines/engines.local.toml`) |

Page evidence: all 21 pages rendered with `pdftoppm -r 220` (PDF sha256 `9c3160ae…52af` re-verified). Every page listed below was opened, and every added numeral was read from a zoomed crop of the 220 dpi render, not from the text layer. Nothing was illegible, so no typed absence was needed. No figure was digitised. The printed Osborn `[6]` and the p. 3 `[10]/[11]` swap are kept as printed, not corrected.

## Per-item table
| Review item | What changed (row . field) | Page evidence (220 dpi render) |
|---|---|---|
| 1. p. 1 hot-metal liquidus (#1) | NEW `liao2023_hot_metal_liquidus_background` (type `background_statement`, `quoted_unattributed`, because no reference is attached): `hot_metal_liquidus_as_printed: below 1200 °C` + full sentence | p. 1, §1 para 2: "The hot metal bearing carbon has a liquidus temperature below 1200 °C which is much lower than the liquidus temperature of the slag." |
| 2. p. 3 Prince prep + thermocouple (#2-5 + thermocouple fact) | `liao2023_techniques_summary` extended: `prince_sample_prep_as_printed` (quote), `prince_calcination_T_as_printed: 1250 °C (several hours)`, `prince_silicic_acid_leach_as_printed: 1:1 HC1`, `prince_fusions_as_printed: three or four`, `prince_sample_prep_print_note` (p. 3 prints `Al2O3·xHzO`; p. 2 prints `Al2O3·xH2O`; acid printed `HC1` with a digit one; both kept as printed), `temperature_measurement_as_printed` (the [13] sentence + the Fig. 1 labels "Working thermocouple" / "Controller Thermocouple"), `hot_stage_citation_swap_note` | p. 3 lines 1-4: "calcined to 1250 °C for several hours", "leached with 1:1 HC1", "After three or four fusions, about 20 mg"; p. 3 end of the "improved quenching" paragraph: "next to a thermocouple ... accurately monitored [13]"; Fig. 1 labels; p. 3 "Gutt et al. [10] and Cavalier et al. [11]" vs the p. 20 list; p. 2 last line "Al2O3·xH2O" (text layer) |
| 3. p. 5 solid solutions + EPMA limit (#6-10) | NEW `liao2023_solid_solution_techniques` (attributed to Osborn & Schairer [5]; the EPMA limit is the review's own statement about the technique, Fig. 2 [22]): `akermanite_isotropic_as_printed: 55% akermanite ...`, `akermanite_colour_estimate_range_as_printed: 40-70% akermanite`, `akermanite_colour_estimate_error_as_printed: less than ±5% akermanite`, `epma_phase_size_limit_as_printed: greater than 2 μm`, quotes, `percent_basis_note` (wt% vs mol% not printed) | p. 5 §2.2: "melilite solid solution with 55% akermanite is isotropic in sodium light", "40–70% akermanite ... error of less than ±5% akermanite"; p. 5 para after Fig. 2 reference: "any phases greater than 2 μm" |
| 4. p. 6 Osborn constant-Al2O3 sections (#11-15) | NEW `liao2023_osborn_constant_al2o3_sections`: `compositions_studied_as_printed: 446`, `Al2O3_sections_range_as_printed: 5 to 35% at 5% intervals`, `Al2O3_planes_shown_as_printed: 10, 15 and 20%`, quote; `plane_meaning_as_printed` (10% high-quality ores; "15% Al2O3 is an average for most of the current BF slag compositions"; 20% low-quality ores), `plane_trend_as_printed` (10→15% monticellite replaced by spinel; 20% spinel field expands); attribution keeps the body's [6] and the captions' [7]; `citation_discrepancy_note` | p. 6 §3.1: "446 compositions were studied by Osborn et al. [6] ... 5 to 35% at 5% intervals"; "constant Al2O3 concentrations of 10, 15 and 20%" (text + Fig. 3 caption [7]); p. 7 para 1 |
| 5. p. 6 + p. 10 CaO/SiO2 = 1.5 bosh slag (#21) | NEW `liao2023_constant_cs_sections` ([13]-[16]): `CaO_SiO2_sections_as_printed: 0.9, 1.1, 1.3 and 1.5`, `final_BF_slag_range_CaO_SiO2_as_printed: 0.9-1.3`, `bosh_slag_section_CaO_SiO2_as_printed: '1.5'` (+ new fidelity sample), quote | p. 10 §3.3: "Four pseudo-ternary sections with the CaO/SiO2 ratios of 0.9, 1.1, 1.3 and 1.5 ... ratio of 1.5 represents the compositions of the bosh slag"; p. 6 "ratios of 0.9, 1.1, 1.3 and 1.5 were experimentally determined [13–16]" |
| 6. p. 8-9 constant-MgO planes + C6MA4S (#16-20 + C6MA4S fact) | NEW `liao2023_constant_mgo_sections`: `prince_plane_as_printed: constant 10% MgO`, `cavalier_planes_as_printed: constant 5, 10 and 15% MgO, respectively`, `gutt_plane_as_printed: constant 5% MgO`, `cavalier_vs_prince_as_printed` (Cavalier's 10% plane slightly below Prince's), `gutt_vs_cavalier_as_printed` (C6MA4S of Gutt & Russell absent from Cavalier's plane; merwinite not mentioned by Cavalier), `phase_name_as_printed: C6MA4S`; spelling note Russell (body) vs Russel (list) | p. 8 §3.2: "Prince et al. [8] constructed ... at constant 10% MgO"; p. 9: "Cavalier and Sandrea-Deudon [10] reported three pseudo-ternary sections ... at constant 5, 10 and 15% MgO, respectively"; "Gutt and Russell [11] reinvestigated ... at a constant 5% MgO"; "The C6MA4S phase reported by Gutt and Russell [11] is not present" |
| 7. p. 12-13 CaO/SiO2 = 1.1 extension (#22 + Ca2SiO4 corner fact) | `liao2023_cs1p1_vs_factsage` extended: `agreement_with_previous_work_as_printed` (Osborn [7], Cavalier and Sandrea-Deudon [10], Muan and Osborn [24] "at 1400 °C"; Fig. 11 legend at 1400 °C), `ca2sio4_corner_as_printed` | p. 12 first para under Fig. 11: "... Muan and Osborn [24] at 1400 °C"; Fig. 11 legend "Osborn et al. 1400 °C / Cavalier et al. 1400 °C / Muan et al. 1400 °C"; p. 13 lines 1-2: "the Ca2SiO4 primary phase was observed ... at the high (CaO + SiO2) corner, which is not shown by FactSage predictions" |
| 8. p. 14 typical slag ~1420 °C + quaternary basicity (#23 + definition fact) | NEW `liao2023_typical_bf_slag_al2o3_sio2_0p4` ([22], Fig. 14): `typical_bf_slag_liquidus_as_printed: approximately 1420 °C (melilite primary phase field)`, quote (Fig. 14 legend "Average composition of Shougang BF slag"; its composition is not printed as numbers), `quaternary_basicity_definition_as_printed: (CaO + MgO)/(Al2O3 + SiO2)` + quote. `liao2023_basicity_liquidus.basicity_naming_note` gains a cross-reference to the p. 14 definition | p. 14 para under Fig. 13: "melilite primary phase field with a liquidus temperature of approximately 1420 °C", "quaternary basicity (CaO + MgO)/(Al2O3 + SiO2)"; Fig. 14 legend; p. 17 Fig. 18 caption "quaternary basicity" with axis "(CaO+MgO)/SiO2 (weight)" |
| 9. p. 20 title compositions + ref [21] (#24-30) | `liao2023_cited_primary_studies`: title text added to [14] ("CaO/SiO2 ratio of 1.10"; the body uses 1.1), [17] ("Al2O3 (30 mass%)"), [18] ("Al2O3 (25 mass pct)" and "Al2O3 (35 mass pct)"). NEW pointer [21] Yao, Ma & Lyu 2021, Calphad 72, 102227 ("Al2O3–CaO–SiO2-(0%, 5%, 10%) MgO slag system"). `note` extended (titles as printed; en dashes written as hyphens; end pages not copied) | p. 20 reference list, entries 14, 17, 18, 21 (zoomed crops) |

## Extras beyond the 30 (additive, same page evidence)
- Title compositions on the existing pointers [8] (10% MgO plane), [13] (0.9), [15] (1.3), [16] ("binary basicity of 1.5"), [22] (Al2O3/SiO2 weight ratio of 0.4), [23] (MgO/CaO ratio of 0.2). These were not counted in the review; I copied them from p. 20.
- New pointer [24] Muan & Osborn 1965, Phase Equilibria Among Oxides in Steelmaking, pp. 148-157 (p. 20). It is now cited by the 1.1 row.
- p. 7 plane meanings and trends, p. 9 Cavalier-vs-Prince, and the p. 3 [10]/[11] citation swap. The review noted the swap but did not require it.
- Ledger: `decoded.render_dpi: 220` + `page_images` note; `extracted.note` 12 → 18 rows; `extracted.fix_round` {date, worker, base 79fed982, applied}.

Not done (out of scope per review): Figs. 1-20 not digitised. Figure ticks, isotherms, and scale bars are not carried. References [19], [20], [25]-[33] are not added: they print no title composition, or they are outside the phase-equilibrium pointers.

I do not self-certify LAND. Ready for main's grok confirm.

— regolith-empirical
