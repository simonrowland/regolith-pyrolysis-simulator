# STATUS: t-1139 Build B2 focused confirm delivered (regolith-empirical → regolith-physics)

Written 2026-10-05 ~22:00 ET by a VPS seat.

- **Verdict: REVISE 07dc6017c03c236287e779d62fec6b7cbe5f3213** (origin review/t1139-build-b2; tip verified; base ad2af6d23).
- **Sol's six findings: 4 FIXED, 2 PARTIAL, 0 NOT-FIXED.**
  - FIXED: P1-2, P1-4, P1-5, P2.
  - PARTIAL: P1-1 and P1-3. The γ conversion is right at X = 1, but X is not transformed with γ.
- **New findings: 0×P0, 3×P1, 3×P2, 6×P3.**
  - P1-A: the mole-fraction basis. The cation X is used with molecular-basis γ (×2.0 in the test melt, ×1.77 in basalt), and the In2O3 and InO1.5 spellings disagree by 1/X.
  - P1-B: Cu2O/CuO0.5 get UpperBound γ = 1 although all four Cu2O rows give γ = 1.56–245.
  - P1-C: the shared MELT_OXIDE_CATIONS_PER_FORMULA edit moves live major X by 1.2–4.6e-4 relative in the six trace-bearing feedstocks, with no pin.
- **Answers to (a)–(e):**
  - (a) Cu2O has no homologue. GeO2's homologue SiO2 is itself unresolvable. Neither parent has a banded row, while Li2O and PbO each have one FactSage 1800–2200 K row.
  - (b) The derivation and all the numbers check, but x and γ do not transform together away from X = 1.
  - (c) Truthful at row level only. Fegley 2023 states the convention (Raoultian, pure liquid oxide) and the molecular X basis in its methods text. The primary-source component basis for Cs2O/Rb2O was not checked.
  - (d) The test passes at base, tip and green on the VPS (1.16–1.40 s). B2 adds about 4 ms to that path. Consistent with "pre-existing", but red not reproduced.
  - (e) Q1 yes (minor, named); Q2 no; Q3 no; Q4 no (P1-C); Q5 no.
- **Tests:** 819 passed, 0 failed across 11 targeted files at the tip. Three mutation proofs; the tree was restored clean.
- **ASK (Studio):**
  - full pytest at the tip vs base;
  - a golden diff on the six trace-bearing feedstocks (P1-C);
  - the single vapour-intent test 3× at base and tip;
  - the scorer regression on existing rows without the 5 s wall.
- **Deliverable:** REVIEW-t1139-b2-confirm.md, sha256 d9504cf58993536bc49e234a117244967cd06c12a4dac8c323def00c0ede406e.
- **Probes:** ferry/reviews/t1139-b2-confirm-probes/ on the mailbox branch.
