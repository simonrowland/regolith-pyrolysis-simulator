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

import math
from dataclasses import dataclass
from typing import Any

# Default valid temperature window of the legacy linear high-T refit (K).
# CF-2-lite keeps refractory species on this band; mbar-regime species use the
# per-species segments below.
ELLINGHAM_FIT_RANGE_K = (1100.0, 1700.0)
ELLINGHAM_MBAR_FIT_RANGE_K = (1100.0, 2200.0)
ELLINGHAM_AUTHORITY_LIMIT_FLAG = "authority_limited_by_ellingham_fit_range"
ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG = (
    "authority_limited_by_reconstructed_ellingham_segment"
)
ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS = "reconstructed_limited"
ELLINGHAM_METAL_PHASE_GAS = "gas"
ELLINGHAM_METAL_PHASE_CONDENSED = "condensed"

# Mg rail boundary, distinct from the 1366 K JANAF coefficient splice below.
# NIST Chemistry WebBook SRD 69 (Mg, CAS 7439-95-4; Honig and Kramer 1969)
# reports T_boiling=1363 K with TRC uncertainty 1.5 K; 1090 C converts to
# 1363.15 K. At and above that boundary Mg(g) is the physical standard state,
# so use the gas-basis fit without retuning its coefficients or consulting the
# condensed-phase Antoine rail.
MG_NORMAL_BOILING_POINT_K = 1363.15


@dataclass(frozen=True)
class EllinghamFitSegment:
    dH_f_kJ_per_mol_O2: float
    dS_f_kJ_per_mol_K_per_mol_O2: float
    n_M: float
    n_ox: float
    range_K: tuple[float, float]
    phase_basis: str
    janaf_anchor: str
    dg_1600C_kJ_per_mol_O2: float | None = None
    dg_1727C_kJ_per_mol_O2: float | None = None
    dg_1800C_kJ_per_mol_O2: float | None = None
    dg_1900C_kJ_per_mol_O2: float | None = None
    authority_status: str = "authoritative"

    def delta_g_kJ_per_mol_O2(self, temperature_K: float) -> float:
        return (
            self.dH_f_kJ_per_mol_O2
            - float(temperature_K) * self.dS_f_kJ_per_mol_K_per_mol_O2
        )

ELLINGHAM_THERMO: dict[str, tuple[float, float, float, float]] = {
    # Legacy representative high-T coefficients kept for keying/stoichiometry
    # compatibility. Authoritative dG(T) now comes from ELLINGHAM_FIT_SEGMENTS.
    # V1c JANAF high-T refit over 1100-1700 K for Na/K/Fe/Cr/Mg/Ca/Al/Ti/Si.
    # Per-species trailing IDs are the JANAF grounding-corpus anchors;
    # dG(1600C) values are the cross-check used during refit.
    #
    # The Mn tuple is the legacy representative Mn(l)-basis row retained for
    # keying and stoichiometry compatibility only. Authoritative Mn dG(T)
    # queries route through ELLINGHAM_FIT_SEGMENTS: Pankratz primary-refit
    # beta/gamma/delta solid-Mn rows cover 1100-1519 K, followed by the
    # explicitly reconstructed-limited Mn(l) sidecar. No runtime Mn dG(T)
    # consumer reads this flat row as thermodynamic authority.
    'Na': (-1135.130, -0.537417, 4, 2),      # Na-012,  dG(1600C) ~ -128
    'K':  (-975.838, -0.520580, 4, 2),       # K-012,   dG(1600C) ~ -1
    'Fe': (-538.946, -0.125272, 2, 2),       # Fe-018,  dG(1600C) ~ -304
    'Mn': (-794.540, -0.165650, 2, 2),       # Legacy Mn(l) compatibility row;
                                             # dG authority is segmented below.
    'Cr': (-748.076, -0.168676, 4/3, 2/3),   # Cr-014,  dG(1600C) ~ -432
    'Mg': (-1342.444, -0.336009, 2, 2),      # Mg-008,  dG(1600C) ~ -713
    'Ca': (-1285.155, -0.222295, 2, 2),      # Ca-027,  dG(1600C) ~ -869
    'Al': (-1126.073, -0.218805, 4/3, 2/3),  # Al-096,  dG(1600C) ~ -716
    'Ti': (-939.632, -0.177149, 1, 1),       # O-043,   dG(1600C) ~ -608
    'Si': (-910.940, -0.182400, 1, 1),       # Si+O2->SiO2; dG(1600C) ~ -569
}


