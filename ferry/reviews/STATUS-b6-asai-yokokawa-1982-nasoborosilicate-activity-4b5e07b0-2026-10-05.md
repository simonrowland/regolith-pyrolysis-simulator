# STATUS: batch6 B1, Asai & Yokokawa 1982 (Na2O–B2O3–SiO2 emf): DONE (draft extract on hunt branch)

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~23:30 ET
branch: hunt/asai-yokokawa-1982-nasoborosilicate-activity (corpus; origin mac-studio-256-1:Repos/regolith-corpus.git)
tip: 4b5e07b0a26aeccfaca726120d8d70b685f6030c (pushed fast-forward from b663bc1b; ls-remote verified)
commits: 4520c952 claim (takeover) / 15837404 extract / 4b5e07b0 release
INDEX: not regenerated (sparse tree)
files: extracts/<sid>.yaml; tables/<sid>/t1.csv, t2.csv, t1/t2.provenance.yaml; text/<sid>/decode-note.md; ledger/<sid>.yaml (no PDFs or PNGs)
rows: Table 1 has 374 emf points in 44 blocks; Table 2 has 44 rows (ΔG/ΔH/ΔS at 1200 K). Extract: 0 observations, 112 context rows (88 numeric table rows + 24 other), 1 bench, 1 experiment
checks (green 61ec839da, which descends from c52b9265):
- Migrator _migrate_extract + finalize complete; hard issues 0; queue 1 ("extract yielded no observations")
- evidence_for: measured_direct / measured_reduced / quoted_attributed / figure_only, no unknown
- 44/44 reduced rows have lineage parents and a derivation
- payload survival: 1056 numeric cells, 0 non-numeric or missing; bench facts survive (CellMaterial PT + AL2O3, CA thermocouple, Cu/Sb calibration, potentiometer, 1 mm hole/wire)
- validate_literature_extracts.py --check-fidelity-match (absolute worktree path): OK (5 Table 2 pins)
- tools/test_ledgers_valid.py: 687 passed
P0 (draft b663bc1b was wrong; corrected from the images):
- t1 was missing 11 points
- about 80 dropped minus signs
- t2 had 2 ΔH signs wrong, and the m=4.0 row was misassigned ((6-m), not (1.5-m))
encoding: both tables are numeric context per the STANDING RULING. Observations with the unsupported quantities migrate as UNAVAILABLE (migrate.py:4809–4812, :7960). AMENDMENTS 1–4 are in the REPORT.
open: outliers −51.1 and 182.5 carried as printed; is context-only acceptable; Table 2 ± undefined; claim takeover was under 12 h (release.py + re-claim per REQ B1)
engines/engines.local.toml: EXISTS (green-ro)
cleanup: worktree removed; Mac free space 63 GB
report: REPORT-b6-asai-yokokawa-1982-nasoborosilicate-activity-4b5e07b0-2026-10-05.md

!COMPLETE: extract-asai-yokokawa-1982-nasoborosilicate-activity — 4b5e07b0a26aeccfaca726120d8d70b685f6030c, tables 2, rows 418, cell Pt (+Al2O3 junction), calibration CA thermocouple at Cu/Sb melting points; migrator completes, hard issues 0
