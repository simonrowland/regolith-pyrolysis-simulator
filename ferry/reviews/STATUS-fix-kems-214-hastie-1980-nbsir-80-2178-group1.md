# STATUS (group 1 of 5): fix kems-214-hastie-1980-nbsir-80-2178

**From:** regolith-empirical (fix seat, batch4 B)   **At:** 2026-10-05 ET
**Start:** 104fa4cb4b0d5d7ea8803a0743b043309d88107a (verified = origin/hunt/kems-214-hastie-1980-nbsir-80-2178 before work)
**Pushed tip after this group:** 4103bbe5c5630fba04d581502dc0291b4b0b36b6 on hunt/kems-214-hastie-1980-nbsir-80-2178 (mac-studio-256-1:Repos/regolith-corpus.git). Not merged to main.

## Group plan (review.md items, N = 113: 84 page-inventory rows with gaps + 29 cross-cutting corrections)
1. Main report PDF 11-31, t1, t2 — **done in this push**
2. Appendix A apparatus/theory PDF 33-56, t3, TMS bench/configurations — pending
3. Appendix A NaCl PDF 57-70, t4-t7, Pt NaCl cell — pending
4. Appendix A Na2SO4 PDF 71-85, t8 — pending
5. Appendix B PDF 87-112, t9-t11, MgO/slag facts, species identities, absence audit — pending

## Group 1 items (11 of 113 done)
Pages read from 220 dpi renders: PDF 11-20, 25-31 (captions), plus PDF 101 and 106 for two cross-group edits.
- PDF 11 (700-1500 K, 1-70 atm), PDF 12 reactions (1)-(4), PDF 15 reactions (5)-(6): draft context **kept** (verified digit by digit).
- PDF 13 / printed 3: Ta tube furnace and HPMS/TMS wording added to TMS bench apparatus_family / cell_material_and_liner (draft lacked them). Pressure-limit / target-condition locators (index 12 / printed 3) verified correct; target pressure `1` replaced by an exclusive lower bound (print says "> 1 atm"), T ~1700 K marked approximate.
- PDF 16 / printed 6: context **redone** — predominant K and O2, room-air oxygen absorption, 1500 K burst, slope 0.5, apparent equilibrium, KOH lesser than reaction (6), 1 atm H2 test case, 87 % as K.
- PDF 17 / printed 7: context **redone** — draft claimed "at H2 < 5x10^-3 atm, K pressure decreased with increasing H2", which is NOT printed; replaced with the printed 10 vol% H2 / corrosion limit and the O2-scavenging statement, plus footnote c notation.
- PDF 19-20 / printed 9-10: Table 2 footnotes a/b, K predominant, O2 much less than observed, K temperature-insensitive; non-ideal/non-monotonic summary and >1 atm future work.
- Main Figures 1-8 (PDF 23, 25-31): figure-only context now enumerates every caption with its conditions (1794 K KMS; 1655 K / 0.18 atm; 1500 K and 1570 K / 0.21 atm; capillary nozzle). No curves digitized.
- t1 (Table 1, 20 cells) and t2 (Table 2, 12 cells) re-checked against PDF 17/19: all match; provenance now records printed pages 7 and 9 (A1). Table 1 K2O activity remains "experimental input, no coefficient printed" (A19, verified).
- Slag sample purity absence narrowed to PDF 15 / printed 5 qualitative "greater purity and homogeneity"; post-run composition absence relocated to the explicit PDF 101 / B15 sentence (verified on image).
- Validator fix needed for any push: the draft's `type: ion_intensity` observation is not a legal type (validator FAIL at 104fa4cb). Removed; Mg 2029 K pressure now `measured_tabulated` with printed k, I+T, A and relation, no fabricated parent (t10 verified on PDF 106).

## Draft (104fa4cb) disposition so far
- extract yaml: PDF 11/12/13/15/18 contexts kept; PDF 16/17/19/20 redone; ion_intensity observation redone. Remaining draft contexts (PDF 33-112) are verified in later groups.
- t3/t8/t9 csv and t8 provenance: not yet verified (groups 2, 4, 5).

## Checks at 4103bbe5 (green 61ec839da, ~/ci-scratch/regolith-green-ro, PYTHONPATH=that clone)
- Migrator._migrate_extract + finalize: 1 work, 3 benches, 3 experiments, 61 observations, 121 context rows; **hard issues 0** (183 advisory identity_incomplete only).
- tools/validate_literature_extracts.py --check-fidelity-match <worktree extract>: OK, exit 0 (was FAIL at 104fa4cb).
- corpus tools/test_ledgers_valid.py: exit 0.
- engines/engines.local.toml exists in the green checkout.
