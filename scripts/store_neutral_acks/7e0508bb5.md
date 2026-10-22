7e0508bb5, b37e28bac, 297df02b9: t-1413 (d-223), printed phase
compositions, distribution ratios and S/Cl capacities. They add the
enums, identity axes, migrate readers and score.py paths for those rows.
The landing train's full regen of b148 (tip 20a005ad5 on green 850a09e93,
studio-3) was a fixed point: no store change. This acknowledges STORE
outputs only; the new rows' scoring is covered by the commits' tests.
