2e69a5faa, 4f9402dea, 31bb956c5, cdd49ec85: t-1347, the hero deck. These
add scorer-owned deck axes in score.py (solved oxygen conditions, typed
deck records, the NBO/T domain bound) that the hero plotter reads.

The landing train's full regen of b143 (tip 800c01e37 on green 95a870f36)
produced no store change (observations=126296 queue=80926
hard_issues=3334; j01 7/7). This acknowledges STORE outputs only; the
scoring and diagnostic changes are covered by the commits' tests.
