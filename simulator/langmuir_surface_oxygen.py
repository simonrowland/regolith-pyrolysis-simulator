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

Overhead. The root is the oxygen balance of the net fluxes the
evaporation publisher will emit at the same partials: Hertz-Knudsen
drive ``max(0, P_eq - P_bulk)`` and, where that publisher enables it,
the species-specific gas-film resistance. A vacuum root that is then
clamped to the overhead partial is not a root of that balance. While
O2 is net-leaving, the solved surface is not below the overhead pO2,
because the O2 drive is zero at and below that partial. Inside this
root the O2 partial is that backpressure on every transport branch.
``species_drive_bulk_Pa`` is the only bulk decision. This module calls
it with ``surface_oxygen_bound`` true. The flux publisher passes the
same flag only when the root closed; a closed gate uses green's film
rule for O2 as well. A disabled film and an HKL upper bound set
``R_gas = 0`` (ideal Hertz-Knudsen); they do not zero the O2 partial
of a root that owns the drive. Free-molecular
``k_O2/k_Ca = sqrt(M_Ca/M_O2) = 1.119``. The 10 mbar film ratio is
1.424. ``p_total = 0`` with no O2 partial recovers the vacuum root.
The exit check compares the solved surface with the caller's partial,
not with a bulk a branch already discarded.

A failed root refuses every residual participant. Below-band and
in-band carriers are the same defect once the gate is open, and so
are positive exponents: AlO2, CrO2 and CrO3 have ``n > 0`` and oxygen
weight ``2*n``, so they are not neutral. The persisted evaluation
pressure was not a closed balance. Transport channels and ``n == 0``
carriers stay published. Nothing is written as 0.

Invariant. ``apply_langmuir_surface_oxygen`` is the only writer of the
flux map, and every caller (live evaporation and each finite-capacity
partial vector) enters it with the partials of that evaluation. The
closure runs only when the evaluated pO2 is at the physical floor or
the reservoir source capacity is at or below
``OXYGEN_RESERVOIR_NOOP_MOL`` (d-072 ``no_melt_redox_capacity``).
``equilibrium.liquid_fraction`` is not that test. While O2 is
net-leaving, the solved surface is not below the caller's overhead
O2 partial. That partial is the O2 drive bulk for every film flag
and every HKL membership; the exit of this solve checks the caller's
partial, not a bulk a branch zeroed. The flux publisher uses that
bulk only when it is emitting this root. A closed gate does not.
Every species is then one of
three outcomes: the published pressure (closure closed,
transport-headspace channel, or no melt-surface law), the single
surface-pO2 law below, or a typed refusal. A required solve that
does not close is the refusal, including an in-band carrier whose
reference pO2 is above the certified minimum and a positive-exponent
carrier in the same residual. Refusing only the below-band set, or
only ``n < 0``, would publish that unclosed pressure. A metal rail
(``gas_fugacity`` or ``condensed_raoult_psat``) with no recorded
``pO2_bar`` does not borrow the source fO2 as its reference. That
missing reference does not close the gate: a negative exponent still
runs the floor and empty-buffer tests, and an open gate omits the
pressure with ``missing pO2 reference``. Returning the persisted
pressure is the unclosed value. The published surface is
the root of ``sum (nO_i - dO_i) J_i = 0`` where ``J_i`` is that
species' publisher net flux at those partials, and the reported
residual is that root's residual. The alpha in ``J_i`` and the
gas-film flag are the publisher's, from ``publisher_effective_alpha``
and ``resolve_gas_resistance_enabled``: a measured row, a ``*``
wildcard, or a permitted unmeasured fallback (broad-proxy rows are
stripped, then alpha = 1 when the fallback allows that species). A
carrier the policy does not emit is not in the residual. On a
successful root an ``n != 0`` carrier in that set is a typed refusal
and is absent from the flux map: the publisher emits unflagged
alpha 1 for an unmeasured species when the parent mass is at or
below 1e-12 kg or the alpha-1 baseline is at or below the
nontrivial-flux threshold, so the persisted pressure would be that
emission. A measured alpha of 0 emits no flux, so it is not a
residual term, and its pressure is still the surface law. Nothing writes
``0.0`` in place of a published pressure. Nothing drops a
participating carrier's reaction oxygen demand because a cap zeroed
the local slope. A condensed-metal rail never publishes above
``P_sat``, including when the solve itself drives a subunit activity
across 1.

