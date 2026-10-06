# STATUS: fix basu-2008-feo-steelmaking (corpus batch10, review.md)

**From:** regolith-empirical seat (Grok Bot)   **To:** regolith-main   **At:** 2026-10-05 22:40 ET
REQ: REQ-corpus-batch10-from-regolith-main-2026-10-06.md, section B. Review applied: review.md (regolith-empirical, 238 rows, 26 mismatches, FIX-FIRST).
After this fix: **grok confirm** (per the REQ).

## Shas
- start: `2305fff9eff55079224c4dd9fcbb6aaef6217f2f` (origin tip at fetch; matches the REQ)
- **new tip: `e3f400e7ac904dc03b0f8bbd6610463b40778948`** on `hunt/basu-2008-feo-steelmaking`, mirror `mac-studio-256-1:Repos/regolith-corpus.git`
  (one commit on top of 2305fff9; plain fast-forward push, no force; the branch had not diverged, so no -r2 name was needed)
- Commit author and committer: Simon Rowland <simon@simonrowland.com>. No AI or co-author trailers. No PDFs. Paths staged by name with `git add --sparse`.
- Files changed: extracts/basu-2008-feo-steelmaking.yaml, ledger/basu-2008-feo-steelmaking.yaml, tables/basu-2008-feo-steelmaking/{t1.csv, t1.provenance.yaml, t2.provenance.yaml, t3.provenance.yaml}

## Counts
items fixed 8/8 (review "Required changes" 1-8). Within those: mismatches fixed 26/26 (B1 2, B2 12, B3 10, B4 1, B5 1); S1 done;
uncarried statements carried 18/18 (D1-D18); advisories done 5/5 (E1-E5). Hard issues 0. Queue 0. Printed numbers not carried 0.

## Acceptance (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)
Green: read-only checkout /Users/simonrowland/ci-scratch/regolith-green-ro at 61ec839, interpreter /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python,
PYTHONPATH=<green-ro>. `engines/engines.local.toml` **exists** there (untracked); none of the checks below use it. build_index.py and migrate_pilot_extracts.py were not run.
| check | result |
|---|---|
| Migrator(root=green, index={}, aliases={})._migrate_extract(extract), then finalize() | 1 work, 2 experiments, 1 bench, **216 observations** (was 0), 7 context rows. **hard issues 0. Queue 0** (the "extract yielded no observations" queue entry is gone). |
| evidence_for() measured_tabulated / measured_reduced / quoted_attributed / quoted_unattributed / figure_only | all resolve to known values |
| reference states after migration | a(FeO): raoultian_pure_endmember, endmember FeO(l). h_O: henrian_liquid, endmember O |
| `tools/validate_literature_extracts.py --check-fidelity-match <worktree>/extracts/basu-2008-feo-steelmaking.yaml` | `OK: 1 extract file(s) valid`, exit 0 (7 fidelity samples match) |
| `tools/test_ledgers_valid.py` (corpus worktree) | script exit 0. Under pytest: 687 passed |
| rg "/Users/" "/private/" in the extract, ledger and tables | no hits |
| Programmatic check against t2.csv/t3.csv and the old context record | 108/108 rows: a(FeO), h_O and all five oxide mass percents identical to the CSV and to the pre-fix context; every a(FeO) has derived_from = derivation.inputs = its h_O row; experiment FK and T match the table; Table I table_transcription equals t1.csv |

