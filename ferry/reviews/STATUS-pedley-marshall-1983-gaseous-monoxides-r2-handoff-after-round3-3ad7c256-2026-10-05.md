# STATUS: batch6 B2 pedley-marshall-1983-gaseous-monoxides r2: rounds 1–3 DONE; round 4 (Appendix II) NOT STARTED, handed back

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~23:55 ET
branch: hunt/pedley-marshall-1983-gaseous-monoxides-r2, tip 3ad7c256044d676698779b501257207e916adf5f (pushed)
rounds:
- r2-1 Tables 6–7: d048b420
- r2-2 Tables 8–9: 59c13894, provenance count fix cd7002b1; re-audited in round 3, 0 changes
- r2-3 Tables 10–11: 3ad7c256
The branch is ready for LAND review of rounds 1–3 as it stands. Each round's REPORT/STATUS is in from-empirical/.

round 4 (Appendix II, Molecular Parameters for Gaseous Monoxides, pp. 1017–1021, PDF pp. 52–56) is not started. No partial work was committed. Reasons it is a separate session:
- size: about 78 species blocks, about 125–200 electronic-state rows (term symbol, g, Te, ωe, ωexe, Be, αe, De, many blank cells), about 70 free-text source/comment lines (e.g. "TE AND WE FROM 72SMO/MAN, BE FROM 69BRE/ROS"), and a page of explanatory prose with the lanthanide estimation note (p. 1021)
- no internal cross-check: unlike Tables 6–11 there is no unit-pair table, so every value needs two independent reads plus a full visual pass against 450 dpi renders. The text layer is poor here (e.g. "0.016Z80", "SPII", "ChlU(li)").
- a schema choice for main: these are input spectroscopic constants (mixed literature values and author estimates), not author-calculated functions. Main should decide whether they stay context-only (compilation_table; suggested row_method_class per state: compilation_assessed, or estimated where the comment says so), as Tables 1–3 and Appendices I and III do.
suggested approach for the next worker: keep one row per (species, state), with the printed comment line attached to its species, and blanks kept as null. Use dual OCR plus a visual read of every row, because there is no arithmetic check apart from g against the term multiplicity.
worktree: removed. Mac scratch kept at ~/Repos/regolith-corpus/.ferry-tmp/pedley-r2/ (450 dpi renders, OCR, gen/ scripts incl. resolve.py and strip_all.py) for reuse. It is safe to delete.