Algebra. Reaction stoichiometry and pressure sensitivity are separate.
The reaction exponent ``n`` is the mass-action slope off any activity
cap. A carrier published at ``P_pub`` (Pa) and reference ``p_ref``
(bar) is ``P(p) = P_pub*(p/p_ref)^n`` at surface ``p`` (bar) when it
is not capped. Every condensed-metal rail uses that cap, not only a
rail already published at pure metal. ``P_pub = min(raw, 1)*P_sat``.
Activity at surface ``p`` is ``raw*(p/p_ref)^n``. While that product
is at least 1, ``P = P_sat``. Below it, ``P = P_sat*raw*(p/p_ref)^n``.
Raising ``p`` releases a cap (``n < 0``); lowering ``p`` drives a
subunit activity up through 1 and the pressure stops at ``P_sat``
instead of following the power law past it. Sanity: ``raw = 100``,
``n = -1/2`` gives ``p_uncap/p_ref = 1e4``. The same ``raw = 0.25``
enters the cap at ``p/p_ref = 1/16``. The O2 coefficient of the
reaction is ``nu_O2 = -n``, so the parent supplies
``dO = nO_gas - 2*n`` oxygen atoms per vapor molecule whether or not
the cap is binding. The Hertz-Knudsen molar flux is
``J = alpha*P/sqrt(2*pi*M*R*T)``, and the surface root is
``sum_i (nO_i - dO_i)*J_i = 0``. The factor ``1/sqrt(2*pi*R*T)`` is
common and cancels, leaving
``sum_i (nO_i - dO_i)*alpha_i*P_i/sqrt(M_i) = 0``. With backpressure
and gas resistance the publisher flux is
``J_i = k_i*max(0, P_i - P_bulk,i)`` where
``k_i = 1/(M_i*(R_interface,i + R_gas,i))`` and
``R_interface = 1/(alpha*k_HK)``. ``M_i`` is the flux publisher's
molar mass (``production_vapor_molar_mass_g_per_mol``: catalog
``molar_mass_g_mol``, else ``MOLAR_MASS``), not the formula-derived
CIAAW mass. Catalog Ca is 0.04008 kg/mol; CIAAW Ca is 0.040078 kg/mol.
The reported residual is the residual of the flux that mass emits.
``R_gas`` is 0 in free molecular
flow, so ``k_i`` collapses to ``alpha/sqrt(2*pi*M*R*T)`` and a zero
bulk recovers the vacuum balance. Unequal ``k_i`` does not cancel.

d-376 round 5. With a 10 mbar total pressure the fluxes pass through
the gas-film series resistance, the mandate's
``J = dP / (1/(alpha*k_HK) + R_gas(Kn) + R_melt)``. There the congruent
condition ``J_O2 = 1/2 J_Ca`` is set by the diffusive conductances, not
the effusion ``1/sqrt(M)`` weights. A different O/Ca conductance ratio
moves the surface pO2 down and Ca up; +8.4% is that, and ``p_total = 0``
recovering the free-molecular value is the right limit. The ratio used
is the publisher's 1 Pa probe at 2200 C, pipe 0.12 m, N2, alpha = 1,
``R_melt = 0``, and zero bulks, at the publisher molar masses (catalog
Ca 0.04008 kg/mol, not CIAAW 0.040078): ``k_O2/k_Ca = 1.424`` at 1000 Pa,
against the free-molecular ``sqrt(M_Ca/M_O2) = 1.119``. That is the move
from 5.3423 Pa to 5.7893 Pa.
The signed weight ``(nO - dO)`` equals ``2*n`` from the reaction, not
from the local slope. Off the cap the slope in ``log10(p)`` has the
sign of ``n^2`` and does not fold; on the cap the metal term is flat
and O2 still lifts the residual. The independent variable is
``log10(p/bar)``. The lower edge is
``log10(MELT_DISSOCIATION_PO2_MIN_BAR)`` with margin 0, and the
upper edge is 0.

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
published pressure. A positive exponent is not that case. AlO2
(``n = +1/4``), CrO2 (``n = +1/4``) and CrO3 (``n = +3/4``) have
weight ``2*n`` and sit in the residual. A failed root refuses them
with the metals. Atomic O(g) is not added. The ordinary catalog often
omits it, so this root sits slightly off a pure-oxide congruent solution
that carries O. Positive-exponent carriers that the provider dropped below
its reporting threshold are the same omission: they are not invented back
into the balance. Ones the provider did publish are.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.accounting.formulas import (
    SpeciesFormula,
    resolve_species_formula,
)

LANGMUIR_SURFACE_PO2_REASON = (
    "langmuir_surface_pO2_from_evaporative_oxygen_balance"
)
_RESIDUAL_CLOSE_MAX = 1.0e-4
_METAL_RAILS = frozenset({"gas_fugacity", "condensed_raoult_psat"})


@dataclass(frozen=True)
class LangmuirTransport:
    """Publisher knobs that set species conductance for one partial vector.

    ``overhead_pressure_Pa`` is the total pressure the flux provider
    passes into the series law (partial sum, floored by the commanded
    melt pressure). It is not a per-species backpressure. Species
    backpressures travel separately as partials. ``extrapolate_continuum``
    is the provider's transitional continuation: True only when that
    provider would keep the continuum film on instead of refusing.
    """

    temperature_K: float
    pipe_diameter_m: float
    gas_temperature_K: float
    carrier_gas: str
    axial_stir_factor: float
    radial_stir_factor: float
    overhead_pressure_Pa: float
    gas_resistance_enabled: bool
    hkl_upper_bound_species: frozenset[str]
    extrapolate_continuum: bool
    campaign_name: str | None = None


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
    # Antoine P_sat (Pa) when this condensed rail has a saturation cap.
    # None for gas-fugacity rails and for condensed rails with no raw
    # activity. The pressure law caps whenever activity would exceed 1,
    # not only when the publication was already capped.
    cap_psat_Pa: float | None = None


def _open_gate_obligation(carrier: _Carrier) -> bool:
    """Carrier an open gate must close or refuse.

    Positive pressure, nonzero reaction exponent, not a transport
    channel. A missing pO2 reference is still an obligation. It is
    not a reason to publish the persisted pressure (d-367). ``n == 0``
    and transport channels are not obligations. This is the set
    ``_assemble_open_gate_map`` enforces. ``_residual_participant``
    is the subset that can enter the sum.
    """

    exponent = carrier.reaction_exponent
    return (
        not carrier.transport_channel
        and carrier.pressure_Pa > 0.0
        and exponent is not None
        and exponent != 0.0
    )


