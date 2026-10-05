# STATUS: t-1139 Build A review of record DELIVERED (regolith-empirical → regolith-physics)
Date: 2026-10-05 ~05:30 ET. Full review: `from-empirical/REVIEW-t1139-build-a-2026-10-05.md`.
Branch `review/t1139-build-a` @ a11d8f6ffbd6b6c28eaa0849dc86a6b2f4e43e84 (base e702b2551).

**Verdict: LAND-WITH-FOLLOWUPS at a11d8f6ff (gated).** Gate G1: the owner sanctions a ≤4.7e-15 ledger stoich shift on 16 rows and the pin loosening in 5a41afc68. Gate G2: Mac Studio full suite, goldens and the t622 test are green. If either gate is refused, REVISE. 0×P0, 4×P1, 6×P2, 6×P3. P1-2..4 block Build B (enable), not this dormant landing.

1. Pins/hashes: PARTIAL. Pressure pins byte-identical, live compiled species and all 231 pressures bit-identical. The pin file changed in 5a41afc68 (exact → abs 1e-12) and the original pins fail 16/122 on head. t622 refresh is evidence-only (proof re-run PASS, dataclass sha unchanged); base evidence was already stale; the grid sha is platform-dependent.
2. Interpolation OK (≤4 J/mol away from element transitions; transition kinks cancel at reaction level). One-P° is NOT enforced by the compiler (mixed P° compiles, ignored). atm→bar is correct at reaction level, species-only per record (Pankratz, ≤0.3 kJ/mol per O2).
3. Same-compilation references: YES (B(b) NG-1293 at 1400–1800 K). BUG: Si(cr) NG-1858 is dropped (inverted interval), so the reference silently falls back to Si(g): SiO/Si ΔfG off by 213–243 kJ/mol below 1690 K.
4. Derived stoich equals the 34 retired rows within 4.7e-15 (16 not bit-identical). Hydrate parsing unchanged; AlO1.5/CuO0.5 now parse.
5. Zero t1139_* ids in the live catalog, legacy_view and config bundle: YES. 45 channels / 259 gaps confirmed.
6. d-068: species_rail_differential.cea_delta_fG_kJ_mol is a true second copy and should call one shared kernel. pure_phase_janaf_score shares only the arithmetic; leave it for now.
d-062: Q1 YES (copies listed), Q2 YES (basis rule only in the generator; stoich in the legacy projection), Q3 NO, Q4 PARTIAL, Q5 YES (stoich pin loosened).
FOLD.md: absent from the bundle and the branch.
ASK regolith-main: full suite + goldens + the t622 test on a Mac Studio at a11d8f6ff.
