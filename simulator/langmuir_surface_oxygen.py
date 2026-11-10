"""Self-consistent surface pO2 for free Langmuir evaporation.

The vapor-pressure provider keeps the mass-action metal pressure at the
commanded melt fO2, including the b-491 floor inversion. Provider
equilibrium pressures stay on that law. This module replaces the flux
map only for the closure in d-376 amendment 1, and names the warning.

d-376 amendment 1. Under the d-072 reservoir a melt with redox capacity
controls its interface pO2. Evaporative oxygen changes that inventory
through the booked fluxes; it does not reset the surface pO2 on the
evaporation step. The congruent closure (effusion-weighted
``J_O2 = 1/2 J_M``) is the surface boundary only when the evaluated
pO2 is at or below the physical floor, or the melt's effective redox
capacity is negligible (``C_m ~ 0``). The physical floor is
``MELT_DISSOCIATION_PO2_MIN_BAR`` (1e-30 bar), the clamp that published
the unphysical 2.6e13 Pa Ca pressure. Every other in-band evaporation
keeps the reservoir / mass-action pressures.

Overhead. While that closure has O2 net-leaving, the surface pO2 is
not set below the overhead pO2. The vacuum root is raised to the
overhead partial, and the metal pressures follow the same power law
there.

A failed root drops only below-band carriers. An in-band failure keeps
the published pressures and names the warning.

Invariant. This function is the only writer of the flux map. The
closure runs only when the evaluated pO2 is at the physical floor or
the reservoir source capacity is at or below
``OXYGEN_RESERVOIR_NOOP_MOL`` (d-072 ``no_melt_redox_capacity``).
``equilibrium.liquid_fraction`` is not that test. Every species is
then one of three outcomes: the published pressure (closure closed,
transport-headspace channel, or no melt-surface law), the single
surface-pO2 law below, or a typed refusal. Nothing writes ``0.0`` in
place of a published pressure, and nothing drops a participating
carrier's reaction oxygen demand because a cap zeroed the local slope.

Algebra. Reaction stoichiometry and pressure sensitivity are separate.
The reaction exponent ``n`` is the mass-action slope off any activity
cap. A carrier published at ``P_pub`` (Pa) and reference ``p_ref``
(bar) is ``P(p) = P_pub*(p/p_ref)^n`` at surface ``p`` (bar) when it
is not capped. A condensed-metal rail with ``raw > 1`` is published at
``P_sat`` (the cap). Its local slope at that point is 0, but the
parent reaction is unchanged. The cap releases when
``raw*(p/p_ref)^n = 1``, i.e. ``p_uncap = p_ref*(1/raw)^(1/n)`` with
``n < 0``, so ``p_uncap > p_ref``. Below ``p_uncap``, ``P = P_sat``.
Above it, ``P = P_sat*raw*(p/p_ref)^n``. Sanity: ``raw = 100``,
``n = -1/2`` gives ``p_uncap/p_ref = 1e4``. The O2 coefficient of the
reaction is ``nu_O2 = -n``, so the parent supplies
``dO = nO_gas - 2*n`` oxygen atoms per vapor molecule whether or not
the cap is binding. The Hertz-Knudsen molar flux is
``J = alpha*P/sqrt(2*pi*M*R*T)``, and the surface root is
``sum_i (nO_i - dO_i)*J_i = 0``. The factor ``1/sqrt(2*pi*R*T)`` is
common and cancels, leaving
``sum_i (nO_i - dO_i)*alpha_i*P_i/sqrt(M_i) = 0``. The signed weight
``(nO - dO)`` equals ``2*n`` from the reaction, not from the local
slope. Off the cap the slope in ``log10(p)`` has the sign of ``n^2``
and does not fold; on the cap the metal term is flat and O2 still
lifts the residual. The independent variable is ``log10(p/bar)`` on
``(-30, 0]``.

Transport. A ``transport_headspace`` carrier, including SiO, is priced
at the headspace oxygen potential. It is not a function of the melt
surface pO2. The closure does not rescale it, refuse it, or feed it
into this residual. Metal rails stay in the residual, including a
capped condensed metal: the cap is a pressure law, not a transport
channel.

Units. ``p`` and ``p_ref`` are bar. ``P`` is Pa. The synthetic O2 channel,
used only when O2 is absent from the vapor map, is ``P_O2 = p*1e5``
Pa/bar. ``M`` is kg/mol, ``T`` is K, ``R`` is J mol^-1 K^-1, alpha is
dimensionless, and ``J`` is mol m^-2 s^-1. The reported residual is the
relative oxygen-flux residual, dimensionless. Ideal-alpha mass fluxes
quoted against this root are ``alpha*P*k_HK*3600``, in kg m^-2 h^-1.

Limiting case. Congruent CaO with only Ca(g) and O2(g), alpha = 1:
``n_Ca = -1/2``, ``nO_Ca = 0``, so ``dO_Ca = 1``; O2 has ``n = +1``,
``nO = 2``, ``dO = 0``. One parent oxygen leaves with each Ca, as half an
O2 molecule, so ``J_Ca = 2*J_O2``:
``P_Ca/sqrt(M_Ca) = 2*P_O2/sqrt(M_O2)``, with ``P_O2 = p*1e5`` and
``P_Ca`` proportional to ``p^(-1/2)``. Neutral oxide gas (``n = 0``, for
example CaO(g)) has weight 0, does not move the root, and keeps its
published pressure. Atomic O(g) is not added. The ordinary catalog often
omits it, so this root sits slightly off a pure-oxide congruent solution
that carries O. Positive-exponent carriers that the provider dropped below
its reporting threshold are the same omission: they are not invented back
into the balance.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.accounting.formulas import (
    SpeciesFormula,
    parse_formula,
    resolve_species_formula,
)

LANGMUIR_SURFACE_PO2_REASON = (
    "langmuir_surface_pO2_from_evaporative_oxygen_balance"
)
_RESIDUAL_CLOSE_MAX = 1.0e-4
_METAL_RAILS = frozenset({"gas_fugacity", "condensed_raoult_psat"})


@dataclass(frozen=True)
class LangmuirSurfaceOxygen:
    """Flux-map adjustment. ``pressures_Pa`` is the caller's map when not applied."""

    pressures_Pa: Mapping[str, float]
    applied: bool
    surface_pO2_bar: float | None
    relative_residual: float | None
    warning: str | None
    refused_species: Mapping[str, str]
    persisted_pO2_bar_by_species: Mapping[str, float]
    capacity_negligible: bool = False
    eval_pO2_below_physical_floor: bool = False
    overhead_bounded: bool = False

    def as_diagnostic(self) -> dict[str, Any]:
        return {
            "reason": LANGMUIR_SURFACE_PO2_REASON,
            "applied": self.applied,
            "surface_pO2_bar": self.surface_pO2_bar,
            "relative_residual": self.relative_residual,
            "warning": self.warning,
            "refused_species": dict(self.refused_species),
            "persisted_pO2_bar_by_species": dict(
                self.persisted_pO2_bar_by_species
            ),
            "capacity_negligible": self.capacity_negligible,
            "eval_pO2_below_physical_floor": (
                self.eval_pO2_below_physical_floor
            ),
            "overhead_bounded": self.overhead_bounded,
        }


