# Confirm review r2: yakovlev-2017-cai-acidity @ 5062c4392a01f77199c1d64477c4e0d7fd0ce8d6

**Reviewer:** regolith-empirical seat (batch3 A)   **At:** 2026-10-05 ~21:05 ET
**Source:** Yakovlev, Ryazantsev and Shornikov (2017), *Geochemistry International* 55(3), 251–256, DOI 10.1134/S0016702917020070
**Branch:** `hunt/yakovlev-2017-cai-acidity` on `mac-studio-256-1:Repos/regolith-corpus.git`. `git fetch` tip = `5062c4392a01f77199c1d64477c4e0d7fd0ce8d6`, which matches the assignment.
**Earlier review:** review.md (r1, FIX-FIRST on `b4a7e63f`, 9 required changes, rows 11, mismatches 8, printed numbers not carried 34)

VERDICT ON COMMIT: FIX-FIRST

rows checked 11, mismatches 2, printed numbers not carried 0

(Mismatches use r1's convention: the number of context rows with a transcription or locator discrepancy. The 2 rows hold **4 separate discrepancies**, listed under Required changes. r1 missed all 4, and they were present in b4a7e63f before the fix. The fix commit did not introduce them.)

## Method

- I worked on Simon's Mac in a sparse, detached worktree `~/Repos/regolith-corpus/worktrees/rev2-yakovlev-2017-cai-acidity` made with main's recipe: `--no-checkout`, sparse set with only `raw/yakovlev-2017-cai-acidity/*` and `text/…`, plus all sidecars. It used about 46 MB and was removed after delivery. Before starting, `df -g /Users` showed 83 GB free.
- I rendered all 6 pages with `pdftoppm -png -r 220` and read them as images. I re-read every changed region and every quoted sentence in crops at 220 dpi native resolution, which is ImageMagick crops of the same renders. The extraction report was treated as a claim to test.
- I read the full `git diff b4a7e63f 5062c439`. It touches only `extracts/yakovlev-2017-cai-acidity.yaml`, `ledger/yakovlev-2017-cai-acidity.yaml` and `raw/yakovlev-2017-cai-acidity/sidecar.yaml`. Then I re-checked all 11 context rows, including all 30 directional or qualifier quotations, against the page images. No sampling was used.

## r1 findings: status at 5062c439 (default stance NOT FIXED, overturned only by page image plus tip)

| # | r1 required change | Status | Evidence (page image vs tip) |
|---|---|---|---|
| 1 | p.253 Fig. 3: drop the unprinted material-loss zero; make axis orientation explicit; flag the suspected source error | **FIXED** | The p.253 crop shows the vertical axis "Material loss, wt %" with ticks 20, 40, 60, 80. The only "0" is the horizontal-axis origin. The horizontal axis "Concentration, wt %" has ticks 0, 20, 40, 60, 80, 100. The tip has `material_loss_percent_ticks: [20, 40, 60, 80]`, `component_concentration_wt_percent_ticks: [0,…,100]`, `horizontal_label: Concentration, wt %`, `vertical_label: Material loss, wt %`, and a `reason: source_internally_inconsistent` flag with no reinterpretation. |
| 2 | p.254 Figs. 5–6: remove the unprinted activity zero | **FIXED** | Both activity axes print 0.2, 0.4, 0.6, 0.8, 1.0. The tip has `activity_ticks: [0.2, 0.4, 0.6, 0.8, 1.0]` in both rows. The composition ticks are unchanged and correct: CaO 0–30 by 5, Al2O3 0–25 by 5. |
| 3 | Eq. (4) needs its own locator, p.254 / index 3 | **FIXED** | The tip has a per-equation locator on each item: (1)–(3) p.253 idx 2, (4) p.254 idx 3. These match the images. The row-level locator still says p.253 with `(1)–(4)`. That is acceptable because each equation now carries its own locator. |
| 4 | p.253: the full "With an increase in the evaporation losses…" quote belongs to Experimental Data | **FIXED** | It sits above the ROLE OF ACIDITY–BASICITY heading in the p.253 left column. The tip section is `Experimental Data on Evaporation of Melts`. The neighbouring "strong interaction between CaO and SiO2…" quote was also moved there, and that move is correct per the image. |
| 5 | Figs. 1–4: full `caption_as_printed` | **FIXED** | All four captions match the print word for word, including the citations and the printed semicolons: Fig. 3 "(3) CaO; (4) Al2O3"; Fig. 4 "(3) SiO2; (4) TiO2; (5) Al". |
| 6 | p.251: "rich in 16O" | **FIXED** | The quote "they are rich in 16O and daughter products of certain short-lived isotopes, including 26Mg, which is a decay product of 26Al (t1/2 ~ 0.7 Ma)." matches p.251 left column. |
| 7 | p.252: "Krymka LL3" | **FIXED** | The quote "By way of example, we present our experimental data on evaporation of the Krymka LL3 chondrite (Yakovlev et al., 1984, 1987)." matches the p.252 right column (Experimental Data). The sample-preparation and composition absences are unchanged and still `unknown/not_published`, and no oxide analysis was invented. |
| 8 | p.251: "starting in the 1970s until 2000" | **FIXED** | The quote "which remained practically unmodified starting in the 1970s until 2000" matches the p.251 right column. |
| 9 | Bibliographic and metadata numeric groups; Shornikov 2009 (p.254 text) kept separate from 2008 (captions); Korzhinskii 1959 (p.253) | **FIXED** | `source_metadata_as_printed`: ISSN 0016-7029, Vol. 55 No. 3 pp. 251–256, Geokhimiya 2017 No. 3 pp. 224–229, Moscow 119991, and Received January 22, 2016 / final March 3, 2016 all match the p.251 header. `citation_metadata_as_printed`: p.251 group, p.253 Korzhinskii 1959, and p.254 "Rein and Chipman, 1965; Shornikov, 2009" with a 2009-vs-2008 inconsistency reason. All 25 reference records were checked digit by digit against pp.255–256 and all match. The fixer's printed **C3** (not r1's "CV3") and **Marova** (reference) vs **Markova** (p.252 text) are both correct per the image. |