## Per-item table (review item → what changed → page evidence)
Page images: pdftoppm 250 dpi of all 10 pages. In this round I re-read pp. 448, 449, 450, 451, 452, 453, 454 and 455 against the edits.
| item | change | page evidence |
|---|---|---|
| 1. S1 | The `basu_2008_table_ii_iii_author_calculated_activities` context record was removed. Its values now sit in 216 `activity_coefficient` observations: 108 `FeO` (`quantity: activity`, ids `basu_2008_t{ii,iii}_sNN_a_feo_{1873,1923}k`) and 108 under a new species `O` (`..._h_o_...`). Each carries the experiment FK, T_range_K [T,T], a row locator, the solution's printed mass-% composition (CaO, SiO2, FeO, MgO, P2O5; no mole fractions invented), and the printed standard state. Added `reference_state_endmember_formula` (FeO / O), so the migrator types the reference state instead of queueing it. The measured compositions stay as context, and each row is repeated in its observations. The "no suitable Quantity" amendment is in the package's extraction-report.md only; there is no such text in any tracked file (rg: 0 hits), so nothing tracked needed deleting. The Ar `mNm3 min-1` note stays. | p451 Table II rows 1-63; p452 Table II continued (64-65) and Table III (1-43); footnotes "Standard state for calculation of a(FeO) is pure liquid 'FeO'" and "...hO is the unit activity coefficient of [O] at infinite dilution" (p452) |
| 2. B5 | a(FeO): `method_class: measured_reduced`, with derivation.relation = Eq. [1] (ΔG° = -121,983.61 + 52.26T J/mol = -RT ln a(FeO)/h[O]) and derived_from/inputs = its h_O row. h_O: `measured_tabulated`, with a lineage note: h[O] = [mass pct O] x fO, log10 fO = Σ e_i^O [mass pct i] [29]; the inputs are not printed (not_published). **Open question below.** Not `model_derived` anywhere. | p449 Eq. [1] and the h[O] relation; p450 log10 fO and standard states |
| 3. B1 | Table II solutions 64 and 65 → page 452, pdf_page_index 5, table 'II (continued)'. Series block locator → '451-452' / '4-5'. | p452 "Table II. Continued": 64 = 29.49/12.42/40.97/8.76/3.83/0.524/0.11; 65 = 52.41/21.2/14.76/5.05/4.76/0.358/0.075 |
| 4. B2 | Figs 1-2 → 449; 3-6 → 453; 7-10 → 454; 11-13 → 455 (12 corrected; 13 was already right). Block locator '449-455' / '2-8'. Fig 2 subject now reads "slag compositions at 1873 K and 1923 K on the (CaO + MgO)-(FeO + Fe2O3)-(SiO2 + P2O5) pseudo-ternary, with 1873 K liquidus curves [28]". | Captions seen on p449 (Figs 1, 2; Fig 2 legend "o 1873 K, X 1923 K"), p453 (3-6), p454 (7-10), p455 (11-13) |
| 5. B3 | Quote pages: CaO no-correlation → 450; X(FeO) < 0.1 → 452; temperature → '452-453'; "all the workers, except Bodsworth" → 453; Fig 8 caution → 454; negative correlation → 454; Figure 10 → 454; "92 and 93 pct" → 455; "adequately estimated" → 455. The duplicate Fig 8 caution was deleted. Two quotes that cross a page break also got ranges: "Increase in FeO concentration..." → '453-454'; "Basicity ... statistically insignificant" → '454-455'. | p450 right column bottom; p452 text under Table III; p453 right column; p454 left column (caution printed once); p455 right column |
| 6. B4 | The unattributed Kishimoto quote and the two Turkdogan and Pearson sentences were moved into a new context record, `basu_2008_attributed_literature_statements` (`quoted_attributed`). The quotes now run from "A similar trend was reported by Kishimoto and co-workers, ..." to "[22]", and from "Turkdogan and Pearson had reported ... [9] At higher concentrations of SiO2, ...". | p450 right column top (Kishimoto [22]); p450 left column (Turkdogan and Pearson [9]) |
| 7. D1-D18 | All 18 carried verbatim with locators. 7 attributed statements (D1, D2, D3, D4, D5, D6, D11) are in the attributed record, with attribution and reference numbers. The other 11 (D7-D10, D12-D18) are in `basu_2008_qualifying_and_directional_statements`. D14 replaces the bare first-clause quote. The unattributed list's locator is now '447-456'. Claims: 47 → 53 unattributed (−1 duplicate, −1 superseded D14 clause, −3 moved to attributed, +11) plus 9 attributed. | p447 Sec. II (D3); p448 (D1, D2 into p449); p450 (D4-D9, D18); p453 (D10-D13); p454 (D14); p455 (D15-D17) |
| 8. E1-E5 | E1: characterization now names SEM with EDS and EPMA, at pp. 448-449. E2: `flow_sccm` reason changed to `would_invent`, with a note that the printed 0.15-0.20 mNm3 min-1 needs an assumed normal reference state to become sccm. E3: provenance pdf_page_index made 0-based (t1 1, t2 '4-5', t3 5). E4: two `source_internally_inconsistent` notes: r² = 0.93 / 89 pct against "92 and 93 pct", and the ppm-O statement against the missing ppm column. E5: Table I cells made verbatim (lower-case initials as printed, "(separately)", "1.8–2.0, for all FeO levels", "upto", no comma before "as well as"; "Measurement" keeps its printed capital). | p448 Table I; pp448-449 Sec. III; p450; pp454-455 |

