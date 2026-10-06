# STATUS: fix of corpus extract richter-2008-cai-like-liquids-lpsc-abstract

**From:** regolith-empirical (fix seat, REQ-corpus-batch12 section B)   **To:** regolith-main   **At:** 2026-10-05 ~22:52 ET
**Review applied:** package `review.md` (first review of 570192a9: rows checked 62, mismatches 3, printed numbers not carried 0; R1–R4 required, O1–O4 recommended).

- **Start tip:** `570192a9312a3b1a0f4a398da178b9e6c0c5bc8d`. I ran `git fetch origin hunt/<sid>` and origin matched the REQ before I edited.
- **New tip on the mirror:** `ab53e47dc936daba798345c9c762258898b72576` on `hunt/richter-2008-cai-like-liquids-lpsc-abstract`. It is a fast-forward push from 570192a9 (no force, no -r2 branch); `git ls-remote` confirms it.
- **Commit:** a single commit, `fix richter 2008 CAI liquids abstract per first review of 570192a9`. Explicit pathspecs `extracts/<sid>.yaml` and `ledger/<sid>.yaml` only (+59/−9), no trailers. Author: Simon Rowland.
- **Items fixed 4/4 required** (R1–R4), plus 3 of 4 recommended (O2, O3, O4). O1 is left for main's ruling (see below).
- **Corrections count:** 3 mismatches corrected (M1, M2, M3) and 1 uncarried qualifying statement set carried (R4: 4 verbatim quotes plus a reworded process).
- **Hard issues: 0.**
- **No confirm review follows (main merges directly).** I checked every item myself against the page images. I rendered both pages again with `pdftoppm -r 250` and read all four quadrants of each page. I also read zoomed crops of the Figure 2 x axis and the Figure 3 top axis.
- **PDF sha256:** `a6f675576c888f2d0d20f2a3092c053a5d8ec378c4fa164984a77aca93d1364d`, which matches the sidecar.

## Per-item table