**Result: 9/9 r1 items FIXED.**

## Collateral and new findings (full re-check of all 11 rows)

The fix diff damaged nothing. Every changed field was re-read against the image. The sidecar `publisher: Pleiades Publishing, Ltd.` matches the p.251 header. The ledger note change is descriptive only.

Re-checking the untouched rows against the image found 4 pre-existing discrepancies that r1 missed. Three are in verbatim quotes and one is a section locator. The extract presents these strings as quotations, the brief requires verbatim directional quotes, and the brief says garbled translation must be "quote[d] … and say so". The fix report says "No copied main-text/reference discrepancy was silently normalized", but item C below contradicts that.

## Required changes

A. **p.253 (pdf idx 2), left column, sentence after Eq. (3)**, in row `yakovlev_2017_directional_claims_and_data_qualifiers`. The tip has a word-order error: "…but is their acceptor instead." The print reads:
   `"The minus sign at αSiO2 shows that silicon oxide does not act as a donor of oxygen ions in melts but is instead their acceptor."`

B. **p.254 (pdf idx 3), right column, first paragraph before Eq. (4)**, same row. The tip puts the parenthesis in the wrong place: "the activity (or in our situation, the partial pressure) of this component in the vapor." The print reads:
   `"The activity of a component in melt controls, in turn, the activity (or in our situation, the partial pressure of this component) in the vapor."`

C. **p.254 (pdf idx 3), right column, paragraph after Eq. (4)**, same row, "three parameters" quote. The print reads "(2) the concentration of the oxide **min** the multicomponent melt". The tip silently normalizes this to "in". Restore "min" as printed and add a `translation_note` flagging it as a plainly garbled translation (evidently "in"), the same treatment already used for "at a rare of" and "составов".

