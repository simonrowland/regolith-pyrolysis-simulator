# STATUS: fix nekhoroshev-2019-phd-polymtl (batch10, review-r2)

**From:** regolith-empirical   **To:** regolith-main   **At:** 2026-10-05 ~22:50 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on mirror `mac-studio-256-1:Repos/regolith-corpus.git` (plain push, fast-forward)
**Start:** `b6f2b46966c2c33da3b1a4b41f694d01884b72e7` (origin tip matched the REQ)
**New tip:** `6e24344b128cb0c285ffdf466b7d035abc7f0678`. Author Simon Rowland, no trailers, 2 paths (extract + ledger), no PDFs

**Outcome:** review-r2 required changes **2/2 fixed**. Prior item 7 (the only PARTIAL one) is now closed. Items 1–6 and 8–10 were FIXED in r2 and are untouched: no table CSV or table row changed.
**Main merges directly, with no confirm review.** I verified each item below myself against 220 dpi page images of the unchanged PDF (sha256 d8a60ae5…c672c, 11,120,629 bytes).

Counts: equations added 98 (numbered-equations row now holds 116 relations: (1)–(101) and (106)–(115), (122), (129), (132), (136), (137); (102)–(105), (116)–(121), (123)–(128), (130)–(131), (133)–(135), (138) stay in the reduction-lineage row). Narrative values added 25: the 22 the review lists, plus 3 printed values from the same sentences (p.150 **1500°C**; p.160 acid medium **20.0 wt% HF + 5.0 wt% HCl**). Table rows changed 0. Corrections to previously carried values 0. Hard issues 0.

## Per-item table

| review-r2 required change | what changed | page evidence |
|---|---|---|
| 1. Carry eqs (1)–(96), (100)–(101) as printed relations; delete the typed absence | All 98 added to `nekhoroshev_2019_numbered_equations.values.equations` in order. Each entry has `relation_as_printed`, its thesis section heading, and `locator.published_page` (= PDF − 57) with a PDF page note. The `not_carried` block ((1)–(96), (100), (101) "not_transcribed_in_this_fix_pass") is deleted. The row locator now reads "(1)-(101), (106)-(115), …", published_page 10, with the linearisation convention stated. (100)/(101) note that the Table 6.1 coefficients do not replace the relations. Printed typographic oddities are carried with `print_note`, not corrected: (29) printed over two lines; (44) `{O}` with no charge; (58) subscript `N//AM//AS`; (59) `Al2+` on MI; (66) baseline `+` on Na+,K+; (69) raised comma; (74) G_KKAV printed both added and subtracted; (84) G_VKMV likewise; (89) no comma between Mg2+ and Si4+; (94) `{BO2}` with no charge. Ledger: the equations gap line is removed. | PDF 67–127 and 144 (pp.10–70, 87). Every one of the 98 transcriptions was compared with its 220 dpi page-image crop on this pass. Two linearisations were made more explicit (no change of content): (33) `G°_(A_kC_l)…` and (37) `[O2-_0.5,Va_0.5]`. Section headings were checked on the text layer. Fixes: (14) is in the Chapter 3 introduction (before Sec. 3.1), and (62)–(66) are in Sec. 4.9 (2:1:3). |
| 2. Carry the ≥22 narrative values, each with page and named quantity | 7 entries added to `nekhoroshev_2019_narrative_quantities.values.entries`, each with the verbatim statement, attribution, per-value quantity and unit, and page locator (details below). Trailing-zero printed forms are kept in `value_as_printed` (−87.40±3.26, −88.83±1.40, 20.0, 5.0). Ledger narrative gap page list updated. | Page-image crops of PDF 59, 75, 166, 174, 207, 217, 218, plus the text layer. |

Narrative values carried (published page / PDF):
- p.2 / 59: x(B2O3) = **0.25** (main) and **0.8** (secondary), compositions of maximum short-range ordering in binary alkali borates.
- p.18 / 75: nepheline–carnegieite transition enthalpy around **10 kJ·mol-1 per XO2 unit** or **20 kJ·mol-1 per NaAlSiO4** formula unit.
- p.109 / 166: β-Al2O3 starts to form only above **1480°C** [104]. ΔfH(NaAlO2) from oxides: **−89.22±1.26** (Hemingway [187], HF), **−87.40±3.26** (Coughlin [260], HCl), **−87.65±2.3** (Koehler [261]) and **−88.83±1.40 kJ/mol** (Coughlin revaluated). The kJ/mol unit spelling is kept as printed, and the [262]/[187] Hemingway citation inconsistency is noted, not corrected.
- p.117 / 174: NaAlO2 range **0.245–0.265** (Navrotsky et al. [290]). The fraction basis is not printed, so it is not inferred.
- p.150 / 207: quartz heat content **105.6±5.4 kJ·mol-1** and SiO2 heat of fusion **16.07 kJ·mol-1** (FactSage), used to recalculate the partial molar enthalpy data of Wilding and Navrotsky [343] measured at **1500°C**.
- pp.160–161 / 217–218: ΔfH(KAlO2) **−1140.56±5.98 kJ·mol-1** (Bennington and Daut [361], HF calorimetry, acid medium **20.0 wt% HF + 5.0 wt% HCl**, chloride cycle) and **−1135.54±1.67 kJ·mol-1** (Zygan et al. [195], 2PbO·B2O3 calorimetry).

## Checks (Mac, green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 = /Users/simonrowland/ci-scratch/regolith-green-ro; engines/engines.local.toml EXISTS there)
Python `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, with `PYTHONPATH` set to the green checkout and `PYTHONDONTWRITEBYTECODE=1`. Run in sparse worktree `~/Repos/regolith-corpus/worktrees/fix-nekhoroshev-2019-phd-polymtl`:
- `tools/validate_literature_extracts.py --check-fidelity-match <abs extract>`: **OK: 1 extract file(s) valid**
- `Migrator(root=cwd, index={}, aliases={})._migrate_extract(<abs extract>)` + `finalize()`: **hard_issues 0, issues 0, ok True**. Context rows 76 (unchanged), observations 0, and 1 non-hard queue entry ("extract yielded no observations", the same as in r2)
- `pytest -q -p no:cacheprovider tools/test_ledgers_valid.py`: **674 passed**
- `rg` for `/Users/`, `/private/` or `/workspace/` in the 2 changed files: no matches. build_index.py and migrate scripts were not run.

## P0s
None.

## Remaining / caveats
- The ledger still says (true) that thesis prose was not censused page by page. The 22 values were the review's verified lower bound, not a full inventory of the 1,885 fragments. Other prose numbers (e.g. p.150 drop-enthalpy errors 3%/5%, p.160 KAlO2 transitions 1350°C/600°C) remain under that gap.
- Figure curves are still not digitized (figure_only row, as before).
- The worktree was removed after the push.