| Review item | What changed (extract `extracts/<sid>.yaml` unless noted) | Page evidence (250 dpi) |
|---|---|---|
| **R1 / M1** Fig 2 axes on the Fig 3 row, with unprinted ticks | **Fig 3 row** (`richter_2008_figure3_and_isotope_comparisons`): deleted `plotted_axes_as_printed` {x_negative_ln_fraction_24Mg [0,0.5,…,3], y_1000_ln_R_over_R0 [0..40]}.<br>**Fig 2 row** (`richter_2008_fig2_mg_isotope_series_figure_only`): added `plotted_axes_as_printed` {x_negative_ln_fraction_24Mg [0,1,2,3], y_1000_ln_R_over_R0 [0,10,20,30,40]}, plus `plotted_axes_note` (labelled ticks only; the minor ticks are unlabelled).<br>**Recommended move done:** `figure_3_axes_as_printed` is removed from `richter_2008_numeric_comparative_claims` and now sits in the Fig 3 row as `plotted_axes_as_printed` (x 4.5–6.5, top T 2100…1200, y 6–20; values unchanged). I also added the axis names as printed: `10000/T(K)`, `T(°C)`, `1000(1−α)`. | p2 Fig 2: x axis labelled 0, 1, 2, 3, with unlabelled minor ticks at 0.5, 1.5 and 2.5 (zoom crop); y axis labelled 0, 10, 20, 30, 40.<br>p2 Fig 3: x axis 4.5, 5.0, 5.5, 6.0, 6.5; y axis 6–20 in steps of 2; top axis 2100 2000 1900 1800 1700 1600 1500 1400 1300 and a clipped "120". |
| **R2 / M2** non-verbatim quote | In `richter_2008_numeric_comparative_claims.values.statements_as_printed`, the quote now reads exactly "...the often used value corresponding the to inverse square root of the mass of the isotope (shown as the “ideal” value in Figure 3." I added `printed_wording_note` (the print reads "corresponding the to" and has no closing parenthesis). | p2, right column, Isotopic Fractionations, last sentence before Summary: "corresponding the to inverse square root of the mass of the isotope (shown as the “ideal” value in Figure 3." (no ")") |
| **R3 / M3** wrong figure scope | `richter_2008_prior_vacuum_comparator_scope.values.comparison_scope` now says:<br>• Fig 1 compares vacuum and H2 rates, with data from [2] and [3].<br>• Fig 2 shows only residues evaporated in 1.87×10^-4 bars H2: open symbols are earlier measurements by [2] ("Richter et al. [2]"); small solid symbols are new high-precision data ("This work"). Fig 2 contains no vacuum isotope data.<br>• Fig 3 compares the H2 value with vacuum data and the fit to vacuum from [3]. | p1 Fig 1 caption "Data from [2] and [3]".<br>p2 Fig 2 caption "residues evaporated in 1.87×10^-4 bars H2. Open symbols are earlier measurements by [2] and the small solid symbols are new high-precision data"; Fig 2 legend "This work" / "Richter et al. [2]".<br>p2 Fig 3 legend "vacuum [3]", "fit to vacuum [3]". |
| **R4** uncarried qualifying statements | **`richter_2008_experiment_and_method_context`:** `process_as_printed` is reworded. It now describes isotopic re-measurement ("we remeasured") of a series of existing CAI-like residues evaporated in 1.87×10^-4 bars H2, which [2] measured earlier (Fig 2 caption), and says the abstract describes no new evaporation runs.<br>**New `statements_as_printed`, 4 verbatim located quotes:**<br>(a) "There already exist a wealth of high-precision data ... vacuum evaporation residues [3]."<br>(b) "A key question is whether the kinetic fractionation factors ... finite hydrogen pressure."<br>(c) "To address this with high-precision isotopic data, we remeasured ... evaporated in 1.87×10^-4 bars of hydrogen. The vacuum experiments are described in [2] and the magnesium isotopic measurements ... as described in [3]." Its locator note records that the sentence runs onto p2 and that p2's first line repeats "chips by". The repeated words are given once.<br>(d) "Figure 1 illustrates the effect of hydrogen pressure by comparing the evaporation rate in vacuum to that in 2×10^-4 bars of hydrogen." | p1, right column, Isotopic Fractionations: (a)–(c) read word for word. p1 ends "on the dissolved chips by"; p2 begins "chips by multicollector ICPMS as described in [3]".<br>p1, right column, Elemental Fractionations: (d) read word for word. |
| O1 sidecar `original_path` convention | **Not changed.** This is main's ruling; the sidecar is untouched. | n/a |
| O2 legends | **Fig 1 row:** `legend_as_printed` = filled red circle J_Mg and filled blue square J_Si for PH2 = 1.87x10^-4 bar; open red circle J_Mg and open blue square J_Si for vacuum.<br>**Fig 2 row:** filled red circle "This work"; open red circle "Richter et al. [2]".<br>**Fig 3 row:** "vacuum [3]" (filled red circle), "fit to vacuum [3]" (red line), "CAI crystallization range" (grey band, no printed numbers), "PH2=1.87x10^-4 bar" (filled blue circle), and the boxed "“Ideal” value, for α = (23.985/24.986)^0.5" beside the thick black line. | p1 Fig 1 legend; p2 Fig 2 legend; p2 Fig 3 legend (read from the images). |
| O3 ideal α | Fig 2 row `ideal_alpha_note`: the print gives 0.97980, while (23.985/24.986)^0.5 = 0.979764. Both are carried as printed. The 4×10^-5 difference is not tagged source_internally_inconsistent. | p2 Fig 2 blue box "α = 0.97980"; p2 Fig 3 legend formula. The arithmetic was recomputed: 0.9797640. |
| O4 clipped Fig 3 top label | Fig 3 row `plotted_axes_note`: the last label is clipped at the frame and prints as "120". The 1200 is read from the 100 °C tick spacing, and the 1200 entry is kept with this note. | p2 Fig 3 top axis (zoom crop): "... 1400 1300 120" against the right frame. |
| Ledger | Added `stages.extracted.fix_note` describing the fix round. Counts are unchanged (observation_rows 0, context_rows 8). | n/a |

