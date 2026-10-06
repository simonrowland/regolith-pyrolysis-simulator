# STATUS: fix of banya-hino-nagasaka-1993-hydroxyl (corpus batch 8, section A)

**From:** regolith-empirical (fix seat)   **To:** regolith-main   **At:** 2026-10-05 ~21:55 ET
**Branch:** `hunt/banya-hino-nagasaka-1993-hydroxyl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Started from:** `32e031c962f0146c1e79beebc09cd45c46f12b2a`. `git fetch origin hunt/banya-hino-nagasaka-1993-hydroxyl` gave exactly that tip before I started.
**New tip (pushed to origin hunt/<sid> only, never main):** `196f8df260231b7a6d8bfe7114e3fcfa0f38bb2a` (parent 32e031c9; one commit, explicit pathspecs, no trailers)
**Files changed:** `extracts/banya-hino-nagasaka-1993-hydroxyl.yaml`, `ledger/banya-hino-nagasaka-1993-hydroxyl.yaml` (+251 / −16). I did not touch the sibling `slag-002-banya-hino-nagasaka-1993.yaml`, the sidecar, raw/ or text/.
**Review applied:** `review.md` (first review, rev-b57: rows checked 40, mismatches 2, printed numbers not carried 3, plus required change R3).

items fixed 6/6 required (M1, M2, N1, N2, N3, R3) + 2/2 optional (A1, A4); corrections 29 (28 extract records + 1 ledger note); hard issues 0

## Self-verification (main merges directly, so there is no confirm review)
I verified every item myself. I rendered all 8 PDF pages with `pdftoppm -r 250` (2056×2903 px each), cut each page into four column/half crops, and read every crop as an image.
Every value, attribution, reference number, page number and verbatim quote in the new or changed records was checked against those images, not against OCR or the review text.
In two places the print differs from the quote text in review.md, and I used the print:
- R3 item 3 continues "…as shown in Figs. 7 and 8 **as an example.15–19)**".
- R3 item 4 reads "Also in CaO–SiO2 **melts** containing basic oxide…".

PDF page → printed page, checked from the page footers: 1→12, 2→13, 3→14, 4→15, 5→16, 6→17, 7→18, 8→19.
Renders are under `~/.goal-flight/ferry-out/fix-banya/` on the Mac, outside the repo. The raw PDF sha256 is `b70ddca6…20c0f`, which matches the sidecar.

## Per-item table
| review item | change on 196f8df2 | page evidence (PDF/printed, read on the 250 dpi render) |
|---|---|---|
| M1 wrong attribution, `k2o_sio2_data_range` | `attribution` changed from "Steiler (reference 23), data shown in Figure 2" to "Kurkjian and Russell (reference 16; J. Soc. Glass Tech. 42 (1958) 130T); data shown in Figure 8". Added locator `figure: '8'`, a verbatim statement, and a note explaining why: the sentence names no author, Fig. 8's legend gives Kurkjian & Russell, and §4.2 / §4.2.2 name Kurkjian and Russell16). Added `reason: source_internally_inconsistent: …` because the text cites "Steiler23)" while the reference list gives 23) = Ban-ya, Iguchi, Yamamoto and 33) = J. M. Steiler. Values 0.5–0.7 unchanged. | PDF 4/15 left col: "The hydroxyl capacity of K2O–SiO2 slag has been measured in the composition range of XSiO2=0.5 to 0.7 as shown in Fig. 8." PDF 6/17 Fig. 8 legend: "Kurkjian & Russell △ KO0.5–SiO2 (1573K)". PDF 3/14 right col §4.2: "Kurkjian and Russell16) measured the hydroxyl capacity of Na2O–SiO2, K2O–SiO2 and Li2O–SiO2 melts". PDF 4/15 left col: "measured by Steiler23)". PDF 7/18 refs 16) and 23). PDF 8/19 ref 33). |
| M2 mixed page conventions | Every locator (all 25 records) now carries `published_page` and `pdf_page_index`, using 1-based PDF pages as the same-DOI sibling does. The bare `page` key is removed. The convention is stated once in `extraction.locator_convention`. As the review asked: notice 13/2, Kennedy 14/3, Eq. 14 range 14/3, K2O range 15/4. | Page footers on PDF 1–8 read 12–19. Table 2 is on PDF 2 (p. 13), §4.1 on PDF 3 (p. 14), §4.2.1 on PDF 4 (p. 15). |
| N1 P° = 101 325 Pa not carried | New record `banya_hino_nagasaka_1993_reference_pressure` (`reference_pressure_Pa: 101325`, symbol P°, used in Eqs. 1, 2, 8, verbatim sentence). | PDF 1/12 right col §2, sentence under Eq. (1): "…and the atmospheric pressure (101 325 Pa) respectively." |
| N2 X_SiO2 < 0.7 not carried; 0.82 mis-attributed | New record `banya_hino_nagasaka_1993_eq14_derivation_composition_range` (`{value: 0.7, relation: less_than}`, quoted_attributed to Ban-ya and Hino refs 10, 12, verbatim). The existing 0.82 record now has `stated_by: present authors … Fig. 1 composition range`, method_class quoted_unattributed, figure '1', and a verbatim statement. The wrong "Ban-ya and Hino" attribution is gone. | PDF 3/14 right col: "Equation (14) has been derived, by Ban-ya and Hino,10,12) in the composition range of XSiO2<0.7 …" and "Therefore, ΔG°11 given by Eq. (14) can not be applied to the composition range of Fig. 1 in which SiO2 content in the melts, XSiO2, is greater than 0.82." |
| N3 1 673 K not carried | New record `banya_hino_nagasaka_1993_banya_1673K_same_tendency` (`temperature_K: 1673`, CaO–SiO2–Al2O3, quoted_attributed to ref. 31 = S. Ban-ya, Y. Iguchi, S. Nagata, Tetsu-to-Hagané 71 (1985) 55). | PDF 7/18 left col §5: "Ban-ya et al.31) have also found the same tendency in C'OH of this slag at 1 673 K." Ref 31) on PDF 8/19 right col. |
| R3 16 directional/comparative statements; false coverage claim in the notice | 16 records, `…_dir01` … `…_dir16`, type `directional_statement`. Each has a verbatim `statement_verbatim`, its own locator (section, figure/table, column) and a method_class: quoted_attributed with attribution where the print cites others (dir02, 03, 04, 05), quoted_unattributed for the authors' own claims. The duplicate notice's locator note and `additional_numeric_prose_context` no longer say these are in the sibling; they point to this extract's context records. The ledger `transcribed` note had the same false claim and is corrected too. | dir01 PDF 1/12 Abstract; dir02 PDF 1/12 R §2; dir03 PDF 1/12 R→2/13 L; dir04 2/13 L; dir05 3/14 L §4.1; dir06 4/15 L; dir07 4/15 L; dir08 4/15 R ("exit" sic); dir09 5/16 L; dir10 5/16 L; dir11 5/16 R; dir12 5/16 R; dir13 6/17 L; dir14 6/17 L; dir15 7/18 L; dir16 7/18 L. Each quote was re-read word for word on the crop. |
| A1 (optional) Figs. 7/8 text vs caption | New record `banya_hino_nagasaka_1993_figs_7_8_text_caption_swap` with `reason: source_internally_inconsistent: …`, the text sentence and both captions verbatim. | PDF 6/17 left col §5 text ("…in alkaline silicates and CaO–SiO2 binary slags respectively") against the Fig. 7 caption (CaO–SiO2, 1 873 K) and the Fig. 8 caption (NaO0.5/LiO0.5/KO0.5–SiO2). |
| A4 (optional) K2O(l) standard state | New `K2O:` context record `banya_hino_nagasaka_1993_k2o_liquid_standard_state` (K2O: pure liquid K2O; KO0.5: hypothetical regular solution of pure liquid KO0.5; verbatim). | PDF 3/14 right col, last paragraph of §4.2.1; Eq. (18) on PDF 4/15 left col. |

Not applied:
- A2 (sibling's 54 hard issues) is a main decision about another extract.
- A3 (legend temperatures) belongs in the sibling, which is outside this sid.
- A5 (sidecar licence) concerns the sidecar, which is not in this diff.
- A6 is a report-only note.
- The four Kennedy values and the 0.5–0.7 values were re-checked and are unchanged.

## Checks (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 = `~/ci-scratch/regolith-green-ro`, read-only cwd, PYTHONPATH=that clone, `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python`)
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/banya-hino-nagasaka-1993-hydroxyl.yaml)` then `finalize()` both complete. Result: works 1, observations 0, `validation.ok True`, **hard issues 0**, 25 rows in `context_by_work`. The queue has 1 entry ("no observations"), which is expected for a duplicate record.
- Payload survival in the serialised context rows:
  - 101325, 0.7 (less_than), 0.82 (greater_than), 1673, 0.5/0.7, and Kennedy 1273/1573, 200/970, 10/35, 1743/40/10, 1743/1993 are all present as numbers.
  - 23 `statement_verbatim` strings and both `reason: source_internally_inconsistent` fields are present.
  - The Kurkjian attribution and "pure liquid K2O" are present.