def _gate_opener(carrier: _Carrier) -> bool:
    """Negative exponent. Only this sign can open the congruent solve.

    A missing reference does not disqualify the opener. The floor and
    the empty-buffer tests still run, and an open gate refuses the
    carrier instead of returning its persisted pressure.
    """

    exponent = carrier.reaction_exponent
    return (
        _open_gate_obligation(carrier)
        and exponent is not None
        and exponent < 0.0
    )


def _residual_participant(carrier: _Carrier) -> bool:
    """True when this carrier can enter ``sum 2*n*J``.

    d-376 amendment 1. ``n < 0`` (Ca, Al) and ``n > 0`` (AlO2, CrO2,
    CrO3) both move the residual. ``n == 0`` (CaO(g)) does not, and
    stays published. Transport channels are priced at the headspace
    potential and stay out. A missing reference cannot be rescaled
    onto the solved surface, so it is not a term in the sum. It is
    still an open-gate obligation: ``_assemble_open_gate_map`` omits
    it. Leaving an ``n > 0`` pressure published is the same unclosed
    value as leaving Ca. The gate still opens only on ``n < 0``.
    """

    exponent = carrier.reaction_exponent
    return (
        _open_gate_obligation(carrier)
        and exponent is not None
        and carrier.p_ref_bar is not None
    )


