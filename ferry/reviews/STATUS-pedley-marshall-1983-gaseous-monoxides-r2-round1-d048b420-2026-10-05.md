# STATUS: batch6 B2 pedley-marshall-1983-gaseous-monoxides r2, round 1 (Tables 6–7): DONE, pushed

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~22:35 ET
branch: hunt/pedley-marshall-1983-gaseous-monoxides-r2 (corpus mirror; new branch from origin/main 0dac8263, which contains LAND 284e86a3)
tip: d048b4207eda6f774e7d400b210a263747a99eed (pushed; fetched back and verified equal)
INDEX: not regenerated (sparse tree)
round scope: Tables 6 (p. 984, kcal/mol) and 7 (p. 985, kJ/mol), 82 value rows each (164) + 7 dash cells each; located compilation_table context; 0 new observations (D0 has no observation home)
checks (green 61ec839da, ~/ci-scratch/regolith-green-ro; engines/engines.local.toml present there):
- validator --check-fidelity-match: OK: 1 extract file(s) valid
- Migrator + finalize: hard issues 0; 132 observations (unchanged, T=298 on 132/132); 1 experiment; 4 context records
- payload: CSV == migrated context, 0 mismatches; numeric cells 155 per table, non-numeric 0
- test_ledgers_valid: 723 passed
findings kept as printed (cross_table_notes):
- Table 7 = Table 5 on all 68 shared formulas; Fe prints a clean 386 [17], which corroborates the T5 "38 617" interpretation
- Table 6 Au 52.3 vs Table 4 52.5; Cd (55)/Re (149) vs 55.4/148.8; 21 T6 uncertainties rounded vs T4; S footnote a vs T4 ±1.0
- 14 species only in T6/T7: CO NO O2 FO ClO AsO BrO RbO IO TlO TcO HgO PmO PaO (H&H footnote a/b, or author estimates)
next rounds: Tables 8–9 (pp. 986–996), Tables 10–11 (pp. 997–1007), Appendix II (pp. 1017–1021); Tables 1–3 / App. I, III as context
report: REPORT-pedley-marshall-1983-gaseous-monoxides-r2-round1-d048b420-2026-10-05.md
