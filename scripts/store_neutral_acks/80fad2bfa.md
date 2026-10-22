80fad2bfa: the OpenIMCC af0f2a2 pin bump. The Sossi residue prediction path
(simulator/battery/residue.py and score.py _sossi_residue_prediction) sends melt evaluation
only the melt pack's oxides and passes the existing UNCERTIFIED MnO activity extension to the
new Mn gas channel, with an OUT_OF_CERTIFIED_BAND notice. Migration does not execute the residue
prediction path (see the 23df83343 entry), and its only score import is the unchanged
parse_species_formula. Store receipt to be confirmed by the landing gate's full regen on the
merge with green (expected: 0 files changed under data/). This acknowledges STORE outputs only;
the residue scoring change is covered by the commit's tests.