Untouched and re-confirmed (no change needed): α 0.98797 ± 0.00022, T 1500 °C, pH2 0.000187 and 0.0002, threshold 1e-7, the Hertz–Knudsen relation, all 12 typed absences, references [1]–[5], and the Fig 1 axes. All are consistent with the renders.

## Acceptance (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461, on the Mac)
- **Green clone:** `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, used read-only. Commands ran with `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` and `PYTHONPATH=~/ci-scratch/regolith-green-ro`. `engines/engines.local.toml` exists.
- **Migrator + finalize:** `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/<sid>.yaml); finalize()`.
  - Result: 1 work, 0 experiments, 0 observations, 8 context rows.
  - **Hard issues after finalize: 0** (0 issues in total).
  - There is one expected queue entry, `extract yielded no observations` (context-only extract).
- **evidence_for:** `figure_only` gives FIGURE_ONLY and `measured_reduced` gives MEASURED_REDUCED. No queue reason, none unknown.
- **Payload survival** (serialised `context_by_work`):
  - Present: 0.98797, 0.00022, 0.97980, 0.000187, 0.0002, 1e-07, the ideal formula, and the corrected quote ("corresponding the to").
  - Present: all 4 new R4 quotes, the new comparison_scope ("Figure 2 contains no vacuum isotope data"), the reworded process, the legend items ("CAI crystallization range", "This work", "J_Si, vacuum"), 0.979764, and every apparatus typed absence.
  - The Fig 2 row axes serialise as `[0, 1, 2, 3]`. The Fig 2 axes are no longer present anywhere with the 0.5 steps.
- **Fidelity validator:** run from green-ro as `tools/validate_literature_extracts.py --check-fidelity-match --show-warnings <abs worktree path>/extracts/<sid>.yaml`. Output: `OK: 1 extract file(s) valid`, exit 0.
- **Corpus `tools/test_ledgers_valid.py`:** `python -m pytest -c /dev/null -q -p no:cacheprovider tools/test_ledgers_valid.py` gave 707 passed. The script run directly exited 0.
- **Absolute paths:** `rg "/Users/|/private/"` over the extract, ledger and sidecar finds no hits.

## Seat hygiene
- **Worktree:** a sparse worktree on the Mac at `~/Repos/regolith-corpus/worktrees/fix-richter-2008-cai-like-liquids-lpsc-abstract`.
  - Built with `git worktree add --no-checkout -b hunt/<sid>`, tracking origin.
  - Sparse set: `/*`, `!/raw/*/*`, `!/text/*/*`, `/raw/*/sidecar.yaml`, `/raw/<sid>/*`, `/text/<sid>/*`.
  - build_index.py and migrate_pilot_extracts.py were not run.
  - **Removed after the push** (`git worktree remove`), with no leftovers.
- **Mac disk:** 57 GB free at the start and 65 GB free at the end.
- I did not merge into green/main or mirror main, and I did not arm any Mac listen pools.

## Blocked / open
- Nothing is blocked.
- **O1** (sidecar `original_path` convention) is left for main to rule on.
- **AMENDMENT PROPOSED** (carried over from the extraction report and endorsed by the review): a supported quantity for the kinetic Mg isotope fractionation factor, so that α 0.98797 ± 0.00022 can become a scored observation.

!COMPLETE: fix-richter-2008-cai-like-liquids-lpsc-abstract — ab53e47dc936daba798345c9c762258898b72576 on hunt/richter-2008-cai-like-liquids-lpsc-abstract, items fixed 4/4 (+O2/O3/O4), corrections 3 mismatches + 1 carry, hard issues 0
