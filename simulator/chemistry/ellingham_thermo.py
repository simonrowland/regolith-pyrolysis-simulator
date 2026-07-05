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

from dataclasses import dataclass
from typing import Any

# Default valid temperature window of the legacy linear high-T refit (K).
# CF-2-lite keeps refractory species on this band; mbar-regime species use the
# per-species segments below.
ELLINGHAM_FIT_RANGE_K = (1100.0, 1700.0)
ELLINGHAM_MBAR_FIT_RANGE_K = (1100.0, 2200.0)
ELLINGHAM_AUTHORITY_LIMIT_FLAG = "authority_limited_by_ellingham_fit_range"


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

    def delta_g_kJ_per_mol_O2(self, temperature_K: float) -> float:
        return (
            self.dH_f_kJ_per_mol_O2
            - float(temperature_K) * self.dS_f_kJ_per_mol_K_per_mol_O2
        )

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


# CF-2-lite (2026-07-04): extend only mbar-regime JANAF authority to 2200 K.
# Values stay in kJ/mol O2. Anchors are NIST-JANAF/Chase 1998 table IDs
# already used by the V1c grounding corpus. Fe and Si split at the metal
# melting transitions so the high segment uses the liquid-metal standard state.
# Cr uses the same construction: Cr fusion at 2180 K and dH_fus=21.0 kJ/mol
# from CRC/Lange tabulations carried in REF-032-style elemental fusion data.
# Mn keeps the existing Mn(l) basis and extends its upper fit limit to 2200 K;
# it is deliberately not marked authoritative below the 1517 K Mn melting point.
ELLINGHAM_FIT_SEGMENTS: dict[str, tuple[EllinghamFitSegment, ...]] = {
    **_LEGACY_SEGMENTS,
    "Na": (
        EllinghamFitSegment(
            -1135.130,
            -0.537417,
            4,
            2,
            ELLINGHAM_MBAR_FIT_RANGE_K,
            "4 Na(l) + O2 -> 2 Na2O(condensed JANAF phase)",
            "Chase 1998 NIST-JANAF Na-012",
            -128.37,
            -60.30,
            -20.98,
            32.76,
        ),
    ),
    "K": (
        EllinghamFitSegment(
            -975.838,
            -0.520580,
            4,
            2,
            ELLINGHAM_MBAR_FIT_RANGE_K,
            "4 K(l) + O2 -> 2 K2O(condensed JANAF phase)",
            "Chase 1998 NIST-JANAF K-012",
            -0.67,
            65.32,
            103.40,
            155.46,
        ),
    ),
    "Fe": (
        EllinghamFitSegment(
            -538.946,
            -0.125272,
            2,
            2,
            (1100.0, 1811.0),
            "2 Fe(s) + O2 -> 2 FeO(condensed JANAF phase)",
            "Chase 1998 NIST-JANAF Fe-018",
            -304.35,
            None,
        ),
        EllinghamFitSegment(
            -566.566,
            -0.140512,
            2,
            2,
            (1811.0, 2200.0),
            "2 Fe(l) + O2 -> 2 FeO(condensed JANAF phase)",
            "Chase 1998 NIST-JANAF Fe-018 + Fe fusion at 1811 K",
            -303.36,
            -285.54,
            -275.26,
            -261.21,
        ),
    ),
    "Mn": (
        EllinghamFitSegment(
            -794.540,
            -0.165650,
            2,
            2,
            (1517.0, 2200.0),
            "2 Mn(l) + O2 -> 2 MnO(s)",
            "Chase 1998 NIST-JANAF Mn-008 + Mn fusion basis",
            -484.21,
            -463.24,
            -451.12,
            -434.56,
        ),
    ),
    "Cr": (
        EllinghamFitSegment(
            -748.076,
            -0.168676,
            4 / 3,
            2 / 3,
            (1100.0, 2180.0),
            "4/3 Cr(s) + O2 -> 2/3 Cr2O3(s)",
            "Chase 1998 NIST-JANAF Cr-014",
            -432.14,
            -410.72,
            -398.39,
            -381.52,
        ),
        EllinghamFitSegment(
            -776.076,
            -0.1815200366972477,
            4 / 3,
            2 / 3,
            (2180.0, 2200.0),
            "4/3 Cr(l) + O2 -> 2/3 Cr2O3(s)",
            "Chase 1998 NIST-JANAF Cr-014 + Cr fusion at 2180 K",
            -432.14,
            -410.72,
            -398.39,
            -381.52,
        ),
    ),
    "Si": (
        EllinghamFitSegment(
            -910.940,
            -0.182400,
            1,
            1,
            (1100.0, 1687.0),
            "Si(s) + O2 -> SiO2(condensed JANAF phase)",
            "Chase 1998 NIST-JANAF Si/SiO2 condensed tables",
            None,
            None,
        ),
        EllinghamFitSegment(
            -961.150,
            -0.212160,
            1,
            1,
            (1687.0, 2200.0),
            "Si(l) + O2 -> SiO2(condensed JANAF phase)",
            "Chase 1998 NIST-JANAF Si/SiO2 tables + Si fusion at 1687 K",
            -563.76,
            -536.83,
            -521.31,
            -500.09,
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


def ellingham_segment_for_temperature(
    species: str,
    temperature_K: float,
) -> EllinghamFitSegment:
    segments = ellingham_fit_segments(species)
    T_K = float(temperature_K)
    for segment in segments:
        low, high = segment.range_K
        if low <= T_K <= high:
            return segment
    if T_K < segments[0].range_K[0]:
        return segments[0]
    return segments[-1]


def ellingham_delta_g_kj_per_mol_o2(
    species: str,
    temperature_K: float,
) -> float:
    segment = ellingham_segment_for_temperature(species, temperature_K)
    return segment.delta_g_kJ_per_mol_O2(temperature_K)


def ellingham_stoichiometry(species: str) -> tuple[float, float]:
    segment = ellingham_segment_for_temperature(
        species,
        ellingham_fit_range_K(species)[0],
    )
    return segment.n_M, segment.n_ox


def ellingham_fit_extrapolation(
    temperature_K: float,
    *,
    species: str,
    consumer: str,
) -> dict[str, Any] | None:
    T_K = float(temperature_K)
    for segment in ellingham_fit_segments(species):
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


def ellingham_authority_diagnostic(
    extrapolations: dict[str, dict[str, Any]],
    *,
    consumer: str,
) -> dict[str, Any]:
    limited = bool(extrapolations)
    return {
        "consumer": consumer,
        "status": "extrapolation_limited" if limited else "authoritative",
        ELLINGHAM_AUTHORITY_LIMIT_FLAG: limited,
        "extrapolated_beyond_fit_range_K": {
            str(species): dict(data)
            for species, data in extrapolations.items()
        },
    }
