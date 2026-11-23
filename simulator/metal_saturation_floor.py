"""Lower bound on published melt fO2 from condensed-metal saturation.

``log10(fO2/bar)`` cannot sit below the most oxidizing condensed-metal
oxide couple that is actually in the melt. Below that line the metal
activity would exceed 1. Precipitation buffers fO2 there. The vapour
corollary is ``p_M(g) <= a_M * P_sat,M(T)`` with ``a_M = 1`` on the
buffer.

Fe is the Holzheid wüstite line in ``fe_metal_saturation_log10_fO2_bar``.
This module calls that function. It does not reimplement it.
``simulator.fe_redox`` stays a leaf: builtin providers import it, and a
saturation floor that also imported the vapour-pressure provider would
cycle. Si, Cr, Ti, Mn, and Ni use the Ellingham standard Gibbs energy
already in ``simulator.chemistry.ellingham_thermo``. No new thermo is
added here. Na, K, and Mg are gas on that owner at furnace temperature,
so they have no condensed floor. Al, Ca, and P are not in the named set:
P in particular has ``n_ox = 1/5``, so a trace of P2O5 still sits above
the Si line and would hide the shuttle case.

The 1e-30 bar value in ``MELT_DISSOCIATION_PO2_MIN_BAR`` is a float
guard inside ``physical_melt_dissociation_pO2_bar``. It is not a melt
state and it is not this floor.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_METAL_PHASE_CONDENSED,
    ellingham_delta_g_kj_per_mol_o2,
    ellingham_metal_phase_kind,
    ellingham_segment_for_temperature,
)
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.fe_redox import fe_metal_saturation_log10_fO2_bar
from simulator.physical_constants import GAS_CONSTANT, J_PER_KJ


# One list. Fe is not on it: the Fe couple is the wüstite function.
# ``scripts/mutation_melt_fo2_si_couple.py`` deletes the Si row. That
# row is the only Si couple; removing it is the mutation.
METAL_SATURATION_FLOOR_OXIDES: tuple[tuple[str, str], ...] = (
    ("Si", "SiO2"),
    ("Cr", "Cr2O3"),
    ("Ti", "TiO2"),
    ("Mn", "MnO"),
    ("Ni", "NiO"),
)

METAL_SATURATION_FLOOR_NOTICE_PREFIX = "melt_fO2_metal_saturation_floor:"
# Same tolerance as the Fe fixed point in ``_consistent_stoich_feo_activity``.
# Re-publishing a value already on the floor does not emit a second notice.
_FLOOR_BIND_TOLERANCE_DEX = 1.0e-9


def _ellingham_metal_saturation_log10_fO2_bar(
    metal: str,
    temperature_K: float,
    oxide_activity: float,
) -> float | None:
    """log10(fO2/bar) where a_M = 1 for one Ellingham couple.

    Premise. The certified reaction is written per mole of O2,
    ``n_M M + O2 = n_ox oxide``, with standard Gibbs energy
    ``ΔG°`` from the Ellingham owner. At metal saturation ``a_M = 1``.
    A non-positive oxide activity means that couple is absent. It is
    not replaced by 1 and it is not clamped to 1e-300. A metal whose
    active segment is a gas, or whose temperature is outside the
    certified segment range, has no condensed floor. The segment
    selector still returns an edge segment there; that extrapolation
    is not a saturation line.

    Algebra, per mole of O2, with ``a_M = 1``::

        log10(fO2/bar) = ΔG° / (R T ln 10) + n_ox log10(a_oxide)

    Si is the case the shuttle exposed. ``n_M = n_ox = 1`` on every
    Si segment, so this is the formation reaction
    ``Si + O2 = SiO2`` and the activity term is exactly
    ``log10(a_SiO2)``::

        log10(fO2_sat,Si) = ΔG°_f(SiO2) / (R T ln 10) + log10(a_SiO2)

    The owner's phase basis is not liquid at every furnace temperature.
    At 1423.15 K the segment is ``Si(s) + O2 -> SiO2(II)`` (1100–1685 K),
    not ``Si(l)``. At 1800 K the segment is ``Si(l) + O2 -> SiO2(l)``
    (1696–2500 K). The algebra is the same because ``n_ox = 1`` on both.
    The liquid segment is not extrapolated below the Si melting point.

    Unit check at 1423.15 K. The owner returns
    ``ΔG° = -656.956480 kJ/mol O2 = -656956.480 J/mol``.
    ``R T ln 10 = 8.31446261815324 * 1423.15 * ln(10) = 27245.861893 J/mol``.
    ``-656956.480 / 27245.861893 = -24.112156``, which is
    ``log10(fO2/bar)`` at ``a_SiO2 = 1``. At 1800 K,
    ``ΔG° = -589.452907 kJ/mol`` and
    ``R T ln 10 = 34460.563825 J/mol``, so
    ``-589452.907 / 34460.563825 = -17.105144``.

    Limiting case. ``a_SiO2 → 0`` sends the floor to -∞, which is the
    correct mass-action limit and is useless as a buffer, so ``a <= 0``
    drops the couple instead of clamping it. ``a_SiO2 = 1`` is the
    pure-oxide line.

    Sanity against the owner's own ΔG and against Holzheid IW
    (``feo_iw_log10_fO2_bar``, a_wüstite = 1). At 1423.15 K, IW is
    -12.275019. The pure Si line is -24.112156, 11.837 dex below IW.
    That is wider than the often-quoted 9–10 dex because this
    temperature is the solid Si / SiO2(II) segment, not the liquid
    line. The pure Fe wüstite floor at the same temperature is
    -12.885722 (IW - 0.611). At 1800 K, IW is -8.731302 and the liquid
    Si line is -17.105144, 8.374 dex below IW, which is the ~9-decade
    comparison. ``a_SiO2 = 0.41`` moves the 1423.15 K line by
    ``log10(0.41)`` to -24.499372.
    """

    activity = float(oxide_activity)
    if not math.isfinite(activity) or activity <= 0.0:
        return None
    if (
        ellingham_metal_phase_kind(metal, temperature_K)
        != ELLINGHAM_METAL_PHASE_CONDENSED
    ):
        return None
    segment = ellingham_segment_for_temperature(metal, temperature_K)
    low_K, high_K = segment.range_K
    if temperature_K < low_K or temperature_K > high_K:
        return None
    # n_ox is the oxide coefficient on this segment's per-mole-O2 reaction.
    # a_M = 1, so the metal term -n_M log10(a_M) is zero.
    delta_g_j_per_mol = (
        ellingham_delta_g_kj_per_mol_o2(metal, temperature_K) * J_PER_KJ
    )
    return (
        delta_g_j_per_mol / (GAS_CONSTANT * temperature_K * math.log(10.0))
        + float(segment.n_ox) * math.log10(activity)
    )


def melt_metal_saturation_floor_log10_fO2_bar(
    temperature_K: float,
    activities: Mapping[str, float] | None,
) -> tuple[float | None, str | None]:
    """Most oxidizing condensed-metal floor, and the metal that sets it.

    ``activities["FeO"]`` is the stoichiometric Kress/Holzheid a_FeO
    passed to ``fe_metal_saturation_log10_fO2_bar``. It is not the
    table-gamma FeO activity. The other keys are parent-formula
    activities (``thermodynamic_parent_activity``) for the oxides in
    ``METAL_SATURATION_FLOOR_OXIDES``. A missing or non-positive
    activity omits that couple.

    Returns ``(None, None)`` when no couple is present. An exact tie
    keeps the earlier metal. Fe is first, so a tie with Fe keeps the
    d-463 Fe floor.
    """

    temperature = float(temperature_K)
    if not math.isfinite(temperature) or temperature <= 0.0:
        return None, None
    present = activities or {}
    floor_log: float | None = None
    binding_metal: str | None = None
    fe_activity = present.get("FeO")
    if (
        fe_activity is not None
        and math.isfinite(float(fe_activity))
        and float(fe_activity) > 0.0
    ):
        floor_log = fe_metal_saturation_log10_fO2_bar(
            temperature,
            a_feo_stoich=float(fe_activity),
        )
        binding_metal = "Fe"
    for metal, oxide in METAL_SATURATION_FLOOR_OXIDES:
        raw_activity = present.get(oxide)
        if raw_activity is None:
            continue
        couple_log = _ellingham_metal_saturation_log10_fO2_bar(
            metal,
            temperature,
            float(raw_activity),
        )
        if couple_log is None:
            continue
        if floor_log is None or couple_log > floor_log:
            floor_log = couple_log
            binding_metal = metal
    return floor_log, binding_metal


def apply_melt_metal_saturation_floor(
    unfloored_log10_fO2_bar: float,
    temperature_K: float,
    activities: Mapping[str, float] | None,
) -> tuple[float, dict[str, object] | None]:
    """Raise a candidate that sits below the metal floor. Never refuse.

    The notice is emitted only when a finite candidate is strictly
    below the floor by more than ``_FLOOR_BIND_TOLERANCE_DEX``. A
    candidate already on the floor is returned unchanged and silent.
    No floor (no couple, or a non-finite temperature) leaves the
    candidate unchanged. A non-finite candidate is not given a floor;
    the caller's finite guard still owns that refusal.
    """

    unfloored = float(unfloored_log10_fO2_bar)
    floor_log, metal = melt_metal_saturation_floor_log10_fO2_bar(
        temperature_K,
        activities,
    )
    if (
        floor_log is None
        or metal is None
        or not math.isfinite(unfloored)
        or unfloored + _FLOOR_BIND_TOLERANCE_DEX >= floor_log
    ):
        return unfloored, None
    return floor_log, {
        "type": "typed_notice",
        "kind": f"{METAL_SATURATION_FLOOR_NOTICE_PREFIX}{metal}",
        "metal": metal,
        "temperature_K": float(temperature_K),
        "floor_log10_fO2_bar": floor_log,
        "unfloored_log10_fO2_bar": unfloored,
    }


def activities_for_metal_saturation_floor(
    account_mol: Mapping[str, float] | None,
    temperature_K: float,
    *,
    a_feo_stoich: float | None = None,
) -> dict[str, float]:
    """Formula activities for the named couples, plus stoichiometric a_FeO.

    Table-gamma FeO is not the saturation activity. Callers pass the
    Kress/Holzheid stoichiometric value or omit it. ``a_feo_stoich <= 0``
    omits the Fe couple rather than substituting 1.
    """

    activities: dict[str, float] = {}
    if (
        a_feo_stoich is not None
        and math.isfinite(float(a_feo_stoich))
        and float(a_feo_stoich) > 0.0
    ):
        activities["FeO"] = float(a_feo_stoich)
    if not account_mol:
        return activities
    for _metal, oxide in METAL_SATURATION_FLOOR_OXIDES:
        row = melt_oxide_activity(
            oxide,
            account_mol,
            temperature_K=temperature_K,
        )
        if row is None:
            continue
        activity = row.thermodynamic_parent_activity()
        if math.isfinite(activity) and activity > 0.0:
            activities[oxide] = float(activity)
    return activities