_LEGACY_SEGMENTS: dict[str, tuple[EllinghamFitSegment, ...]] = {
    species: (
        EllinghamFitSegment(
            dH_f,
            dS_f,
            n_M,
            n_ox,
            ELLINGHAM_FIT_RANGE_K,
            "legacy 1100-1700 K condensed JANAF basis",
            "Chase 1998 NIST-JANAF Thermochemical Tables, 4th ed.",
        ),
    )
    for species, (dH_f, dS_f, n_M, n_ox) in ELLINGHAM_THERMO.items()
}


# Primary-refit (2026-07-09): NIST-JANAF 4th multiphase rows from
# _ellingham_source.json, in kJ/mol O2 for n_M M + O2 -> n_ox oxide.
# Segments split at every in-band metal/oxide phase breakpoint. Longer stable
# intervals are sub-split only where needed for the existing linear segment
# schema to reproduce every source-grid row spanned by the segment to <0.5 kJ.
# Singleton/no-row breakpoint intervals use the JANAF H/G row tangent from the
# nearest 100 K row; those intervals have no source-grid row to residual-check.
# Metal-boil no-row intervals are constrained to the shared endpoint value at
# T_b so the condensed-metal and gas-metal standard states meet continuously;
# only dG/dT kinks at the phase transition. Mg is the documented exception:
# its source-fit join remains at 1366 K while runtime phase ownership switches
# at the NIST-backed 1363.15 K physical boundary above.
ELLINGHAM_FIT_SEGMENTS: dict[str, tuple[EllinghamFitSegment, ...]] = {
    **_LEGACY_SEGMENTS,
    # Na premise: Chase 1998 JANAF Na-014 rows 1100-2600 K, split at Na
    # boil 1156.1 K and Na2O beta->alpha 1243.15 K, alpha->liquid 1405.2 K.
    # Fit: dH/dS are least-squares for each row span below; max residual
    # 0.442 kJ/mol O2. Unit check: dS is kJ/mol/K/mol O2. Sanity:
    # dG(2200 K) = -7.33 vs grid -7.772 kJ/mol O2. Boil kink: above Tb
    # the metal standard state is gas; gaseous metal is a higher-energy
    # reactant, so oxide-formation dG becomes less negative above T_b and
    # the slope changes instead of false-flattening the Na line. The
    # 1100-1156.1 K segment is constrained to the post-boil shared endpoint
    # dG(1156.1 K) = -524.382071 kJ/mol O2.
    "Na": (
        EllinghamFitSegment(
            -731.077284147,
            -0.178786621770,
            4,
            2,
            (1100.0, 1156.1),
            "4 Na(l) + O2 -> 2 Na2O(beta); 1100 K row constrained to Na boil endpoint",
            "Chase 1998 NIST-JANAF Na-014; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1207.826,
            -0.591163333,
            4,
            2,
            (1156.1, 1243.15),
            "4 Na(g) + O2 -> 2 Na2O(beta); source-row tangent at 1200 K",
            "Chase 1998 NIST-JANAF Na-014; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1172.356,
            -0.562800,
            4,
            2,
            (1243.15, 1405.2),
            "4 Na(g) + O2 -> 2 Na2O(alpha); rows 1300-1400 K",
            "Chase 1998 NIST-JANAF Na-014; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1051.1212,
            -0.477468,
            4,
            2,
            (1405.2, 1800.0),
            "4 Na(g) + O2 -> 2 Na2O(l); rows 1500-1800 K",
            "Chase 1998 NIST-JANAF Na-014; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1020.2496,
            -0.460416,
            4,
            2,
            (1800.0, 2200.0),
            "4 Na(g) + O2 -> 2 Na2O(l); rows 1800-2200 K",
            "Chase 1998 NIST-JANAF Na-014; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -985.0724,
            -0.444394,
            4,
            2,
            (2200.0, 2600.0),
            "4 Na(g) + O2 -> 2 Na2O(l); rows 2200-2600 K",
            "Chase 1998 NIST-JANAF Na-014; confidence: primary-refit",
        ),
    ),
    # K premise: this worktree already has K, so keep it non-breaking but
    # fail-closed at the JANAF K-012 solid-only table limit of 2000 K.
    # Rows 1100-2000 K are K(g)+K2O(cr); K boil is below the grid at 1032 K
    # and no JANAF liquid K2O segment exists. Fit max residual 0.499 kJ/mol
    # O2; dS unit is kJ/mol/K/mol O2. Sanity: dG(2000 K) = +50.294 kJ/mol
    # O2 exactly. Boil kink premise is already active for all in-band rows:
    # gaseous K is the higher-energy reactant, so the post-boil line must not
    # reuse the lower-T liquid-metal slope.
    "K": (
        EllinghamFitSegment(
            -1000.445333,
            -0.541100,
            4,
            2,
            (1100.0, 1300.0),
            "4 K(g) + O2 -> 2 K2O(cr); rows 1100-1300 K",
            "Chase 1998 NIST-JANAF K-012; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -967.231,
            -0.515800,
            4,
            2,
            (1300.0, 1600.0),
            "4 K(g) + O2 -> 2 K2O(cr); rows 1300-1600 K",
            "Chase 1998 NIST-JANAF K-012; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -920.512,
            -0.486590,
            4,
            2,
            (1600.0, 1900.0),
            "4 K(g) + O2 -> 2 K2O(cr); rows 1600-1900 K",
            "Chase 1998 NIST-JANAF K-012; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -884.946,
            -0.467620,
            4,
            2,
            (1900.0, 2000.0),
            "4 K(g) + O2 -> 2 K2O(cr); rows 1900-2000 K",
            "Chase 1998 NIST-JANAF K-012; confidence: primary-refit",
        ),
    ),
    # Fe premise: Chase 1998 JANAF Fe-020 rows 1100-2600 K, split at
    # Fe alpha->gamma 1184 K, FeO melt 1650 K, Fe gamma->delta 1665 K,
    # and Fe melt 1809 K. Fit max residual 0.131 kJ/mol O2; dS unit is
    # kJ/mol/K/mol O2. Sanity: dG(2000 K) = -296.893 vs grid -296.904.
    "Fe": (
        EllinghamFitSegment(
            -540.770,
            -0.126754545,
            2,
            2,
            (1100.0, 1184.0),
            "2 Fe(alpha) + O2 -> 2 FeO(s); source-row tangent at 1100 K",
            "Chase 1998 NIST-JANAF Fe-020; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -538.9524,
            -0.125376,
            2,
            2,
            (1184.0, 1650.0),
            "2 Fe(gamma) + O2 -> 2 FeO(s); rows 1200-1600 K",
            "Chase 1998 NIST-JANAF Fe-020; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -487.080,
            -0.093781176,
            2,
            2,
            (1650.0, 1665.0),
            "2 Fe(gamma) + O2 -> 2 FeO(l); nearest-row tangent at 1700 K",
            "Chase 1998 NIST-JANAF Fe-020; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -486.296,
            -0.093320,
            2,
            2,
            (1665.0, 1809.0),
            "2 Fe(delta) + O2 -> 2 FeO(l); rows 1700-1800 K",
            "Chase 1998 NIST-JANAF Fe-020; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -510.298143,
            -0.106702619,
            2,
            2,
            (1809.0, 2600.0),
            "2 Fe(l) + O2 -> 2 FeO(l); rows 1900-2600 K",
            "Chase 1998 NIST-JANAF Fe-020; confidence: primary-refit",
        ),
    ),
    # Mn premise: Pankratz USBM B672 MnO(c) rows 1100-1500 K, then the
    # project Mn(l) reconstruction dG = -794.540 + 0.165650*T from 1600 K
    # upward. Fits are kJ/mol O2 with dS in kJ/mol/K/mol O2. Primary-row
    # max residual is 0.014 kJ/mol O2 after continuous sub-splits at the
    # Mn beta->gamma (1360 K) and gamma->delta (1410 K) allotrope
    # transitions; reconstructed tail is exact by construction. Sanity:
    # dG(2000 K) = -463.240 kJ/mol O2. Reconstructed segments are
    # deliberately tagged non-authoritative.
    "Mn": (
        EllinghamFitSegment(
            -772.183333333,
            -0.148405,
            2,
            2,
            (1100.0, 1360.0),
            "2 Mn(beta,s) + O2 -> 2 MnO(s); Pankratz rows 1100-1300 K",
            "Pankratz USBM B672 MnO(c); confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -779.980666667,
            -0.154138333,
            2,
            2,
            (1360.0, 1410.0),
            "2 Mn(gamma,s) + O2 -> 2 MnO(s); Pankratz row 1400 K",
            "Pankratz USBM B672 MnO(c); confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -781.816277778,
            -0.155440185,
            2,
            2,
            (1410.0, 1519.0),
            "2 Mn(delta,s) + O2 -> 2 MnO(s); Pankratz row 1500 K",
            "Pankratz USBM B672 MnO(c); confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -794.540,
            -0.165650,
            2,
            2,
            (1519.0, 2058.0),
            "2 Mn(l) + O2 -> 2 MnO(s); project rows 1600-2000 K",
            "Project Mn(l) reconstruction; confidence: reconstructed",
            authority_status=ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS,
        ),
        EllinghamFitSegment(
            -794.540,
            -0.165650,
            2,
            2,
            (2058.0, 2600.0),
            "2 Mn(l) + O2 -> 2 MnO(lit. liquid range); project rows 2100-2600 K",
            "Project Mn(l) reconstruction; confidence: reconstructed",
            authority_status=ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS,
        ),
    ),
    # Cr premise: Chase 1998 JANAF Cr-016 rows 1100-2600 K, split at Cr
    # melt 2130 K; Cr2O3 melt is 2603 K, just above this grid. Fit max
    # residual 0.314 kJ/mol O2. Unit check: dS is kJ/mol/K/mol O2.
    # Sanity: dG(2600 K) = -301.079 vs grid -301.093.
    "Cr": (
        EllinghamFitSegment(
            -748.8986,
            -0.169276909,
            4 / 3,
            2 / 3,
            (1100.0, 2130.0),
            "4/3 Cr(s) + O2 -> 2/3 Cr2O3(s); rows 1100-2100 K",
            "Chase 1998 NIST-JANAF Cr-016; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -781.3796,
            -0.184731,
            4 / 3,
            2 / 3,
            (2130.0, 2600.0),
            "4/3 Cr(l) + O2 -> 2/3 Cr2O3(s); rows 2200-2600 K",
            "Chase 1998 NIST-JANAF Cr-016; confidence: primary-refit",
        ),
    ),
    # Mg premise: Chase 1998 JANAF Mg-010 rows 1100-2600 K, with the retained
    # condensed/gas coefficient join at 1366 K; MgO melt is outside the grid
    # at 3105 K. Runtime selects the gas segment from physical T_boiling
    # 1363.15 K, back-extrapolating it 2.85 K without retuning coefficients;
    # the two fits therefore do not join exactly at the runtime boundary.
    # Fit max residual
    # 0.457 kJ/mol O2; dS unit is kJ/mol/K/mol O2. Sanity: dG(2600 K)
    # = -398.384 vs grid -398.744. Boil kink: Mg(g) is a higher-energy
    # reactant than Mg(l), so post-boil oxide-formation dG is less negative
    # and must use the steeper gaseous-metal JANAF slope. The 1300-1366 K
    # rowless connector is constrained to the post-boil shared endpoint
    # dG(1366 K) = -900.695453 kJ/mol O2.
    "Mg": (
        EllinghamFitSegment(
            -1216.985333,
            -0.231080,
            2,
            2,
            (1100.0, 1300.0),
            "2 Mg(l) + O2 -> 2 MgO(s); rows 1100-1300 K",
            "Chase 1998 NIST-JANAF Mg-010; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1229.485036627,
            -0.240695156636,
            2,
            2,
            (1300.0, 1366.0),
            "2 Mg(l) + O2 -> 2 MgO(s); 1300 K row constrained to Mg boil endpoint",
            "Chase 1998 NIST-JANAF Mg-010; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1463.2445,
            -0.411822143,
            2,
            2,
            (1366.0, 2000.0),
            "2 Mg(g) + O2 -> 2 MgO(s); rows 1400-2000 K",
            "Chase 1998 NIST-JANAF Mg-010; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1443.971643,
            -0.402149286,
            2,
            2,
            (2000.0, 2600.0),
            "2 Mg(g) + O2 -> 2 MgO(s); rows 2000-2600 K",
            "Chase 1998 NIST-JANAF Mg-010; confidence: primary-refit",
        ),
    ),
    # Ca premise: Chase 1998 JANAF Ca-029 rows 1100-2600 K, split at Ca
    # melt 1115 K and boil 1757 K; CaO melt is outside the grid. Fit max
    # residual 0.456 kJ/mol O2; dS unit is kJ/mol/K/mol O2. Sanity:
    # dG(2600 K) = -575.694 vs grid -575.720. Boil kink: Ca(g) raises the
    # reactant standard-state G above T_b, making formation dG less negative
    # than a false liquid-Ca extrapolation. The 1700-1757 K rowless connector
    # is constrained to the post-boil shared endpoint dG(1757 K) =
    # -896.761077 kJ/mol O2.
    "Ca": (
        EllinghamFitSegment(
            -1269.498,
            -0.208094545,
            2,
            2,
            (1100.0, 1115.0),
            "2 Ca(s) + O2 -> 2 CaO(s); source-row tangent at 1100 K",
            "Chase 1998 NIST-JANAF Ca-029; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1285.2748,
            -0.222384,
            2,
            2,
            (1115.0, 1700.0),
            "2 Ca(l) + O2 -> 2 CaO(s); rows 1200-1700 K",
            "Chase 1998 NIST-JANAF Ca-029; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1219.214447718,
            -0.183524969246,
            2,
            2,
            (1700.0, 1757.0),
            "2 Ca(l) + O2 -> 2 CaO(s); 1700 K row constrained to Ca boil endpoint",
            "Chase 1998 NIST-JANAF Ca-029; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1569.619286,
            -0.382958571,
            2,
            2,
            (1757.0, 2400.0),
            "2 Ca(g) + O2 -> 2 CaO(s); rows 1800-2400 K",
            "Chase 1998 NIST-JANAF Ca-029; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1553.840333,
            -0.376210,
            2,
            2,
            (2400.0, 2600.0),
            "2 Ca(g) + O2 -> 2 CaO(s); rows 2400-2600 K",
            "Chase 1998 NIST-JANAF Ca-029; confidence: primary-refit",
        ),
    ),
    # Al premise: Chase 1998 JANAF Al-101 rows 1100-2600 K, split at
    # Al2O3 alpha->liquid 2327 K; Al melt is below the grid. Fit max residual
    # 0.459 kJ/mol O2. Unit check: dS is kJ/mol/K/mol O2. Sanity:
    # dG(2000 K) = -689.214 vs grid -689.397.
    "Al": (
        EllinghamFitSegment(
            -1124.438873,
            -0.217612364,
            4 / 3,
            2 / 3,
            (1100.0, 2100.0),
            "4/3 Al(l) + O2 -> 2/3 Al2O3(alpha,s); rows 1100-2100 K",
            "Chase 1998 NIST-JANAF Al-101; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1117.678667,
            -0.214185,
            4 / 3,
            2 / 3,
            (2100.0, 2327.0),
            "4/3 Al(l) + O2 -> 2/3 Al2O3(alpha,s); rows 2100-2300 K",
            "Chase 1998 NIST-JANAF Al-101; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -1034.045667,
            -0.178330,
            4 / 3,
            2 / 3,
            (2327.0, 2600.0),
            "4/3 Al(l) + O2 -> 2/3 Al2O3(l); rows 2400-2600 K",
            "Chase 1998 NIST-JANAF Al-101; confidence: primary-refit",
        ),
    ),
    # Ti premise: Chase 1998 JANAF O-045 rows 1100-2600 K, split at
    # Ti alpha->beta 1166 K, Ti melt 1941 K, and TiO2 melt 2130 K. Fit max
    # residual 0.206 kJ/mol O2; dS unit is kJ/mol/K/mol O2. Sanity:
    # dG(2100 K) = -567.263 kJ/mol O2 exactly.
    "Ti": (
        EllinghamFitSegment(
            -938.339,
            -0.175941818,
            1,
            1,
            (1100.0, 1166.0),
            "Ti(alpha) + O2 -> TiO2(rutile,s); source-row tangent at 1100 K",
            "Chase 1998 NIST-JANAF O-045; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -938.77244,
            -0.176554881,
            1,
            1,
            (1166.0, 1941.0),
            "Ti(beta) + O2 -> TiO2(rutile,s); rows 1200-1900 K",
            "Chase 1998 NIST-JANAF O-045; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -950.891,
            -0.182680,
            1,
            1,
            (1941.0, 2130.0),
            "Ti(l) + O2 -> TiO2(rutile,s); rows 2000-2100 K",
            "Chase 1998 NIST-JANAF O-045; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -880.544,
            -0.149735,
            1,
            1,
            (2130.0, 2600.0),
            "Ti(l) + O2 -> TiO2(l); rows 2200-2600 K",
            "Chase 1998 NIST-JANAF O-045; confidence: primary-refit",
        ),
    ),
    # Si premise: Chase 1998 JANAF O-039 rows 1100-2600 K. Split at Si
    # melt 1685 K and the JANAF SiO2 II->liquid transition 1696 K (not the
    # classical cristobalite melt often quoted near 1996 K). Fit max residual
    # 0.478 kJ/mol O2; dS unit is kJ/mol/K/mol O2. Sanity:
    # dG(2200 K) = -512.402 vs grid -512.672.
    "Si": (
        EllinghamFitSegment(
            -902.442943,
            -0.172495143,
            1,
            1,
            (1100.0, 1685.0),
            "Si(s) + O2 -> SiO2(II); rows 1100-1600 K",
            "Chase 1998 NIST-JANAF O-039; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -941.610,
            -0.195618235,
            1,
            1,
            (1685.0, 1696.0),
            "Si(l) + O2 -> SiO2(II); nearest-row tangent at 1700 K",
            "Chase 1998 NIST-JANAF O-039; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -933.753006,
            -0.191277833,
            1,
            1,
            (1696.0, 2500.0),
            "Si(l) + O2 -> SiO2(l); rows 1700-2500 K",
            "Chase 1998 NIST-JANAF O-039; confidence: primary-refit",
        ),
        EllinghamFitSegment(
            -924.129,
            -0.187250,
            1,
            1,
            (2500.0, 2600.0),
            "Si(l) + O2 -> SiO2(l); rows 2500-2600 K",
            "Chase 1998 NIST-JANAF O-039; confidence: primary-refit",
        ),
    ),
}