@dataclass(frozen=True)
class _Carrier:
    species: str
    pressure_Pa: float
    # Mass-action slope of the parent reaction. Oxygen demand uses this,
    # including while a condensed-metal cap holds the local slope at 0.
    reaction_exponent: float | None
    p_ref_bar: float | None
    inverted: bool
    # Headspace oxygen potential. Published pressure is not rewritten.
    transport_channel: bool
    # Condensed rail published at pure-metal activity.
    activity_capped: bool
    raw_activity: float | None


def _needs_surface_balance(carrier: _Carrier) -> bool:
    """True when this carrier is a melt-surface participant.

    Opening the solve is a separate gate (physical floor or the
    reservoir no-buffer test). Transport-channel pressures are not
    participants: they do not open the solve and are not rewritten.
    A capped metal still participates. Its reaction exponent stays
    negative even though the published pressure is flat.
    """

    return (
        not carrier.transport_channel
        and carrier.reaction_exponent is not None
        and carrier.reaction_exponent < 0.0
        and carrier.p_ref_bar is not None
        and carrier.pressure_Pa > 0.0
    )


def _eval_pO2_bar(
    source_reaction_fO2_bar: float | None,
    carriers: list[_Carrier],
) -> float | None:
    """pO2 that published the oxygen-coupled pressures.

    The provider's source-reaction fO2 is that evaluation. If it is
    missing, the lowest metal-rail reference is the stand-in: that is
    the pO2 the mass-action law actually used.
    """

    source = _positive(source_reaction_fO2_bar)
    if source is not None:
        return source
    refs = [
        carrier.p_ref_bar
        for carrier in carriers
        if _needs_surface_balance(carrier) and carrier.p_ref_bar is not None
    ]
    if not refs:
        return None
    return min(refs)


def _capacity_is_negligible(capacity_mol_per_ln_fO2: float | None) -> bool:
    """True when d-072 has no effective redox buffer (``C_m ~ 0``).

    d-376 amendment 1. The value is the reservoir source capacity from
    ``_melt_redox_source_capacity_mol_per_ln_fO2``, the same operand as
    ``no_melt_redox_capacity``. That already includes the reservoir
    liquid-fraction factor. ``equilibrium.liquid_fraction`` is a
    different number and must not be multiplied in here. The reservoir
    integrates ``Δln fO2 = n_O2 / C_m``. At
    ``C_m <= OXYGEN_RESERVOIR_NOOP_MOL`` (1e-15 mol per ln fO2) that
    quotient is not a buffer and the oxygen is not taken up by
    Fe2+/Fe3+. Pure CaO has ``n_Fe = 0``, so ``C_m = 0`` and this test
    is true. A missing capacity is not treated as zero: the caller did
    not show that the buffer is gone.
    """

    if capacity_mol_per_ln_fO2 is None:
        return False
    try:
        capacity = float(capacity_mol_per_ln_fO2)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(capacity):
        return False
    from simulator.core import OXYGEN_RESERVOIR_NOOP_MOL

    return capacity <= float(OXYGEN_RESERVOIR_NOOP_MOL)


