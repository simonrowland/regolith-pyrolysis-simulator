23bee5e7b, 8c154cdb0, ed246fcb7, 62224868f: b-932, the alphaMELTS redox
handoff. 23bee5e7b moves the multivalent-melt rule into
simulator/melt_backend/base.py (score.py and generators/bench.py import
it). 8c154cdb0 made score.py require oxygen for every modeled quantity
over a multivalent melt; 62224868f removes that branch again, restoring
the identity-profile rule. ed246fcb7 keeps alphaMELTS's not-an-input
oxygen mode in the melt backends and
diagnostic_helpers/binary_pot_battery.py; its score.py change is a
comment.

The landing train's full regen of b143 (tip 800c01e37 on green 95a870f36)
produced no store change (observations=126296 queue=80926
hard_issues=3334; j01 7/7). This acknowledges STORE outputs only; the
scoring and diagnostic changes are covered by the commits' tests.