def ellingham_fit_segments(species: str) -> tuple[EllinghamFitSegment, ...]:
    return ELLINGHAM_FIT_SEGMENTS[str(species)]


def ellingham_fit_range_K(species: str) -> tuple[float, float]:
    segments = ellingham_fit_segments(species)
    return (
        min(segment.range_K[0] for segment in segments),
        max(segment.range_K[1] for segment in segments),
    )


def _finite_temperature_K(temperature_K: float) -> float:
    T_K = float(temperature_K)
    if not math.isfinite(T_K):
        raise ValueError("Ellingham temperature_K must be finite")
    return T_K


def ellingham_segment_for_temperature(
    species: str,
    temperature_K: float,
) -> EllinghamFitSegment:
    segments = ellingham_fit_segments(species)
    T_K = _finite_temperature_K(temperature_K)
    selectable_segments = segments
    if str(species) == "Mg" and T_K >= MG_NORMAL_BOILING_POINT_K:
        selectable_segments = tuple(
            segment for segment in segments if "Mg(g)" in segment.phase_basis
        )
    for index, segment in enumerate(selectable_segments):
        low, high = segment.range_K
        if low <= T_K and (
            T_K < high or index == len(selectable_segments) - 1
        ):
            return segment
    if T_K < selectable_segments[0].range_K[0]:
        return selectable_segments[0]
    return selectable_segments[-1]