def _eval_pO2_is_physical_floor(eval_pO2_bar: float | None) -> bool:
    """True at the b-148 dissociation floor (the 2.6e13 Pa Ca case)."""

    if eval_pO2_bar is None:
        return False
    from engines.builtin.vapor_pressure import _pO2_is_melt_dissociation_floor

    return bool(_pO2_is_melt_dissociation_floor(eval_pO2_bar))


def apply_langmuir_surface_oxygen(
    pressures_Pa: Mapping[str, float],
    *,
    temperature_K: float,
    provenance_by_species: Mapping[str, Mapping[str, Any]] | None = None,
    source_reaction_fO2_bar: float | None = None,
    transport_pO2_bar: float | None = None,
    legacy_catalog: Mapping[str, Any] | None = None,
    compiled_catalog: Any | None = None,
    melt_redox_capacity_mol_per_ln_fO2: float | None = None,
    overhead_pO2_bar: float | None = None,
) -> LangmuirSurfaceOxygen:
    """Return flux pressures at the d-376 amendment 1 surface pO2.

    The caller owns ``pressures_Pa``. This function does not mutate it.
    The congruent root runs only when ``eval pO2`` is at the physical
    floor or the reservoir source capacity is negligible. Otherwise the
    map is the reservoir / mass-action publication. A successful root
    copies the map and rescales every melt-surface carrier at one
    shared pO2. Reaction stoichiometry, not the local cap slope, is the
    oxygen demand, and a condensed-metal cap follows the uncap law.
    Transport-headspace pressures, including SiO, stay published.
    The surface is then raised to the overhead pO2 while O2 is
    net-leaving. A failed root drops only the below-band carriers and
    names the reason. Neither path writes a silent 0.
    """

    provenance_by_species = provenance_by_species or {}
    legacy = _legacy_view(legacy_catalog)
    carriers = [
        _classify_carrier(
            species=str(species),
            pressure_Pa=pressure,
            provenance=provenance_by_species.get(species) or {},
            source_reaction_fO2_bar=source_reaction_fO2_bar,
            transport_pO2_bar=transport_pO2_bar,
            row=_legacy_row(legacy, str(species)),
            compiled_catalog=compiled_catalog,
        )
        for species, pressure in pressures_Pa.items()
    ]
    persisted = {
        carrier.species: carrier.p_ref_bar
        for carrier in carriers
        if carrier.p_ref_bar is not None
    }
    inverted = [carrier for carrier in carriers if carrier.inverted]
    if not any(_needs_surface_balance(carrier) for carrier in carriers):
        return LangmuirSurfaceOxygen(
            pressures_Pa=pressures_Pa,
            applied=False,
            surface_pO2_bar=None,
            relative_residual=None,
            warning=None,
            refused_species={},
            persisted_pO2_bar_by_species=persisted,
        )

    eval_pO2_bar = _eval_pO2_bar(source_reaction_fO2_bar, carriers)
    below_floor = _eval_pO2_is_physical_floor(eval_pO2_bar)
    capacity_negligible = _capacity_is_negligible(
        melt_redox_capacity_mol_per_ln_fO2
    )
    # d-376 amendment 1: congruent closure only for the physical floor
    # or a melt with no effective buffer. A buffered in-band melt keeps
    # the mass-action map.
    if not below_floor and not capacity_negligible:
        return LangmuirSurfaceOxygen(
            pressures_Pa=pressures_Pa,
            applied=False,
            surface_pO2_bar=None,
            relative_residual=None,
            warning=None,
            refused_species={},
            persisted_pO2_bar_by_species=persisted,
        )

    try:
        temperature = float(temperature_K)
    except (TypeError, ValueError):
        temperature = math.nan
    if not math.isfinite(temperature) or temperature <= 0.0:
        return _refuse_inverted(
            pressures_Pa,
            inverted,
            persisted,
            detail="temperature_K is not finite and positive",
            capacity_negligible=capacity_negligible,
            eval_pO2_below_physical_floor=below_floor,
        )

    return _solve_or_refuse(
        pressures_Pa,
        carriers=carriers,
        inverted=inverted,
        persisted=persisted,
        temperature_K=temperature,
        rows=legacy,
        legacy_catalog=legacy_catalog,
        overhead_pO2_bar=overhead_pO2_bar,
        capacity_negligible=capacity_negligible,
        eval_pO2_below_physical_floor=below_floor,
    )


def _certified_min_bar() -> float:
    from engines.builtin.vapor_pressure import (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
    )

    return float(MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR)


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    return number


