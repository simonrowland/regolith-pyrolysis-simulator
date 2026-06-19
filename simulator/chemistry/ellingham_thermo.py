"""Canonical Ellingham thermodynamic table (single source of truth).

Pure data leaf -- imports nothing from ``simulator`` or ``engines`` so it can
be imported at module level by both the builtin vapor provider
(:mod:`engines.builtin.vapor_pressure`) and the legacy equilibrium fallback
(:class:`simulator.equilibrium.EquilibriumMixin`) WITHOUT closing an import
cycle (``simulator.core -> equilibrium -> vapor_pressure -> _common ->
capabilities -> simulator -> core``). Keep this module dependency-free.

Tuple per species: ``(dH_f kJ/mol_O2, dS_f kJ/(mol*K), n_M, n_ox)``
  n_M  = moles of metal per mol O2 in the decomposition reaction
  n_ox = moles of oxide per mol O2 in the decomposition reaction
"""

from __future__ import annotations

# Valid temperature window of the linear high-T refit (K).
ELLINGHAM_FIT_RANGE_K = (1100.0, 1700.0)

ELLINGHAM_THERMO: dict[str, tuple[float, float, float, float]] = {
    # V1c JANAF high-T refit over 1100-1700 K for Na/K/Fe/Cr/Mg/Ca/Al/Ti/Si.
    # Per-species trailing IDs are the JANAF grounding-corpus anchors;
    # dG(1600C) values are the cross-check used during refit.
    #
    # Mn updated 0.5.2 (2026-05-27) from the 298 K basis to a proper
    # HIGH-T linear refit anchored on Mn(l) above the solid->liquid
    # transition at 1517 K (NIST-JANAF Mn-008 + phase transition data).
    # Reaction is 2 Mn(l) + O2 -> 2 MnO(s) over the 1517-1700 K window
    # (Mn liquid, MnO solid; MnO melts at 2058 K above any furnace-
    # survivable T):
    #   dH(rxn, Mn liquid) = dH(rxn, Mn solid) - 2 * dH_fus(Mn)
    #                      = -770.44 - 2 * 12.05 = -794.54 kJ/mol O2
    #   dS(rxn, Mn liquid) = dS(rxn, Mn solid) - 2 * dS_fus(Mn)
    #                      = -149.75 - 2 * 7.95 = -165.65 J/K
    # Below 1517 K the table underestimates oxide stability by
    # ~5-15 kJ/mol O2 (Mn solid is reactant; the table assumes liquid).
    # Acceptable for the simulator's use case: Mn high-T vapor pressure
    # governs evaporation; the recipe T window where Mn matters
    # (1500-1800 K) is in the liquid-Mn regime where the table is
    # accurate. Mn is a minor byproduct (~0.2 wt% MnO in lunar mare) so
    # the sub-1517 K residual is well below the V1c approximation band.
    'Na': (-1135.130, -0.537417, 4, 2),      # Na-012,  dG(1600C) ~ -128
    'K':  (-975.838, -0.520580, 4, 2),       # K-012,   dG(1600C) ~ -1
    'Fe': (-538.946, -0.125272, 2, 2),       # Fe-018,  dG(1600C) ~ -304
    'Mn': (-794.540, -0.165650, 2, 2),       # Mn-008 high-T (Mn(l)+O2->MnO(s),
                                             # 1517-1700 K basis); dG(1600C) ~ -484
    'Cr': (-748.076, -0.168676, 4/3, 2/3),   # Cr-014,  dG(1600C) ~ -432
    'Mg': (-1342.444, -0.336009, 2, 2),      # Mg-008,  dG(1600C) ~ -713
    'Ca': (-1285.155, -0.222295, 2, 2),      # Ca-027,  dG(1600C) ~ -869
    'Al': (-1126.073, -0.218805, 4/3, 2/3),  # Al-096,  dG(1600C) ~ -716
    'Ti': (-939.632, -0.177149, 1, 1),       # O-043,   dG(1600C) ~ -608
    'Si': (-910.940, -0.182400, 1, 1),       # Si+O2->SiO2; dG(1600C) ~ -569
}