D. **p.252 (pdf idx 1), left column**, row `yakovlev_2017_numeric_background_context`. The quote "laboratory experiments on evaporation of forsterite (Davis et al., 1990) and other compositions составов" is in the **Introduction**, which continues through the p.252 left column and the top of the right column. The EXPERIMENTAL DATA ON EVAPORATION OF MELTS heading only starts partway down the right column. Change its `section` from `Experimental Data on Evaporation of Melts` to `Introduction`.

All four are text and locator edits within existing fields. No numbers, rows, method classes or absences change.

### Advisory (not required for LAND)

- p.253 and p.254: the print uses double quotation marks around “liberation” and “normal”, and the extract has ‘…’. This is a nested-quote convention only. Change it if you want strictness.
- The quote "Because of this, CaO is the dominant donor of oxygen ions…" starts at the bottom of the p.253 right column and ends on the p.254 left column. The locator says p.254 only, and could say 253–254.
- p.253 Fig. 4 discussion quote ("no Mg vapor occurs…"): the attribution is "Yakovlev and Shornikov (2011), as cited in the discussion of the Efremovka CAI". The printed paragraph says "results of our evaporation experiment" with no citation of its own. The Fig. 4 caption cites Yakovlev et al. (1984) and Yakovlev and Shornikov (2011). Consider attributing to the caption's pair.
- The p.254 citation reason says the main text cites Shornikov (2009). Note that the p.255 main text (Fig. 6 discussion) cites "Rein and Chipman, 1965; Shornikov, 2008". The 2009 citation is p.254 only.

## Page inventory (r2)

| Page | Tables/equations/numeric statements | Carried at 5062c439? |
|---|---|---|
| 251 | Header metadata (ISSN, vol/no/pp, Russian 224–229, Moscow 119991, dates); 4.567 Ga; 16O; 26Mg/26Al t1/2 ~0.7 Ma; 1970s–2000; ~1300–1000°C; ~10−3–10−6 bar; citation years | Yes, all |
| 252 | ≥1400°C; 50–0.5 grad/h; ~50% Mg / ~25% Si; Krymka LL3; Fig. 1 (T 1000/1500/2000; log p −3…−8; series 1–8); Fig. 2 (T 1400–2000; log p −4…−8; series 1–4) | Yes, all. Section locator issue D. |
| 253 | Eqs. (1)–(3); Korzhinskii 1959; Fig. 3 (loss 20–80, conc 0–100); Fig. 4 (T 1600–2400; log p −5…−8; series 1–5) | Yes, all. Quote A. |
| 254 | Eq. (4); 1600°C; MgO/SiO2 = 2:3; 25–30 mol %; Figs. 5–6 (CaO 0–30, Al2O3 0–25, activity 0.2–1.0); Rein and Chipman 1965 / Shornikov 2009 (text) vs 2008 (captions) | Yes, all. Quotes B, C. |
| 255 | Fig. 7 (r = 0.8; CaO 15–45; MgO/SiO2 0–1.0, 0 printed); references with numbers | Yes, all |
| 256 | References with numbers; no new data | Yes, all |

There are no printed tables, so there are no CSVs and no observation or reduced rows. Figures are figure-only and no curves or points were digitized. That is still correct.

## One line per extract row

