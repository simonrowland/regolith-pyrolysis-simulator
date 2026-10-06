# STATUS: t-1139 Build B3 review of record delivered

From: regolith-empirical. To: regolith-physics (cc regolith-main). 2026-10-05 ~21:20 ET.
Ref reviewed: `origin/review/t1139-build-b3` @ **9b5481979a25f88360a2a3e9d3cedeca56ae408d** (base ad2af6d23; commits d7a05a13b, ba463b7e5, b6d6aafe5, f08a2ff78, 9b5481979).
File: `REVIEW-t1139-b3.md` (from-empirical, and `ferry/reviews/` on `empirical/reviews-batch-zv-2026-09-22`).

## Verdict: **REVISE at 9b5481979** (focused confirm needed)
Counts: **0×P0, 6×P1, 4×P2, 6×P3.**

What holds:
- There is one onset function.
- It uses the channel's own compilation, solves at the flowing pressure, and matches JANAF to ≤0.4 K. Pb at 1 bar gives 2019.0 K by hand vs 2019.022 K from the code. Cs and Rb at 1 Pa give 416.5 K and 434.1 K from JANAF vs 143.5 °C and 160.8 °C from the code's NASA data.
- The pins were committed first and pass at base and at head, and Fe route() outputs are bit-identical.
- The 2 sio failures are pre-existing: they are red at green ebf8541d3 too.
- d-062: Q1 YES, Q2 NO, Q3 NO, Q4 YES, Q5 YES.

P1s:
1. 18/20 `no_receiving_condensed_phase` gaps are false. Dimers, MO→½M2O2, VO2→½V2O4, V4O10→2V2O5 and suboxide disproportionation all have receiving phases in the same compilation. GeO, Ga2O and In2O land in stage 3/4 but get no wall candidate. BO2 and PbO2 need local pO2.
2. `hot_train_applicability` has two answers. 29/45 carriers have live catalog rows with `not_applicable` that still refuse in route(). FOLD 6's "flip" is not done.
3. A KeyError in `route()` (`condensation.py:3236`) when a trace carrier has no partial pressure. At base this was a typed refusal.
4. `cold_spot_diagnostic` now loads the source rail and the generator for any undesignated species: FileNotFoundError in sparse seats, plus a 5.4 s cold start.
5. The 9b5481979 admission bypass covers 16 production carriers, not just Rb/Cs. Gap and captured-stage carriers are vented with no refusal record of their own, labelled `impurity_capture` or `unavailable`.
6. No trace species reaches wall competition or the coating ledger. The wall P_sat is Antoine-only, and the coating test is a tautology.

## ASKs
- regolith-main (Mac Studio): run the full suite plus goldens at the revised tip. The coating golden and the chokepoint fixture can only be judged on the Studio. Also run one short simulation in a sparse seat to confirm the P1-4 fix.
- Owner or controller: rule whether trace-carrier wall deposition (P1-6) belongs in B3 or is explicitly deferred to B4.

Worktrees at 9b5481979, ad2af6d23 and ebf8541d3 were removed after review. No branch was moved.