def ellingham_metal_phase_kind(species: str, temperature_K: float) -> str:
    """Return the metal standard-state rail carried by the active row."""

    segment = ellingham_segment_for_temperature(species, temperature_K)
    phase_basis = str(segment.phase_basis)
    if f"{species}(g)" in phase_basis:
        return ELLINGHAM_METAL_PHASE_GAS
    return ELLINGHAM_METAL_PHASE_CONDENSED


def ellingham_delta_g_kj_per_mol_o2(
    species: str,
    temperature_K: float,
) -> float:
    T_K = _finite_temperature_K(temperature_K)
    segment = ellingham_segment_for_temperature(species, T_K)
    return segment.delta_g_kJ_per_mol_O2(T_K)


def ellingham_stoichiometry(species: str) -> tuple[float, float]:
    segment = ellingham_segment_for_temperature(
        species,
        ellingham_fit_range_K(species)[0],
    )
    return segment.n_M, segment.n_ox


def _ellingham_segment_is_reconstructed(segment: EllinghamFitSegment) -> bool:
    return segment.authority_status == ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS


def _ellingham_reconstructed_authority_limit(
    species: str,
    temperature_K: float,
    segment: EllinghamFitSegment,
    *,
    consumer: str,
) -> dict[str, Any]:
    # Premise: a fit segment may be inside its numeric K range while still
    # carrying reconstructed, non-authoritative provenance.
    # Algebra: dG(T) = dH - T*dS remains computable; only authority metadata
    # changes. Unit check: temperature_K and range_K are kelvin, while the
    # segment coefficients remain kJ/mol O2. Sanity: Mn at 1873.15 K selects the
    # project reconstruction and must not collapse to "authoritative".
    return {
        "temperature_K": temperature_K,
        "segment_range_K": segment.range_K,
        "species": species,
        "consumer": consumer,
        "authority_status": ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS,
        "authority_flag": ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG,
        ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG: True,
        "authority_reason": "reconstructed_segment",
        "phase_basis": segment.phase_basis,
        "source_basis": segment.janaf_anchor,
    }