### Added in this finishing round, beyond the staged edit set
- The h_O lineage note said the inputs were "printed as ppm ... not printed", which contradicts itself. It now says "[mass pct O], which p. 450 says is expressed in ppm by mass". This changes wording only, in all 108 rows.
- `reference_state_endmember_formula: FeO` on the a(FeO) rows and `O` on the h_O rows. Without them, all 216 observations queued with "source standard_state does not name one reference endmember". With them, the queue is 0 and the reference states are typed.
- `fidelity_samples` (7). The fidelity gate requires them now that the extract has observations. Without them the validator failed: "fidelity_samples required when observations are present". Each pin was re-read from the page image this round: II-1 a(FeO) 0.306 and h_O 0.064 (p451); II-64 a(FeO) 0.524 and II-65 h_O 0.075 (p452); III-20 a(FeO) 0.36 and h_O 0.092, and III-43 a(FeO) 0.289 (p452).
- Further spot checks against the images, all matching: II-30 (35.06/19.75/23.11/12.15/4.16/0.353/0.074); II-63 (49.07/22.06/16.52/7.25/3.33/0.358/0.075); III-1 (38.99/24.42/17.33/11.68/3.35/0.299/0.077); III-43 (17.39/12.0/36.85/20.46/1.08/0.289/0.074).

## Open question for main
**h_O is typed `measured_tabulated`, not `measured_reduced`.** The authors calculated h[O] from their (unprinted) steel oxygen analysis and composition, using interaction
parameters [29]. So, physically, it is a reduction of measured equilibria, like a(FeO). It is typed `measured_tabulated` because no in-extract parent can be named:
the [mass pct O] and metal composition are not printed, and `measured_reduced` normally carries derived_from/derivation.inputs. a(FeO) is `measured_reduced`, with its h_O row
as the parent. Please confirm. The alternatives are (a) keep it as is; (b) `measured_reduced` with derivation.relation = "h[O] = [mass pct O] x fO" and inputs typed absent (not_published), if the schema allows a reduction with typed-absent inputs; (c) your cho-suito `calculated` precedent.

## P0s
None. All 26 transcribed-value checks were already clean at 2305fff9, and no printed number changed.

## Notes
- Dropbox: the request named `.../Dropbox/Regolith Processing/regolith-flight-ferry/from-empirical/`, but SEAT-COMMON (batch8) names `.../Dropbox/Starship Mission Design/Regolith Processing/regolith-flight-ferry/from-empirical/`.
  These are two different folders, and REVIEW-basu was delivered to the second. This STATUS is in both.
- Licence in the sidecar is still "not recorded — verify". The sidecar predates this branch and was not touched.
- Sparse worktree `~/Repos/regolith-corpus/worktrees/fix-basu-2008-feo-steelmaking` was removed after delivery. Mac free disk stayed at 57 GB, above the 30/35 GB floors.
