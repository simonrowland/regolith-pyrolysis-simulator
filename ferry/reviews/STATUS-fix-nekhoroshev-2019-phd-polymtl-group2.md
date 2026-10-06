# STATUS: fix nekhoroshev-2019-phd-polymtl — group 2 (main-table continuations 14.1, 21.1)

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~21:20 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Pushed full tip after this group:** `2a9892ad54fe7302a7b97a36ae7733994a13b9e9` (parent: group 1 `b02bcc7b3cdbbe95e32490168108126a3852f022`)

## Groups covered
- Group 1 (done earlier): 6/6 mismatches (Table 8.1 four bindings; Table 24.1 and scope-context locators).
- Group 2 (this push):
  - Item 1, Table 14.1, p.222 / PDF279 (220 dpi image read + crops, cross-checked with the PDF text layer): 14 rows added → **53 rows**
    (56 numeric temperature/composition cells). Row 40 (bold 827 °C; 33.70, 0.91, 65.39) has empty Solid phases / Equilibrium type cells on
    p.222 and is bound to the Combeite-Wo-NC2S2 eutectic group continued from p.221 (stated in provenance + locator note). Bold rows =
    `model_derived`; bracketed rows keep their attribution ([117], [428]).
  - Item 2, Table 21.1, pp.301–302 / PDF358–359: 54 rows added → **57 rows** (54 temperatures + 111 composition cells = 165 values).
    New columns `published_page` (per row) and `Print_note`. p.301 rows 4–5 inherit NM5S12-pEn-Oliv across the page break. 18 reported
    rows printing identical Na2O and MgO (16 of them summing >100 mol%) are carried as printed and flagged, not repaired. p.302
    Tri-NM5S12-NM2S6: the non-bold composition is bound to the 1018 [391, 495] row (first reported row, as in every other group; the
    print sits between lines, stated explicitly), the bold composition to the bold 993 row, 1013 [158] has no composition.
    Provenance also carries the p.300 "hand-plotted, could be not precise" qualifier and the p.302 Botvinkin [497] exclusion note.

## Checks (Mac; green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461, /Users/simonrowland/ci-scratch/regolith-green-ro; engines/engines.local.toml exists there)
- Migrator `_migrate_extract` + `finalize()`: hard issues **0**.
- `validate_literature_extracts.py --check-fidelity-match <abs corpus extract>`: OK: 1 extract file(s) valid.
- Corpus `tools/test_ledgers_valid.py`: 674 passed.

## Items done so far: 4/10 (review.md items 1, 2, 4, 6)
Remaining: 3 Table A.42 p.529; 5 Tables 1.1–1.2; 10 Table A.6 fraction semantics; 9 activity standard states + reduction lineage;
8 per-study method facts; 7 numbered equations (1)–(138) + narrative quantities.