def ellingham_fit_extrapolation(
    temperature_K: float,
    *,
    species: str,
    consumer: str,
) -> dict[str, Any] | None:
    T_K = _finite_temperature_K(temperature_K)
    segment = ellingham_segment_for_temperature(species, T_K)
    valid_low, valid_high = segment.range_K
    if valid_low <= T_K <= valid_high:
        return None
    valid_low, valid_high = ellingham_fit_range_K(species)
    return {
        "temperature_K": T_K,
        "fit_range_K": (valid_low, valid_high),
        "species": species,
        "consumer": consumer,
        "authority_status": "extrapolation_limited",
        "authority_flag": ELLINGHAM_AUTHORITY_LIMIT_FLAG,
    }


def ellingham_authority_limit(
    temperature_K: float,
    *,
    species: str,
    consumer: str,
) -> dict[str, Any] | None:
    T_K = _finite_temperature_K(temperature_K)
    extrapolation = ellingham_fit_extrapolation(
        T_K,
        species=species,
        consumer=consumer,
    )
    if extrapolation is not None:
        return extrapolation
    segment = ellingham_segment_for_temperature(species, T_K)
    if _ellingham_segment_is_reconstructed(segment):
        return _ellingham_reconstructed_authority_limit(
            species,
            T_K,
            segment,
            consumer=consumer,
        )
    return None


