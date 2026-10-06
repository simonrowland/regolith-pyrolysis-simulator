# REPORT: t-1117 attribution, ruling (b): rename to `values.attribution` + page repairs

- from: regolith-empirical · to: regolith-main · at: 2026-10-05 ~21:25 ET
- ruling: RULING-hastie-t1117-b716 (10-04 19:45 ET) §#17, option (b). Backlog #17. Answers REQ-backlog-reseat §3.
- branch: `review/t1117-attribution-rename` @ **`56865c42bc56c61833daea31e95e9a934827bec8`** (`git ls-remote` verified 2026-10-05 ~21:00 ET)
- base: `8089eadbfc856179668287e9c4e503e0e92c2682`. The branch is 175 behind current green `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, and
  `git merge-tree --write-tree 61ec839da 56865c42b` is **clean**. Not merged or rebased (your call).
- This code was pushed 10-04 ~20:04 ET. The REPORT was lost to the outage, so this is the first one.
- No derived store on the branch: `git diff 8089eadbf 56865c42b -- data/literature/extracts-v2 data/literature/observations-v2 data/battery` is empty.
  Absolute paths added: **0** (`rg '^\+.*(/Users/|/workspace/|/private/|/home/)'` on the diff).

## Commits
| commit | what |
|---|---|
| `f36c187405796c9590e556090aeff9b2b585c191` | PIN (tests only) `tests/battery/test_extract_attribution_home.py`. Red at 8089eadbf |
| `bcf6f2ee9833735c438a0f1564fb04168e388e4b` | data: scripted rename (`tools/rename_values_attribution.py`, no hand edits). 74 species-observation `values` blocks in 17 extracts; list `quoted_from` joined with "; "; plus the one path-form fidelity sample (Zahnle 2010) that mirrors a renamed block. **Conflicts (block already had `attribution`): none.** Row-level, nested (`values.rows[]`, `values.<sub>`), `context[]` and non-species keys are left alone, including Schaefer & Fegley 2011's row-level `source_attribution` prose. No reader change, no alias |
| `6e5631585d4687712cba67ba5eeea6ba72dc6982` | data: page repairs. `values.attribution` is written only where the page or its reference list names the work (8 extracts, 9 insertions) |
| `56865c42bc56c61833daea31e95e9a934827bec8` | test: pin reuses `test_migrate._extract_observations` (no second walker). Same assertions |

### Page repairs (6e5631585): §1's 11 records + Tachibana 1998 + Turkdogan Ref. 2
| record | page | name written |
|---|---|---|
| JSC 12023 C/N compilation (5 row children) | p3 | Moore 1971; Kaplan & Petrowski 1971; Kerridge 1978; Norris 1983 |
| ammin-76-904 quoted comparator enthalpies | pp909–911 | Stebbins 1983/1984; Richet & Bottinga 1984; Ferrier 1968; Adamkovicova 1980 |
| hashimoto-nakano-2021 appendix sample provenance | p25 | Jarosewich (1990) |
| lpi-compendium-15013 maturity and grain size | p1 | Morris 1978 (for Is/FeO = 77) |
| lpi-compendium-15013 carbon and nitrogen | p1 | Moore 1973; Des Marais 1973; Kothari & Goel 1972; Muller 1973 |
| lpi-compendium-65701 attributed trace/exposure | p1 | Moore 1973; Kerridge 1975; Cirlin & Housley 1981; Wrigley 1973; Walton 1973; Graf 1993 / Butler 1973 |
| itoh-hino-banya-1998 quoted prior interaction parameters | p87 refs 10–11 | Mizin 1983 (e_Mg_Al); Sponseller & Flinn 1964 |
| tachibana-tsuchiyama-1998 prior-experiment α | p1 refs [1]–[4] | replaces "experimental studies [1-4]" with Hashimoto (1990) [1]; Wang et al. (1993) [2]; Nagahara & Ozawa (1996) … |
| kems-048 Turkdogan 2001 Ref. 2 (self-citation) | p932 ref 2 | "E. T. Turkdogan: ISIJ Int., 40 (2000), 964 (ref 2; the author's recent publication)" |
**Left `quoted_unattributed`: none.** Every page named its source, and no name was invented.

## Acceptance: migrator sibling delta + hard issues (real migrator, uncommitted scratch regens)
The regens were run in scratch worktrees by the 10-04 seat: `z6-rename` @ bcf6f2ee9 (20:17 ET) and `z6-after` @ 6e5631585 (20:31 ET; the tip differs from it only in the test file).
I recomputed the delta today from those trees against the committed 8089eadbf store (`t1117_sibling_delta.py`, read-only). The output is attached:
`ATTACH-t1117-sibling-delta-56865c42b.txt`. Nothing is committed.

| measure | your prediction | rename only (bcf6f2ee9) | tip (6e5631585 = 56865c42b data) |
|---|---:|---:|---:|
| hard issues (migration-report census) | −12 | **3638 → 3626 (−12)** | **3638 → 3615 (−23)** |
| `conditional_field:attribution` | | 23 → 11 | 23 → **0** |
| quoted_unattributed → quoted_attributed flips | 95 | **95** | **95** |
| attribution fills (class unchanged) | 23 | **23** (quoted_attributed 12, figure_only 5, model_derived 5, compilation_assessed 1) | **34** (quoted_attributed 23 [+11 page repairs], figure_only 5, model_derived 5, compilation_assessed 1) |
| attribution text replaced (class unchanged) | | 14 | 15 (+ Tachibana) |
| observation ids added / removed | | 0 / 0 | 0 / 0 |
| sibling files touched | | 16 extracts-v2 + migration-report.md | 21 extracts-v2 + migration-report.md |

Flips by source (both stages): Schaefer & Fegley 2011 38, De Maria 1973 31, Murchison/Voropaev 2023 8, Kato 1993 5, Turkdogan 2001 5,
de Guzman 2026 3, Ohara 1987 2, Furukawa 1976 1, Yamada 1983 1, Nunoue 1987 1. The measured numbers equal the predictions exactly at
the rename stage. The page repairs then remove the remaining 11 attribution hard issues.

## Tests (VPS, targeted)
- `tests/battery/test_extract_attribution_home.py`: tip **37 passed**; same file at 8089eadbf **22 failed / 15 passed** (red-first confirmed).
- `tests/test_literature_extracts.py -k <the 20 touched sources>`: 8089eadbf **4 failed / 42 passed**, tip **4 failed / 42 passed**. The failed set is identical:
  the baseline fidelity-sample mismatches ammin-75-781-hemingway-1990, cooper-2007, deguzman-2026, itoh-hino-banya-1997, the same
  ones in your b693 r2 review's baseline list. No regression.
- Not run: whole `test_literature_extracts.py` / `test_migrate.py` validate-corpus (300 s timeouts at green). Mac Studio pregate, please.

## D-062 canonical answers
1. **Second copy? NO.** No reader change. The pin's own walker was replaced by `test_migrate._extract_observations` (56865c42b).
   `tools/rename_values_attribution.py` is the one rename script.
2. **Rule in presentation/wiring? NO.** Data and tests only.
3. **Forbidden import / cycle? NO.** No simulator code changed. The tool imports only stdlib + yaml.
4. **Behaviour-preserving moves pinned first? YES / n.a.** No code move. The data change is pinned red-first by f36c18740.
5. **Relaxed guard / baseline entry? NO.** The validator's `quoted_attributed requires attribution` guard is untouched, and its 23 hits are cleared by data.

— regolith-empirical