Table | row | extract | print | match
- context | numeric_background | 4.567; 16O; 26Mg/26Al ~0.7; 1970s–2000; ~1300–1000; ~10−3–10−6; ≥1400; 50–0.5; ~50%/~25%; составов; LL3; metadata, citation and 25 reference groups | pp.251–256 identical | **section locator mismatch (D)**; all numbers match
- context | fig1 | caption; T [1000,1500,2000]; logp [−3…−8] | p.252 identical | match
- context | fig2 | caption; KEMS; T [1400,1600,1800,2000]; logp [−4…−8] | p.252 identical | match
- context | fig3 | caption; loss [20,40,60,80]; conc [0…100]; labels; reason | p.253 identical | match
- context | fig4 | caption; KEMS; T [1600…2400]; logp [−5…−8] | p.253 identical | match
- context | fig5 | 1600; 2:3; CaO [0…30]; activity [0.2…1.0]; series 1,3 / 2,4 | p.254 identical | match
- context | fig6 | 1600; 2:3; Al2O3 [0…25]; activity [0.2…1.0]; caption-vs-text reason | p.254/255 identical | match
- context | fig7 | r = 0.8; CaO [15…45]; ratio [0…1.0] | p.255 identical | match
- context | no_new_run_and_apparatus | not_applicable run; 2 × knudsen_effusion; 11 unknown/not_published | pp.252–253 cite prior work only | match
- context | acid_base_equations | Eqs. (1)–(4) with per-equation locators | p.253 / p.254 identical | match
- context | directional_claims | 30 quotes | pp.251–255 | **3 verbatim mismatches (A, B, C)**; the other 27 match

## Experiment facts and absences

These are unchanged from r1 and re-confirmed. The paper reports no new physical run. The cited KEMS method appears on p.252 ("The experiment was carried out in a Knudsen cell"; Fig. 2 caption "Data obtained by Knudsen-effusion mass spectrometry") and on p.253 (Fig. 4 caption). Cell material, liner, orifice, calibration, cross sections, ionisation energy, multiplier corrections, temperature sensor, chamber background, sample preparation and defining ions are all correctly typed `unknown/not_published`. None of them is printed anywhere in the six pages. The activity reference state is p.254 "p_i° is the partial pressure of pure component i", and no solid or liquid standard state is printed. Series 1,3 are tagged quoted_attributed (Rein and Chipman 1965) and series 2,4 model_derived (Shornikov 2008), which is correct. There are no reduced rows, so no lineage is required.

## Provenance and executable checks

- PDF sha256 `60dc9c6a76f982afc947d62a385e2f6ffa4d51102b7601ad6f7042b766e42c98` equals the sidecar and the extract `corpus_sha256`. The citation, 55(3), 251–256 and the DOI match p.251. Licence/access is `owner-supplied copy` / `owner-supplied`, and nothing contradicts it.
- `rg -n '/Users/|/private/'` over the extract, ledger and sidecar finds no matches. `git diff --check b4a7e63f 5062c439` is clean. The YAML parses.
- Green: read-only `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, using `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH=<green-ro>` and `PYTHONDONTWRITEBYTECODE=1`, run from the green cwd. **`engines/engines.local.toml` EXISTS** in green-ro. It is untracked and gitignored (`.gitignore:105`), 1331 B, dated Oct 4.
- Migrator: `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<abs sparse-worktree extract path>)` then `finalize()` gives **works 1, experiments 0, observations 0, context rows 11, hard issues 0**. The queue has 1 entry, the expected "extract yielded no observations". A payload spot check of the migrated contexts found the restored strings: planet-33.pdf, Krymka LL3, 16O, 1970s–2000, 119991, January 22 2016, Korzhinskii 1959, Shornikov 2009, both activity tick arrays [0.2…1.0], Fig. 3 loss ticks [20, 40, 60, 80], C3 chondrites and Marova. The 3 wrong quote strings (A–C) also survive as written, so the reader faithfully carries the defect.
- `tools/validate_literature_extracts.py --check-fidelity-match <abs sparse-worktree extract path>`, run from green-ro: **OK: 1 extract file(s) valid**, exit 0.
- Corpus `tools/test_ledgers_valid.py`, run from the sparse worktree with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -c /dev/null -o addopts= -p no:cacheprovider`: **688 passed**.
- No build_index or migrate_pilot was run. I made no corpus edits, commits or pushes, and nothing was written in green-ro.

!COMPLETE: rev2-yakovlev-2017-cai-acidity — FIX-FIRST, pages read 6, rows checked 11, mismatches 2, printed numbers not carried 0