def ellingham_authority_diagnostic(
    extrapolations: dict[str, dict[str, Any]],
    *,
    consumer: str,
) -> dict[str, Any]:
    limited = bool(extrapolations)
    reconstructed_limited = any(
        isinstance(data, dict)
        and data.get(ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG) is True
        for data in extrapolations.values()
    )
    statuses = {
        str(data.get("authority_status"))
        for data in extrapolations.values()
        if isinstance(data, dict) and data.get("authority_status") is not None
    }
    status = "authoritative"
    if limited:
        status = (
            "authority_limited"
            if statuses == {ELLINGHAM_RECONSTRUCTED_AUTHORITY_STATUS}
            else "extrapolation_limited"
        )
    return {
        "consumer": consumer,
        "status": status,
        ELLINGHAM_AUTHORITY_LIMIT_FLAG: any(
            data.get("authority_status") == "extrapolation_limited"
            for data in extrapolations.values()
            if isinstance(data, dict)
        ),
        ELLINGHAM_RECONSTRUCTED_AUTHORITY_FLAG: reconstructed_limited,
        "authority_limits": {
            str(species): dict(data)
            for species, data in extrapolations.items()
        },
        "extrapolated_beyond_fit_range_K": {
            str(species): dict(data)
            for species, data in extrapolations.items()
            if data.get("authority_status") == "extrapolation_limited"
        },
    }