def _needs_surface_balance(carrier: _Carrier) -> bool:
    """True when this carrier can open the congruent solve and be rescaled.

    Opening the solve is a separate gate (physical floor or the
    reservoir no-buffer test). Only a negative exponent opens it.
    ``n > 0`` is still a residual participant once the gate is open.
    A missing reference does not open through this predicate; it
    opens through ``_gate_opener`` and is then refused. Transport
    channels do not open the solve and are not rewritten. A capped
    metal still participates. Its reaction exponent stays negative
    even though the published pressure is flat.
    """

    exponent = carrier.reaction_exponent
    return (
        _residual_participant(carrier)
        and exponent is not None
        and exponent < 0.0
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
    overhead_partials_Pa: Mapping[str, float] | None = None,
    transport: LangmuirTransport | None = None,
    conductance_mol_s_m2_Pa: Mapping[str, float] | None = None,
    allow_unmeasured_alpha_fallback: bool = False,
    unmeasured_alpha_fallback_species_allowlist: set[str] | None = None,
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
    The root already includes overhead backpressure and the publisher
    gas resistance, so the surface is not clamped to the overhead
    partial afterwards. While O2 is net-leaving that root is not below
    the overhead pO2. A failed root refuses every residual
    participant, below-band and in-band, ``n < 0`` and ``n > 0``,
    and names the reason. Transport channels and ``n == 0`` carriers
    stay published. Neither path writes a silent 0.
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
    # No negative-exponent melt carrier. A positive exponent does not
    # open the gate. A missing reference on an opener does not take
    # this branch: the floor and empty-buffer tests still run.
    if not any(_gate_opener(carrier) for carrier in carriers):
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
        return _refuse_unclosed_surface(
            pressures_Pa,
            carriers,
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
        overhead_partials_Pa=overhead_partials_Pa,
        transport=transport,
        conductance_mol_s_m2_Pa=conductance_mol_s_m2_Pa,
        capacity_negligible=capacity_negligible,
        eval_pO2_below_physical_floor=below_floor,
        allow_unmeasured_alpha_fallback=allow_unmeasured_alpha_fallback,
        unmeasured_alpha_fallback_species_allowlist=(
            unmeasured_alpha_fallback_species_allowlist
        ),
    )


def _certified_min_bar() -> float:
    from engines.builtin.vapor_pressure import (
        MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR,
    )

    return float(MELT_DISSOCIATION_PO2_MASS_ACTION_CERTIFIED_MIN_BAR)


def _nonnegative_partials(
    partials_Pa: Mapping[str, float] | None,
) -> dict[str, float]:
    """Species backpressures in Pa. Non-positive entries are not a bulk."""

    if not isinstance(partials_Pa, Mapping):
        return {}
    partials: dict[str, float] = {}
    for species, value in partials_Pa.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0.0:
            partials[str(species)] = number
    return partials


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


def _condensed_cap(
    provenance: Mapping[str, Any],
    pressure_Pa: float,
) -> tuple[bool, float | None, float | None]:
    """Return ``(already_capped, raw_activity, P_sat_Pa)``.

    ``P_pub = min(raw, 1) * P_sat``. An already-capped publication has
    ``raw > 1`` and ``P_pub = P_sat``. A subunit publication has
    ``raw < 1`` and ``P_pub = raw * P_sat``. Both use the same law: the
    cap binds whenever ``raw*(p/p_ref)^n >= 1``, which includes a solve
    that lowers ``p`` until a subunit activity crosses 1. Gas-fugacity
    rails are not capped. A condensed rail with no positive raw activity
    has no cap to apply.
    """

    if str(provenance.get("pressure_rail") or "") != "condensed_raoult_psat":
        return False, None, None
    activity = _positive(provenance.get("activity_factor"))
    raw = _positive(provenance.get("raw_metal_activity_root"))
    if raw is None and activity is not None and activity < 1.0 - 1.0e-12:
        # Uncapped publication: the activity factor is the raw root.
        raw = activity
    already = bool(
        activity is not None
        and raw is not None
        and activity >= 1.0 - 1.0e-12
        and raw > 1.0
    )
    if raw is None or pressure_Pa <= 0.0:
        return already, None, None
    p_sat = _positive(provenance.get("P_reference_Antoine_Pa"))
    if p_sat is None:
        # min(raw, 1) is dimensionless. Dividing Pa by it yields Pa.
        factor = min(raw, 1.0)
        if factor <= 0.0:
            return already, None, None
        p_sat = pressure_Pa / factor
    return already, raw, p_sat


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
    activity_capped, raw_activity, cap_psat_Pa = _condensed_cap(
        provenance,
        pressure,
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
        cap_psat_Pa,
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
            # formula. The conductance mass is the publisher's operand,
            # not this derived mass.
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
    *,
    allow_unmeasured_alpha_fallback: bool = False,
    unmeasured_alpha_fallback_species_allowlist: set[str] | None = None,
) -> tuple[float | None, str]:
    """Publisher alpha for the residual, or None when that publisher will not emit.

    ``publisher_effective_alpha`` is the kernel's order: species row,
    ``*`` wildcard, else the permitted fallback of 1. A broad-proxy row
    has already been stripped, so the fallback is what publishes Al2.
    A zero alpha emits no flux and stays out of the sum. The caller
    still rewrites that pressure onto the solved surface. An unmeasured
    species the fallback does not permit returns None. The caller
    refuses that carrier. The publisher's missing-alpha check is not
    that refusal: it fires only when the parent mass and the alpha-1
    baseline are both above their thresholds, and otherwise emits
    alpha 1 with no fallback flag.
    """

    from engines.builtin.evaporation_flux import publisher_effective_alpha

    try:
        decision = publisher_effective_alpha(
            species,
            temperature_K,
            alpha_by_species,
            allow_unmeasured_alpha_fallback=allow_unmeasured_alpha_fallback,
            unmeasured_alpha_fallback_species_allowlist=(
                unmeasured_alpha_fallback_species_allowlist
            ),
        )
    except (TypeError, ValueError):
        return None, ""
    if decision.alpha == 0.0:
        return 0.0, ""
    if not decision.policy_emits():
        return None, ""
    status = ""
    if isinstance(decision.evaluation, Mapping):
        status = str(decision.evaluation.get("authority_status") or "")
    if decision.unmeasured and decision.fallback_permitted:
        status = status or "unmeasured_alpha_fallback"
    source = status or "catalog evaporation_alpha"
    return float(decision.alpha), f"evaporation_flux_alpha:{source}"


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
    the pure-metal cap (``P_sat``, not the uncapped publication);
    the other branch is the mass-action continuation.
    """

    assert carrier.reaction_exponent is not None
    assert carrier.p_ref_bar is not None
    assert carrier.raw_activity is not None
    assert carrier.cap_psat_Pa is not None
    log_raw = math.log10(carrier.raw_activity) + (
        carrier.reaction_exponent
        * math.log10(surface_pO2_bar / carrier.p_ref_bar)
    )
    if log_raw >= 0.0:
        return carrier.cap_psat_Pa
    log_p = math.log10(carrier.cap_psat_Pa) + log_raw
    if not math.isfinite(log_p) or log_p > 300.0:
        raise ValueError("langmuir pressure law overflowed")
    if log_p < -300.0:
        return 0.0
    return 10.0 ** log_p


def _pressure_at_surface(carrier: _Carrier, surface_pO2_bar: float) -> float:
    """Melt-surface pressure law. Transport carriers never reach this."""

    if carrier.cap_psat_Pa is not None and carrier.raw_activity is not None:
        return _capped_pressure_Pa(carrier, surface_pO2_bar)
    assert carrier.reaction_exponent is not None
    assert carrier.p_ref_bar is not None
    return _scaled_pressure_Pa(
        carrier.pressure_Pa,
        carrier.reaction_exponent,
        carrier.p_ref_bar,
        surface_pO2_bar,
    )


def _ideal_molar_conductance(
    alpha: float,
    molar_mass_kg_per_mol: float,
    temperature_K: float,
) -> float:
    """``alpha * k_HK / M``, mol s^-1 m^-2 Pa^-1.

    ``k_HK`` is ``hertz_knudsen_k_kg_s_m2_pa``, the mass coefficient
    the flux publisher uses. Dividing by molar mass is the molar
    Hertz-Knudsen conductance ``alpha / sqrt(2*pi*M*R*T)``. A local
    sqrt would let this root drift from that publisher. Multiplying
    by a driving pressure in Pa yields mol m^-2 s^-1.
    """

    from simulator.chemistry.langmuir_knudsen import hertz_knudsen_k_kg_s_m2_pa

    return (
        alpha
        * hertz_knudsen_k_kg_s_m2_pa(temperature_K, molar_mass_kg_per_mol)
        / molar_mass_kg_per_mol
    )


def publisher_net_law(
    *,
    species: str,
    alpha: float,
    molar_mass_kg_per_mol: float,
    bulk_pressure_Pa: float,
    temperature_K: float,
    transport: LangmuirTransport | None,
) -> tuple[float, float]:
    """Return ``(k, bulk)`` for ``J = k * max(0, P_eq - bulk)``.

    ``k`` is mol s^-1 m^-2 Pa^-1 and ``bulk`` is Pa. With no transport
    context the law is ideal Hertz-Knudsen of the supplied backpressure
    (``R_gas = 0``). With a transport context the conductance is the
    evaporation publisher's and the bulk is ``species_drive_bulk_Pa``
    with the surface root owning the drive: overhead O2 on every
    branch, and every other species only when the film is on and the
    carrier is not an HKL upper bound. A disabled film or an HKL upper
    bound is ideal Hertz-Knudsen (``R_gas = 0``), not a zero O2 bulk.
    The gate-closed publisher does not use this law; it passes
    ``surface_oxygen_bound`` false and O2 follows the film rule.
    Every other carrier uses
    ``_series_resistance_evaporation_flux_kg_m2_s``. The 1 Pa probe is
    valid because that flux is linear in ``P_eq - P_bulk`` (``R_gas``
    does not depend on the driving pressure). Sanity: free-molecular
    flow with no O2 partial returns the ideal conductance and a zero
    bulk, which is the vacuum root.
    """

    if (
        not math.isfinite(alpha)
        or alpha <= 0.0
        or not math.isfinite(molar_mass_kg_per_mol)
        or molar_mass_kg_per_mol <= 0.0
    ):
        return 0.0, 0.0
    bulk = (
        bulk_pressure_Pa
        if math.isfinite(bulk_pressure_Pa) and bulk_pressure_Pa > 0.0
        else 0.0
    )
    if transport is None:
        if not math.isfinite(temperature_K) or temperature_K <= 0.0:
            raise ValueError("publisher net flux requires a positive temperature")
        return (
            _ideal_molar_conductance(
                alpha, molar_mass_kg_per_mol, temperature_K
            ),
            bulk,
        )
    return _publisher_net_law_with_transport(
        species=species,
        alpha=alpha,
        molar_mass_kg_per_mol=molar_mass_kg_per_mol,
        bulk=bulk,
        transport=transport,
    )


def _publisher_net_law_with_transport(
    *,
    species: str,
    alpha: float,
    molar_mass_kg_per_mol: float,
    bulk: float,
    transport: LangmuirTransport,
) -> tuple[float, float]:
    from engines.builtin.evaporation_flux import (
        EvaporationFluxConfigurationError,
        _series_resistance_evaporation_flux_kg_m2_s,
        species_drive_bulk_Pa,
    )

    ideal = _ideal_molar_conductance(
        alpha, molar_mass_kg_per_mol, transport.temperature_K
    )
    hkl_bound = species in transport.hkl_upper_bound_species
    # d-376 amendment 1. Disabled film and an HKL upper bound are ideal
    # Hertz-Knudsen, ``R_gas = 0``. Overhead O2 stays the backpressure,
    # so the O2 drive is zero at and below that partial and the root
    # cannot sit under it while O2 leaves. Other species keep the
    # free-evaporation bound (bulk 0 here). Free-molecular
    # ``k_O2/k_Ca = sqrt(M_Ca/M_O2) = 1.119``; the 10 mbar film ratio
    # is 1.424. No O2 partial recovers the vacuum root either way.
    drive_bulk = species_drive_bulk_Pa(
        species,
        bulk,
        gas_resistance_enabled=transport.gas_resistance_enabled,
        hkl_upper_bound=hkl_bound,
        surface_oxygen_bound=True,
    )
    if not transport.gas_resistance_enabled or hkl_bound:
        return ideal, drive_bulk

    try:
        probed = _series_resistance_evaporation_flux_kg_m2_s(
            species=species,
            P_eq_pa=drive_bulk + 1.0,
            P_bulk_pa=drive_bulk,
            T_surface_K=transport.temperature_K,
            molar_mass_kg_mol=molar_mass_kg_per_mol,
            alpha_i=alpha,
            pipe_diameter_m=transport.pipe_diameter_m,
            overhead_pressure_pa=transport.overhead_pressure_Pa,
            axial_stir_factor=transport.axial_stir_factor,
            radial_stir_factor=transport.radial_stir_factor,
            carrier_gas=transport.carrier_gas,
            T_gas_K=transport.gas_temperature_K,
            gas_resistance_enabled=True,
            extrapolate_continuum=transport.extrapolate_continuum,
            melt_resistance_enabled=False,
        )
    except EvaporationFluxConfigurationError as exc:
        raise ValueError(
            f"publisher net flux unavailable for {species}: {exc}"
        ) from exc
    flux_kg_s_m2 = float(probed.flux_kg_s_m2)
    if not math.isfinite(flux_kg_s_m2) or flux_kg_s_m2 < 0.0:
        raise ValueError(f"publisher net flux invalid for {species}")
    if flux_kg_s_m2 == 0.0:
        return 0.0, drive_bulk
    # J_mol = flux_kg / M. The probe drive is 1 Pa, so k = J_mol / 1 Pa.
    conductance = flux_kg_s_m2 / molar_mass_kg_per_mol
    if not math.isfinite(conductance) or conductance < 0.0:
        raise ValueError(f"publisher conductance invalid for {species}")
    return conductance, drive_bulk


def _surface_balance_log10_bracket() -> tuple[float, float]:
    """log10(p/bar) domain shared with the melt-dissociation floor.

    Margin is 0. The lower edge is ``log10`` of
    ``MELT_DISSOCIATION_PO2_MIN_BAR``, read from that module at call
    time. ``_pO2_is_melt_dissociation_floor`` is true for
    ``pO2 <=`` that same bar constant, and brentq includes this
    endpoint, so the root search starts on the floor the gate owns.
    No extra decade is subtracted, and the search does not stop short
    of the floor. The upper edge stays 0, which is ``log10(1 bar)``.
    ``MELT_DISSOCIATION_PO2_MAX_BAR`` (100 bar) is the mass-action
    envelope cap, not this vacuum root's domain.
    """

    import simulator.physical_constants as physical_constants

    return math.log10(physical_constants.MELT_DISSOCIATION_PO2_MIN_BAR), 0.0


def _solve_net_surface_balance(
    coupled: list[_Carrier],
    channels: list[Any],
    *,
    law: Mapping[str, tuple[float, float]],
) -> Any:
    """Root of the publisher net-flux balance, cap included.

    ``law[species]`` is ``(k, bulk)`` with ``J = k*max(0, P - bulk)``
    in mol m^-2 s^-1. ``k`` already contains alpha and gas resistance.
    The vacuum power-law exponent check does not apply: a condensed cap
    is flat and then a power law, and a backpressure clip is not a pure
    power of pO2. O2's drive still rises with pO2 and a metal's drive
    does not, so the residual does not fold across the cap. The
    returned residual is this root's residual.

    d-376 amendment 1. The residual is ``sum (nO_i - dO_i) J_i``. For
    congruent CaO, Ca has ``nO = 0`` and ``n = -1/2``, so ``dO = 1``
    and the weight is ``-1``. O2 has ``nO = 2`` and ``n = +1``, so
    ``dO = 0`` and the weight is ``+2``. One parent oxygen leaves with
    each Ca, as half an O2, and the root is ``J_Ca = 2 J_O2``. While
    that metal demand is positive, O2 must be net-leaving. O2's drive
    is ``max(0, P_O2 - P_bulk)``, which is zero at and below the
    overhead partial, so the root sits strictly above that partial.
    Clamping the surface onto the partial zeroes O2 and leaves the
    metal flux unbalanced.
    """

    from scipy.optimize import brentq

    from simulator.vacuum_oxygen_balance import (
        OxygenBalanceBracketBoundary,
        _VacuumOxygenBalance,
    )
    from simulator.physical_constants import PA_PER_BAR

    by_species = {carrier.species: carrier for carrier in coupled}
    low, high = _surface_balance_log10_bracket()

    def parts(
        logp: float,
    ) -> tuple[float, dict[str, float], float, float, dict[str, float]]:
        surface = 10.0 ** logp
        oxygen = 0.0
        parent = 0.0
        pressures: dict[str, float] = {}
        fluxes: dict[str, float] = {}
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
            conductance, bulk = law[channel.species]
            drive = pressure - bulk
            if drive < 0.0:
                drive = 0.0
            flux = conductance * drive
            if not math.isfinite(flux) or flux < 0.0:
                raise ValueError(
                    f"net flux invalid for {channel.species!r}"
                )
            pressures[channel.species] = pressure
            fluxes[channel.species] = flux
            oxygen += channel.oxygen_atoms * flux
            parent += channel.parent_oxygen_demand * flux
        return oxygen - parent, pressures, oxygen, parent, fluxes

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
        # 1e-14, not 1e-12. A clipped drive (metal bulk above P_eq,
        # O2 just above its overhead) has gross flux ~1e-13 mol m^-2
        # s^-1, and the O2 kink is steep. xtol 1e-12 stops while
        # |O-D|/max(O,D) is still ~1e-2, above _RESIDUAL_CLOSE_MAX,
        # so a closed overhead root is refused. n == 0 is not in this
        # sum and does not inflate that scale. 1e-14 puts the root
        # under the check; float precision stops near 1e-5 relative.
        root = float(brentq(
            lambda value: parts(value)[0],
            low,
            high,
            xtol=1.0e-14,
            rtol=1.0e-14,
            maxiter=200,
        ))
        bracket = None

    residual, pressures, oxygen, parent, fluxes = parts(root)
    relative = abs(residual) / max(oxygen, parent, 1.0e-300)
    return _VacuumOxygenBalance(
        pO2_bar=10.0 ** root,
        bracket_boundary=bracket,
        pressures_Pa=pressures,
        channel_fluxes_mol_m2_s=fluxes,
        oxygen_atom_flux_mol_m2_s=oxygen,
        parent_oxygen_demand_flux_mol_m2_s=parent,
        relative_residual=relative,
        alpha_sources={
            channel.species: channel.alpha_source for channel in channels
        },
    )


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
    overhead_partials_Pa: Mapping[str, float] | None = None,
    transport: LangmuirTransport | None = None,
    conductance_mol_s_m2_Pa: Mapping[str, float] | None = None,
    capacity_negligible: bool = False,
    eval_pO2_below_physical_floor: bool = False,
    allow_unmeasured_alpha_fallback: bool = False,
    unmeasured_alpha_fallback_species_allowlist: set[str] | None = None,
) -> LangmuirSurfaceOxygen:
    registry = _formula_registry(rows)
    from simulator.evaporation import (
        _load_evaporation_alpha_by_species,
        _measured_alpha_control_view,
    )

    alphas = _measured_alpha_control_view(
        _load_evaporation_alpha_by_species(dict(legacy_catalog or {}))
    )
    from simulator.vacuum_oxygen_balance import (
        _OXYGEN_GAS_ALPHA,
        _OXYGEN_GAS_ALPHA_SOURCE,
        _VacuumOxygenChannel,
    )
    from simulator.physical_constants import PA_PER_BAR

    refused: dict[str, str] = {}
    channels: list[Any] = []
    coupled: list[_Carrier] = []
    # Measured alpha of 0. Not a residual term (J = 0). Still an n != 0
    # pressure, rewritten onto the solved surface after the root.
    zero_alpha: list[_Carrier] = []
    for carrier in carriers:
        # Transport and n == 0 stay at the published pressure. They are
        # not in this residual. Every other carrier is a term in the
        # sum or a typed refusal. Sign does not pick a third path.
        if carrier.transport_channel or not (
            carrier.reaction_exponent not in (None, 0.0)
            and carrier.pressure_Pa > 0.0
        ):
            continue
        if carrier.p_ref_bar is None:
            refused[carrier.species] = (
                "missing pO2 reference; surface pressure omitted"
            )
            continue
        formula = _species_formula(carrier.species, registry)
        if formula is None:
            refused[carrier.species] = (
                "formula unresolved; surface pressure omitted"
            )
            continue
        alpha, alpha_source = _flux_alpha(
            carrier.species,
            alphas,
            temperature_K,
            allow_unmeasured_alpha_fallback=allow_unmeasured_alpha_fallback,
            unmeasured_alpha_fallback_species_allowlist=(
                unmeasured_alpha_fallback_species_allowlist
            ),
        )
        if alpha is None:
            # Not in the sum: there is no emitting coefficient to weight.
            # Do not keep the persisted pressure. The flux publisher
            # refuses an unmeasured alpha only when the parent mass is
            # above 1e-12 kg AND the alpha-1 baseline exceeds the
            # nontrivial-flux threshold. Below either threshold, and
            # whenever the viscous-domain diagnostic is unset (vacuum
            # overhead), it emits that alpha of 1 with no fallback flag.
            # A subthreshold or dust-parent carrier would then reach
            # wall-supply accounting at this unclosed pO2.
            refused[carrier.species] = (
                "evaporation alpha does not enter the balance; "
                "surface pressure omitted"
            )
            continue
        if alpha == 0.0:
            zero_alpha.append(carrier)
            continue
        oxygen_atoms = float(formula.elements.get("O", 0.0))
        # Reaction exponent, never the capped local slope. A pure-metal
        # cap still releases the parent oxide's oxygen.
        demand = _parent_oxygen_demand(
            oxygen_atoms, carrier.reaction_exponent
        )
        if demand < 0.0:
            refused[carrier.species] = (
                "oxygen demand is negative; surface pressure omitted"
            )
            continue
        molar_mass = _publisher_molar_mass_kg_per_mol(
            legacy_catalog, carrier.species
        )
        if molar_mass is None:
            refused[carrier.species] = (
                "publisher molar mass unavailable; surface pressure omitted"
            )
            continue
        channels.append(
            _VacuumOxygenChannel(
                species=carrier.species,
                molar_mass_kg_per_mol=molar_mass,
                oxygen_atoms=oxygen_atoms,
                parent_oxygen_demand=demand,
                pO2_exponent=carrier.reaction_exponent,
                alpha=alpha,
                alpha_source=alpha_source,
            )
        )
        coupled.append(carrier)

    if not any(channel.pO2_exponent < 0.0 for channel in channels):
        return _refuse_unclosed_surface(
            pressures_Pa,
            carriers,
            persisted,
            detail="no oxygen-coupled carrier could enter the balance",
            extra_refused=refused,
            capacity_negligible=capacity_negligible,
            eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
        )

    o2_mass = _publisher_molar_mass_kg_per_mol(legacy_catalog, "O2")
    if all(channel.species != "O2" for channel in channels):
        if o2_mass is None:
            return _refuse_unclosed_surface(
                pressures_Pa,
                carriers,
                persisted,
                detail="publisher molar mass unavailable for O2",
                extra_refused=refused,
                capacity_negligible=capacity_negligible,
                eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
            )
        channels.append(
            _VacuumOxygenChannel(
                species="O2",
                molar_mass_kg_per_mol=o2_mass,
                oxygen_atoms=2.0,
                parent_oxygen_demand=0.0,
                pO2_exponent=1.0,
                alpha=_OXYGEN_GAS_ALPHA,
                alpha_source=_OXYGEN_GAS_ALPHA_SOURCE,
            )
        )

    partials = _nonnegative_partials(overhead_partials_Pa)
    if "O2" not in partials:
        overhead_bar = _positive(overhead_pO2_bar)
        if overhead_bar is not None:
            partials["O2"] = overhead_bar * PA_PER_BAR
    override = {
        str(species): float(value)
        for species, value in dict(conductance_mol_s_m2_Pa or {}).items()
    }
    try:
        law: dict[str, tuple[float, float]] = {}
        for channel in channels:
            bulk = partials.get(channel.species, 0.0)
            if channel.species in override:
                conductance = override[channel.species]
                if not math.isfinite(conductance) or conductance < 0.0:
                    raise ValueError(
                        f"conductance override invalid for {channel.species!r}"
                    )
                law[channel.species] = (conductance, bulk)
                continue
            law[channel.species] = publisher_net_law(
                species=channel.species,
                alpha=channel.alpha,
                molar_mass_kg_per_mol=channel.molar_mass_kg_per_mol,
                bulk_pressure_Pa=bulk,
                temperature_K=temperature_K,
                transport=transport,
            )
        balance = _solve_net_surface_balance(
            coupled,
            channels,
            law=law,
        )
    except ValueError as exc:
        return _refuse_unclosed_surface(
            pressures_Pa,
            carriers,
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
        return _refuse_unclosed_surface(
            pressures_Pa,
            carriers,
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

    surface_pO2_bar = float(balance.pO2_bar)
    # The caller's overhead partial, not the bulk a transport branch
    # stored in the law. A disabled film used to zero that bulk and
    # this check then missed a vacuum root under the real overhead.
    physical_o2_Pa = partials.get("O2", 0.0)
    o2_flux = float(balance.channel_fluxes_mol_m2_s.get("O2", 0.0) or 0.0)
    # O2 drive is zero at and below the overhead partial. A positive O2
    # flux therefore means the solved surface is above that partial.
    overhead_bounded = physical_o2_Pa > 0.0 and o2_flux > 0.0
    if overhead_bounded and surface_pO2_bar * PA_PER_BAR < physical_o2_Pa:
        return _refuse_unclosed_surface(
            pressures_Pa,
            carriers,
            persisted,
            detail=(
                "solved surface pO2 is below the overhead partial "
                "while O2 is net-leaving"
            ),
            extra_refused=refused,
            capacity_negligible=capacity_negligible,
            eval_pO2_below_physical_floor=eval_pO2_below_physical_floor,
        )
    solved_Pa: dict[str, float] = {}
    for carrier in coupled:
        if carrier.species in refused:
            continue
        solved = balance.pressures_Pa.get(carrier.species)
        if solved is None:
            continue
        solved_Pa[carrier.species] = float(solved)
    for carrier in zero_alpha:
        if carrier.species in refused:
            continue
        try:
            solved_Pa[carrier.species] = _pressure_at_surface(
                carrier, surface_pO2_bar
            )
        except ValueError as exc:
            refused[carrier.species] = (
                "surface pressure omitted: " + str(exc)
            )
    adjusted = _assemble_open_gate_map(
        pressures_Pa,
        carriers,
        solved_Pa=solved_Pa,
        refused=refused,
        detail="open-gate carrier was not on the solved surface",
    )
    warning = (
        f"{LANGMUIR_SURFACE_PO2_REASON}: "
        f"surface_pO2_bar={surface_pO2_bar:.6e} "
        f"relative_residual={residual:.3e}"
    )
    if overhead_bounded:
        warning += f" overhead_pO2_bar={physical_o2_Pa / PA_PER_BAR:.6e}"
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


def _publisher_molar_mass_kg_per_mol(
    catalog: Mapping[str, Any] | None,
    species: str,
) -> float | None:
    """Publisher molar mass in kg/mol, or None when that publisher will not emit.

    Same operand as ``_build_evaporation_aux_maps``. A non-positive or
    non-numeric catalog entry is not a mass the flux can use.
    """

    from simulator.evaporation import production_vapor_molar_mass_g_per_mol

    raw = production_vapor_molar_mass_g_per_mol(catalog, species)
    try:
        mass_g = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(mass_g) or mass_g <= 0.0:
        return None
    return mass_g / 1000.0


def _assemble_open_gate_map(
    pressures_Pa: Mapping[str, float],
    carriers: list[_Carrier],
    *,
    solved_Pa: Mapping[str, float],
    refused: dict[str, str],
    detail: str,
) -> dict[str, float]:
    """Pressure map once the d-376 gate is open.

    Every obligation (positive pressure, ``n != 0``, not transport) is
    a solved pressure or a typed refusal. It is never the persisted
    unclosed value. A missing pO2 reference, a missing formula, an
    unmeasured alpha, and a failed root all land here. Transport
    channels and ``n == 0`` keep the published pressure unless a
    caller already named them in ``refused``.
    """

    obligation = {
        carrier.species
        for carrier in carriers
        if _open_gate_obligation(carrier)
    }
    adjusted: dict[str, float] = {}
    for species, pressure in pressures_Pa.items():
        if species in refused:
            continue
        if species not in obligation:
            adjusted[species] = pressure
            continue
        solved = solved_Pa.get(species)
        if solved is None:
            refused.setdefault(
                species,
                "langmuir surface pO2 unavailable; "
                "unclosed surface pressure omitted: " + detail,
            )
            continue
        adjusted[species] = float(solved)
    for species in obligation:
        if species in pressures_Pa or species in refused:
            continue
        refused[species] = (
            "langmuir surface pO2 unavailable; "
            "unclosed surface pressure omitted: " + detail
        )
    return adjusted


def _refuse_unclosed_surface(
    pressures_Pa: Mapping[str, float],
    carriers: list[_Carrier],
    persisted: Mapping[str, float],
    *,
    detail: str,
    extra_refused: Mapping[str, str] | None = None,
    capacity_negligible: bool = False,
    eval_pO2_below_physical_floor: bool = False,
) -> LangmuirSurfaceOxygen:
    """Refuse every melt-surface carrier when the required solve fails.

    d-376 amendment 1 has already opened the gate (physical floor or
    ``C_m ~ 0``). The persisted evaluation pressure is not a closed
    surface balance until the root exists. Below-band carriers
    (reference pO2 under the certified minimum) and in-band carriers
    are that same defect. Pure CaO at 1e-9 bar is in-band, so the
    below-band set is empty, and a 1 bar overhead O2 makes the O2
    drive identically zero on the log-p bracket ``[-30, 0]`` while Ca
    demand stays positive. The bracket then has no root. Refusing only
    the below-band set would return the unclosed Ca pressure.
    Transport channels and neutral carriers (``n == 0``) stay published.
    ``n > 0`` is not neutral: its oxygen weight is ``2*n``. Nothing is
    written as 0.
    """

    refused: dict[str, str] = {}
    if extra_refused:
        refused.update(dict(extra_refused))
    # One map for every open-gate return. Obligations without a more
    # specific reason, including a missing pO2 reference that never
    # entered the residual, get the failure detail. They are not left
    # at the persisted pressure.
    adjusted = _assemble_open_gate_map(
        pressures_Pa,
        carriers,
        solved_Pa={},
        refused=refused,
        detail=detail,
    )
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