- `tools/validate_literature_extracts.py --check-fidelity-match <absolute path of the extract in my sparse corpus worktree>` (run from the green clone): `OK: 1 extract file(s) valid`.
- Corpus `tools/test_ledgers_valid.py`: `python3 tools/test_ledgers_valid.py` exits 0, but this file is a pytest module, so plain python runs no test. I therefore also ran it as `python -m pytest -q tools/test_ledgers_valid.py`: **687 passed**.
- `rg "/Users/|/private/"` on the extract: no matches. YAML parse check: every locator key is in the SCHEMA set, there are no null values, and comma-containing flow scalars are quoted.
- `engines/engines.local.toml` in the green clone: **exists** (gitignored).
- I ran neither tools/build_index.py nor tools/migrate_pilot_extracts.py.
- Worktree: sparse (SEAT-COMMON recipe, 49 MB) at `~/Repos/regolith-corpus/worktrees/fix-banya-hino-nagasaka-1993-hydroxyl`, removed after delivery. `df -g /Users` showed 69 GB free at the start.

!COMPLETE: fix-banya-hino-nagasaka-1993-hydroxyl — 196f8df260231b7a6d8bfe7114e3fcfa0f38bb2a, items fixed 6/6 (+2 optional), corrections 29, hard issues 0; self-verified against 250 dpi page images, no confirm review needed
