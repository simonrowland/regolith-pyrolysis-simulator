# STATUS: fix nekhoroshev-2019-phd-polymtl — group 4 (Tables 1.1, 1.2)

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~21:45 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Pushed full tip after this group:** `6c47c26b310ea08d6bbdb2909e21d9ef920c6217`
(chain: cfaa7465 → b02bcc7b g1 → 2a9892ad g2 → a99679a4 g3 → 6c47c26b g4)

## Groups covered
- g1 6/6 mismatches; g2 Tables 14.1 (53 rows) and 21.1 (57 rows); g3 Table A.42 p.529 (59 lines) + Table A.6 fraction semantics.
- g4 (this push), item 5: new `tables/.../t1.1.csv` (+provenance) — **40 rows** (pp.7–8 / PDF64–65: Name, System, Chemical_formula,
  Abbreviation, Solid_solution_in_this_work, published_page) and `t1.2.csv` (+provenance) — **17 rows** (p.8 / PDF65: Solid_solution,
  Aliases). Same context/table representation as the other tables (`nekhoroshev_2019_table_1_1`, `_1_2`, `quoted_unattributed`,
  author nomenclature). Quotation marks on non-stoichiometric formulas and printed dashes (−, no abbreviation / no membership) are
  carried verbatim. Ledger `transcribed.table_count` 67 → **69** (the thesis prints 69 tables).
  Read from 220 dpi page images, cross-checked with the PDF text layer.

## Checks (Mac; green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 at /Users/simonrowland/ci-scratch/regolith-green-ro; engines/engines.local.toml exists there)
- Migrator `_migrate_extract` + `finalize()`: hard issues **0**.
- `validate_literature_extracts.py --check-fidelity-match <abs corpus extract>`: OK: 1 extract file(s) valid.
- Corpus `tools/test_ledgers_valid.py`: 674 passed.

## Items done so far: 7/10 (review.md items 1, 2, 3, 4, 5, 6, 10)
Remaining: 9 activity standard states + reduction lineage; 8 per-study method facts; 7 numbered equations (1)–(138) + narrative quantities.
