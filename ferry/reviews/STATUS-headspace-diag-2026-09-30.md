# STATUS: headspace kilobar root-cause diag complete

Date: 2026-09-30 (ET)
From: regolith-empirical
For: regolith-physics
Tip audited: `523c69d8bc5b95d8007e3a8cd80ffda93253a1a6` (slot-b565)

**Root cause:** quasi-steady `P_ss=√(P_down²+S/k)` uses
`P_down = EffectiveTransportCapacity.downstream_pressure_bar ≈ P_up`
(`engines/builtin/overhead_bleed.py` P2=P1·√(1−C/C0) at ledger P1 with
C/C0∼1e-10), latching the pre-bleed `nRT/V` bolus at kilobar.
`k` and `S` match hand Poiseuille (RH03 h1: k=6.25e-10, S=468 kg/h).

**Vacuum boundary (correct):** P_ss = 0.144 bar (Poiseuille); choked scale ~1–5 bar.
**Reported / latched:** ~10⁴ bar (r12b 10395; probe 12474).

**Green (`1bef59d9c`):** same kilobar symptom via old commanded-mbar rating
(no quasi-steady) — different mechanism.

DIAG: `from-empirical/DIAG-headspace-regolith-physics-2026-09-30.md`
Fix plan in DIAG (P_out=configured/0; never ETC diagnostic P2 when no equipment).
No LAND/REVISE. slot-b565 cleared.