def _legacy_view(legacy_catalog: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(legacy_catalog, Mapping) or not legacy_catalog:
        return {}
    from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

    return vapor_pressure_legacy_view(legacy_catalog)


def _legacy_row(legacy: Mapping[str, Any], species: str) -> Mapping[str, Any] | None:
    for group_name in ("metals", "oxide_vapors"):
        group = legacy.get(group_name) or {}
        if not isinstance(group, Mapping):
            continue
        row = group.get(species)
        if isinstance(row, Mapping):
            return row
    return None


def _evaluator(compiled_catalog: Any, species: str) -> Any | None:
    species_map = getattr(compiled_catalog, "species", None)
    if not isinstance(species_map, Mapping):
        return None
    compiled = species_map.get(species)
    if compiled is None:
        return None
    return getattr(compiled, "evaluator", None)


def _condensed_activity_capped(provenance: Mapping[str, Any]) -> bool:
    """True when the condensed rail is already sitting on pure metal."""

    if str(provenance.get("pressure_rail") or "") != "condensed_raoult_psat":
        return False
    activity = _positive(provenance.get("activity_factor"))
    raw = _positive(provenance.get("raw_metal_activity_root"))
    if activity is None or raw is None:
        return False
    return activity >= 1.0 - 1.0e-12 and raw > 1.0


def _is_transport_channel(
    species: str,
    provenance: Mapping[str, Any],
    evaluator: Any | None,
    rail: str,
) -> bool:
    """True when the published pressure is not a melt-surface function.

    Compiled rows name ``transport_headspace``. SiO is that channel
    even when the evaluator lookup misses. Metal rails are the melt
    dissociation law, cap included; a transport tag does not take them
    out of the oxygen residual.
    """

    if rail in _METAL_RAILS:
        return False
    channel = provenance.get("oxygen_fugacity_channel")
    if channel is None and evaluator is not None:
        channel = getattr(evaluator, "oxygen_fugacity_channel", None)
    if channel == "transport_headspace":
        return True
    return species == "SiO"


def _reaction_exponent(
    species: str,
    provenance: Mapping[str, Any],
    *,
    evaluator: Any | None,
    row: Mapping[str, Any] | None,
) -> float | None:
    """Mass-action pO2 exponent of the parent reaction.

    This is not the local slope. A condensed-metal cap freezes the
    published pressure and is applied later, in the pressure law.
    """

    rail = str(provenance.get("pressure_rail") or "")
    if rail in _METAL_RAILS:
        try:
            from simulator.chemistry.ellingham_thermo import (
                ellingham_stoichiometry,
            )

            n_metal, _n_oxide = ellingham_stoichiometry(species)
            n_metal = float(n_metal)
        except (KeyError, TypeError, ValueError):
            n_metal = math.nan
        if math.isfinite(n_metal) and n_metal != 0.0:
            return -1.0 / n_metal
    if evaluator is not None:
        exponent = _finite_or_none(getattr(evaluator, "pO2_exponent", None))
        if exponent is not None:
            return exponent
    if isinstance(row, Mapping) and "pO2_exponent" in row:
        exponent = _finite_or_none(row.get("pO2_exponent"))
        if exponent is not None:
            return exponent
    return _finite_or_none(provenance.get("pO2_exponent"))


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _reference_pO2_bar(
    species: str,
    provenance: Mapping[str, Any],
    *,
    evaluator: Any | None,
    source_reaction_fO2_bar: float | None,
    transport_pO2_bar: float | None,
) -> float | None:
    """pO2 that actually entered this carrier's mass action.

    Metal rails store that value on provenance. Compiled oxide rows do not:
    intrinsic-melt evaluators (Ca2 and the other oxide vapors) are solved at
    ``source_reaction_fO2_bar`` while provenance records transport pO2, except
    the P2O5 special case. Using the recorded transport value as p_ref
    mis-scales those carriers by the melt/transport gap.
    """

    recorded = _positive(provenance.get("pO2_bar"))
    rail = str(provenance.get("pressure_rail") or "")
    if rail in _METAL_RAILS:
        return recorded
    channel = getattr(evaluator, "oxygen_fugacity_channel", None)
    source = _positive(source_reaction_fO2_bar)
    transport = _positive(transport_pO2_bar)
    if channel == "intrinsic_melt":
        return source if source is not None else recorded
    if channel == "transport_headspace" or species == "SiO":
        return recorded if recorded is not None else transport
    return recorded if recorded is not None else source


def _classify_carrier(
    *,
    species: str,
    pressure_Pa: Any,
    provenance: Mapping[str, Any],
    source_reaction_fO2_bar: float | None,
    transport_pO2_bar: float | None,
    row: Mapping[str, Any] | None,
    compiled_catalog: Any | None,
) -> _Carrier:
    pressure = _positive(pressure_Pa)
    if pressure is None:
        return _Carrier(species, 0.0, None, None, False, False, False, None)
    evaluator = _evaluator(compiled_catalog, species)
    row_map = row if isinstance(row, Mapping) else None
    rail = str(provenance.get("pressure_rail") or "")
    reaction_exponent = _reaction_exponent(
        species,
        provenance,
        evaluator=evaluator,
        row=row_map,
    )
    transport_channel = _is_transport_channel(
        species,
        provenance,
        evaluator,
        rail,
    )
    activity_capped = _condensed_activity_capped(provenance)
    raw_activity = (
        _positive(provenance.get("raw_metal_activity_root"))
        if activity_capped
        else None
    )
    p_ref = _reference_pO2_bar(
        species,
        provenance,
        evaluator=evaluator,
        source_reaction_fO2_bar=source_reaction_fO2_bar,
        transport_pO2_bar=transport_pO2_bar,
    )
    # Below-band follows the reaction, not the frozen cap slope. A
    # capped metal at the physical floor is still below the certified
    # band and is refused when the balance cannot close.
    inverted = bool(
        not transport_channel
        and reaction_exponent is not None
        and reaction_exponent < 0.0
        and p_ref is not None
        and p_ref < _certified_min_bar()
    )
    return _Carrier(
        species,
        pressure,
        reaction_exponent,
        p_ref,
        inverted,
        transport_channel,
        activity_capped,
        raw_activity,
    )


def _formula_registry(legacy: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    if "metals" in legacy or "oxide_vapors" in legacy:
        groups = [
            legacy.get(name) or {}
            for name in ("metals", "oxide_vapors")
        ]
    else:
        groups = [legacy]
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        for species, row in group.items():
            if not isinstance(row, Mapping):
                continue
            formula = row.get("formula")
            if not isinstance(formula, str) or not formula.strip():
                continue
            # Catalog molar masses are rounded (Ca 40.08 vs CIAAW 40.078)
            # and fail the formula checker. Atom counts come from the
            # formula; the molar mass is the derived one.
            registry[str(species)] = {"formula": formula}
    return registry


def _species_formula(
    species: str,
    registry: Mapping[str, Any],
) -> SpeciesFormula | None:
    try:
        return resolve_species_formula(species, registry)
    except (LookupError, ValueError, ArithmeticError):
        return None


def _flux_alpha(
    species: str,
    alpha_by_species: Mapping[str, Any],
    temperature_K: float,
) -> tuple[float | None, str]:
    if species not in alpha_by_species:
        return None, ""
    from engines.builtin.evaporation_flux import _evaluate_alpha_control

    try:
        value, evaluation = _evaluate_alpha_control(
            species,
            temperature_K,
            alpha_by_species[species],
        )
    except (TypeError, ValueError):
        return None, ""
    alpha = _finite_or_none(value)
    if alpha is None or alpha < 0.0:
        return None, ""
    status = ""
    if isinstance(evaluation, Mapping):
        status = str(evaluation.get("authority_status") or "")
    source = status or "catalog evaporation_alpha"
    return alpha, f"evaporation_flux_alpha:{source}"


def _parent_oxygen_demand(oxygen_atoms: float, exponent: float) -> float:
    """Oxygen atoms the parent must supply per vapor molecule.

    This is ``dO = nO_gas - 2*n`` from the module docstring. Checks:
    Ca n=-1/2, nO=0 demands 1; SiO n=-1/2, nO=1 demands 2; CaO(g) n=0,
    nO=1 demands 1; O2 n=+1, nO=2 demands 0. The signed weight
    (nO - demand) is 2n.
    """

    return oxygen_atoms - 2.0 * exponent


def _scaled_pressure_Pa(
    pressure_Pa: float,
    exponent: float,
    p_ref_bar: float,
    surface_pO2_bar: float,
) -> float:
    if exponent == 0.0 or surface_pO2_bar == p_ref_bar:
        return pressure_Pa
    # log10(P) = log10(P_pub) + n * log10(p / p_ref). Log space keeps a
    # floor pressure of 1e18 Pa from overflowing before the ratio is applied.
    log_p = math.log10(pressure_Pa) + exponent * math.log10(
        surface_pO2_bar / p_ref_bar
    )
    if not math.isfinite(log_p) or log_p > 300.0:
        raise ValueError("langmuir pressure law overflowed")
    if log_p < -300.0:
        return 0.0
    return 10.0 ** log_p


def _capped_pressure_Pa(carrier: _Carrier, surface_pO2_bar: float) -> float:
    """Condensed-metal pressure, capped at P_sat until the activity uncapped.

    ``P = P_sat`` while ``raw*(p/p_ref)^n >= 1``. Above that pO2,
    ``P = P_sat * raw * (p/p_ref)^n``. ``n`` is the reaction exponent
    (negative). Log space avoids forming ``P_sat * raw`` when the raw
    activity at the floor is huge. Unit check: ``raw`` is dimensionless,
    both pressures are bar, ``P`` is Pa. The ``log_raw >= 0`` branch is
    the pure-metal cap; the other branch is the mass-action continuation.
    """

    assert carrier.reaction_exponent is not None
    assert carrier.p_ref_bar is not None
    assert carrier.raw_activity is not None
    log_raw = math.log10(carrier.raw_activity) + (
        carrier.reaction_exponent
        * math.log10(surface_pO2_bar / carrier.p_ref_bar)
    )
    if log_raw >= 0.0:
        return carrier.pressure_Pa
    log_p = math.log10(carrier.pressure_Pa) + log_raw
    if not math.isfinite(log_p) or log_p > 300.0:
        raise ValueError("langmuir pressure law overflowed")
    if log_p < -300.0:
        return 0.0
    return 10.0 ** log_p


def _pressure_at_surface(carrier: _Carrier, surface_pO2_bar: float) -> float:
    """Melt-surface pressure law. Transport carriers never reach this."""

    if carrier.activity_capped:
        return _capped_pressure_Pa(carrier, surface_pO2_bar)
    assert carrier.reaction_exponent is not None
    assert carrier.p_ref_bar is not None
    return _scaled_pressure_Pa(
        carrier.pressure_Pa,
        carrier.reaction_exponent,
        carrier.p_ref_bar,
        surface_pO2_bar,
    )


def _solve_piecewise_surface_balance(
    coupled: list[_Carrier],
    channels: list[Any],
    *,
    temperature_K: float,
) -> Any:
    """Root of the cap-aware pressure law.

    The shared vacuum solver checks that every channel is one power law.
    A condensed-metal cap is flat and then a power law, so that check
    does not apply. The residual is the same one: reaction demand, not
    the local slope, times the Hertz-Knudsen weight. O2 is strictly
    increasing, so the sum does not fold across the kink.
    """

    from scipy.optimize import brentq

    from simulator.battery.oxygen_balance import (
        OxygenBalanceBracketBoundary,
        _VacuumOxygenBalance,
    )
    from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR

    by_species = {carrier.species: carrier for carrier in coupled}
    low, high = -30.0, 0.0

    def parts(
        logp: float,
    ) -> tuple[float, dict[str, float], float, float]:
        surface = 10.0 ** logp
        oxygen = 0.0
        parent = 0.0
        pressures: dict[str, float] = {}
        for channel in channels:
            carrier = by_species.get(channel.species)
            if carrier is None:
                pressure = surface * PA_PER_BAR
            else:
                pressure = _pressure_at_surface(carrier, surface)
            if not math.isfinite(pressure) or pressure < 0.0:
                raise ValueError(
                    f"pressure law returned invalid {channel.species!r} pressure"
                )
            pressures[channel.species] = pressure
            # Proportional to mol m^-2 s^-1. The shared 1/sqrt(2*pi*R*T)
            # cancels in the residual and in the relative residual.
            factor = channel.alpha * pressure / math.sqrt(
                channel.molar_mass_kg_per_mol
            )
            oxygen += channel.oxygen_atoms * factor
            parent += channel.parent_oxygen_demand * factor
        return oxygen - parent, pressures, oxygen, parent

    f_low = parts(low)[0]
    f_high = parts(high)[0]
    bracket: OxygenBalanceBracketBoundary | None
    if f_low == 0.0:
        root = low
        bracket = "lower"
    elif f_high == 0.0:
        root = high
        bracket = "upper"
    elif f_low > 0.0 and f_high > 0.0:
        root = low
        bracket = "lower"
    elif f_low < 0.0 and f_high < 0.0:
        raise ValueError(
            "vacuum oxygen balance has no root in the declared pO2 bracket"
        )
    else:
        root = float(brentq(
            lambda value: parts(value)[0],
            low,
            high,
            xtol=1.0e-12,
            rtol=1.0e-12,
            maxiter=200,
        ))
        bracket = None

    residual, pressures, oxygen, parent = parts(root)
    relative = abs(residual) / max(oxygen, parent, 1.0e-300)
    hk = 1.0 / math.sqrt(2.0 * math.pi * GAS_CONSTANT * temperature_K)
    fluxes = {
        channel.species: hk * channel.alpha * pressures[channel.species] / math.sqrt(
            channel.molar_mass_kg_per_mol
        )
        for channel in channels
    }
    return _VacuumOxygenBalance(
        pO2_bar=10.0 ** root,
        bracket_boundary=bracket,
        pressures_Pa=pressures,
        channel_fluxes_mol_m2_s=fluxes,
        oxygen_atom_flux_mol_m2_s=oxygen * hk,
        parent_oxygen_demand_flux_mol_m2_s=parent * hk,
        relative_residual=relative,
        alpha_sources={
            channel.species: channel.alpha_source for channel in channels
        },
    )


def _surface_with_overhead_floor(
    vacuum_pO2_bar: float,
    o2_flux_mol_m2_s: float,
    overhead_pO2_bar: float | None,
) -> tuple[float, bool]:
    """Raise a net-leaving O2 root up to the overhead partial.

    d-376 amendment 1. The vacuum root is the pO2 where the O2 flux
    balances the parent oxygen demand against zero overhead. Net O2
    leaving the surface into an overhead requires
    ``p_surface >= p_overhead``: a root below that partial would be
    O2 flowing toward the surface. The bound is that floor. Metal
    pressures then follow ``P = P_pub * (p / p_ref)^n`` at the floored
    p. The reported residual stays the vacuum root's residual; the
    overhead is a bound, not a second root. ``o2_flux`` is mol m^-2 s^-1
    and both pressures are bar.
    """

    overhead = _positive(overhead_pO2_bar)
    if (
        overhead is None
        or overhead <= vacuum_pO2_bar
        or not math.isfinite(o2_flux_mol_m2_s)
        or o2_flux_mol_m2_s <= 0.0
    ):
        return vacuum_pO2_bar, False
    return overhead, True


def _solve_or_refuse(
    pressures_Pa: Mapping[str, float],
    *,
    carriers: list[_Carrier],
    inverted: list[_Carrier],
    persisted: Mapping[str, float],
    temperature_K: float,
    rows: Mapping[str, Any],
    legacy_catalog: Mapping[str, Any] | None,
    overhead_pO2_bar: float | None = None,
    capacity_negligible: bool = False,
    eval_pO2_below_physical_floor: bool = False,
) -> LangmuirSurfaceOxygen:
    registry = _formula_registry(rows)
    from simulator.evaporation import (
        _load_evaporation_alpha_by_species,
        _measured_alpha_control_view,
    )

    alphas = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(dict(legacy_catalog or {}))
    )
    from simulator.battery.oxygen_balance import (
        _OXYGEN_GAS_ALPHA,
        _OXYGEN_GAS_ALPHA_SOURCE,
        _VacuumOxygenChannel,
        _solve_vacuum_oxygen_balance,
    )

    refused: dict[str, str] = {}
    channels: list[Any] = []
    coupled: list[_Carrier] = []
    for carrier in carriers:
        # Transport-channel flux stays at the published pressure. It is
        # not refused and it does not enter this melt-surface residual.
        if carrier.transport_channel:
            continue
        if carrier.reaction_exponent is None or carrier.p_ref_bar is None:
            if carrier.inverted:
                refused[carrier.species] = (
                    "missing pO2 exponent or reference; inverted pressure omitted"
                )
            continue
        if carrier.species in refused:
            continue
        formula = _species_formula(carrier.species, registry)
        if formula is None:
            if carrier.inverted:
                refused[carrier.species] = (
                    "formula unresolved; inverted pressure omitted"
                )
            continue
        alpha, alpha_source = _flux_alpha(
            carrier.species, alphas, temperature_K
        )
        if alpha is None:
            if carrier.inverted:
                refused[carrier.species] = (
                    "no numeric evaporation alpha; inverted pressure omitted"
                )
            continue
        if alpha == 0.0:
            continue
        oxygen_atoms = float(formula.elements.get("O", 0.0))
        # Reaction exponent, never the capped local slope. A pure-metal
        # cap still releases the parent oxide's oxygen.
        demand = _parent_oxygen_demand(
            oxygen_atoms, carrier.reaction_exponent
        )
        if demand < 0.0:
            if carrier.inverted:
                refused[carrier.species] = (
                    "oxygen demand is negative; inverted pressure omitted"
                )
            continue
        channels.append(
            _VacuumOxygenChannel(
                species=carrier.species,
                molar_mass_kg_per_mol=formula.molar_mass_kg_per_mol(),
                oxygen_atoms=oxygen_atoms,
                parent_oxygen_demand=demand,
                pO2_exponent=carrier.reaction_exponent,
                alpha=alpha,
                alpha_source=alpha_source,
            )
        )
        coupled.append(carrier)

    if not any(channel.pO2_exponent < 0.0 for channel in channels):
        return _refuse_inverted(
            pressures_Pa,
            inverted,
            persisted,
            detail="no oxygen-coupled carrier could enter the balance",
            extra_refused=refused,
            capacity_negligible=capacity_negligible,
            eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
        )

    o2_formula = parse_formula("O2")
    if all(channel.species != "O2" for channel in channels):
        channels.append(
            _VacuumOxygenChannel(
                species="O2",
                molar_mass_kg_per_mol=o2_formula.molar_mass_kg_per_mol(),
                oxygen_atoms=2.0,
                parent_oxygen_demand=0.0,
                pO2_exponent=1.0,
                alpha=_OXYGEN_GAS_ALPHA,
                alpha_source=_OXYGEN_GAS_ALPHA_SOURCE,
            )
        )

    laws = {
        carrier.species: carrier
        for carrier in coupled
        if carrier.species not in refused
    }

    def pressure_model(log10_pO2_bar: float) -> dict[str, float]:
        surface = 10.0 ** log10_pO2_bar
        modeled: dict[str, float] = {}
        for carrier in laws.values():
            # Uncapped carriers only. A cap is piecewise and uses the
            # other solver; this model stays one power law per channel
            # so the exponent check still applies.
            assert carrier.reaction_exponent is not None
            assert carrier.p_ref_bar is not None
            modeled[carrier.species] = _scaled_pressure_Pa(
                carrier.pressure_Pa,
                carrier.reaction_exponent,
                carrier.p_ref_bar,
                surface,
            )
        if "O2" not in laws:
            from simulator.physical_constants import PA_PER_BAR

            modeled["O2"] = surface * PA_PER_BAR
        return modeled

    try:
        if any(carrier.activity_capped for carrier in coupled):
            balance = _solve_piecewise_surface_balance(
                coupled,
                channels,
                temperature_K=temperature_K,
            )
        else:
            balance = _solve_vacuum_oxygen_balance(
                pressure_model,
                channels,
                temperature_K=temperature_K,
            )
    except ValueError as exc:
        return _refuse_inverted(
            pressures_Pa,
            inverted,
            persisted,
            detail=str(exc),
            extra_refused=refused,
            capacity_negligible=capacity_negligible,
            eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
        )

    residual = float(balance.relative_residual)
    pinned = (
        balance.bracket_boundary == "lower" and residual > _RESIDUAL_CLOSE_MAX
    )
    if pinned or residual > _RESIDUAL_CLOSE_MAX or not math.isfinite(
        balance.pO2_bar
    ) or balance.pO2_bar <= 0.0:
        return _refuse_inverted(
            pressures_Pa,
            inverted,
            persisted,
            detail=(
                "oxygen balance did not close "
                f"(relative_residual={residual:.3e}, "
                f"bracket={balance.bracket_boundary})"
            ),
            extra_refused=refused,
            capacity_negligible=capacity_negligible,
            eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
        )

    surface_pO2_bar, overhead_bounded = _surface_with_overhead_floor(
        float(balance.pO2_bar),
        float(balance.channel_fluxes_mol_m2_s.get("O2", 0.0) or 0.0),
        overhead_pO2_bar,
    )
    adjusted = dict(pressures_Pa)
    for species in refused:
        adjusted.pop(species, None)
    try:
        for carrier in coupled:
            if carrier.species in refused or carrier.reaction_exponent is None:
                continue
            if carrier.p_ref_bar is None:
                continue
            if overhead_bounded or carrier.activity_capped:
                adjusted[carrier.species] = _pressure_at_surface(
                    carrier,
                    surface_pO2_bar,
                )
                continue
            solved = balance.pressures_Pa.get(carrier.species)
            if solved is None:
                continue
            adjusted[carrier.species] = float(solved)
    except ValueError as exc:
        return _refuse_inverted(
            pressures_Pa,
            inverted,
            persisted,
            detail=str(exc),
            extra_refused=refused,
            capacity_negligible=capacity_negligible,
            eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
        )
    warning = (
        f"{LANGMUIR_SURFACE_PO2_REASON}: "
        f"surface_pO2_bar={surface_pO2_bar:.6e} "
        f"relative_residual={residual:.3e}"
    )
    if overhead_bounded:
        warning += f" overhead_pO2_bar={surface_pO2_bar:.6e}"
    if refused:
        warning += " refused=" + ",".join(sorted(refused))
    return LangmuirSurfaceOxygen(
        pressures_Pa=adjusted,
        applied=True,
        surface_pO2_bar=surface_pO2_bar,
        relative_residual=residual,
        warning=warning,
        refused_species=dict(refused),
        persisted_pO2_bar_by_species=dict(persisted),
        capacity_negligible=capacity_negligible,
        eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
        overhead_bounded=overhead_bounded,
    )


def _refuse_inverted(
    pressures_Pa: Mapping[str, float],
    inverted: list[_Carrier],
    persisted: Mapping[str, float],
    *,
    detail: str,
    extra_refused: Mapping[str, str] | None = None,
    capacity_negligible: bool = False,
    eval_pO2_below_physical_floor: bool = False,
) -> LangmuirSurfaceOxygen:
    refused = {
        carrier.species: (
            "langmuir surface pO2 unavailable; inverted pressure omitted: "
            + detail
        )
        for carrier in inverted
    }
    if extra_refused:
        refused.update(dict(extra_refused))
    adjusted = {
        species: pressure
        for species, pressure in pressures_Pa.items()
        if species not in refused
    }
    warning = (
        f"{LANGMUIR_SURFACE_PO2_REASON}: surface_pO2_unavailable "
        f"refused={','.join(sorted(refused)) or 'none'} detail={detail}"
    )
    return LangmuirSurfaceOxygen(
        pressures_Pa=adjusted,
        applied=True,
        surface_pO2_bar=None,
        relative_residual=None,
        warning=warning,
        refused_species=refused,
        persisted_pO2_bar_by_species=dict(persisted),
        capacity_negligible=capacity_negligible,
        eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
    )
