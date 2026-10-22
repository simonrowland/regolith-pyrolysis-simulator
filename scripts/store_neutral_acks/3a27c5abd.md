554fd0e07, b746bcf30, 3a27c5abd: t-1275 cell oxygen balance for every
engine (d-142). These pass the requested oxygen to the alphaMELTS
activity call in generators/bench.py, solve the vapour oxygen balance
from engine pressures in score.py and oxygen_balance.py, and close the
alphaMELTS oxygen-balance projection. The landing train's full regen of
798c1b672 (gate b133, studio-3) produced no store change (rows_in=50497
observations=126296 works=349 experiments=3823 queue=80926
hard_issues=3334; j01 7/7). This acknowledges STORE outputs only; the
scoring change (cell-balance residuals per engine) is covered by the
commits' tests.
