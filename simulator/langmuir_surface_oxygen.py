"""Self-consistent surface pO2 for free Langmuir evaporation.

The vapor-pressure provider keeps the mass-action metal pressure at the
commanded melt fO2, including the b-491 floor inversion. Free evaporation
into vacuum is a different boundary. This module replaces the flux
pressure map with one surface pO2 and names the warning. Provider
equilibrium pressures stay on the mass-action law.

Premise. Free (Langmuir) evaporation does not impose a headspace pO2. The
surface oxygen potential is the value at which the oxygen atoms leaving in
the vapor fluxes equal the oxygen atoms removed from the parent oxides.
The persisted melt fO2 is the mass-action state of the melt, not that
surface boundary. That includes an ordinary in-band value such as 1e-9 bar.
Below the certified band, ``p_M = K*a/sqrt(pO2)`` is an uncertified
extrapolation; in band it is the certified melt state. Neither is the
free-surface boundary, so both flux maps are re-evaluated at the solved
surface pO2. A failed root drops only below-band carriers. An in-band
failure keeps the published pressures and names the warning.

Algebra. A carrier published at ``P_pub`` (Pa) and reference ``p_ref``
(bar), with pressure scaling as ``pO2^n``, is ``P(p) = P_pub*(p/p_ref)^n``
at surface ``p`` (bar). The O2 coefficient of that equilibrium is
``nu_O2 = -n``, so the parent supplies ``dO = nO_gas - 2*n`` oxygen atoms
per vapor molecule: the atoms still in the molecule, plus the atoms that
leave as O2. The Hertz-Knudsen molar flux is
``J = alpha*P/sqrt(2*pi*M*R*T)``, and the surface root is
``sum_i (nO_i - dO_i)*J_i = 0``. The factor ``1/sqrt(2*pi*R*T)`` is common
and cancels, leaving
``sum_i (nO_i - dO_i)*alpha_i*P_i/sqrt(M_i) = 0``. The signed weight
``(nO - dO)`` equals ``2*n``, so the slope in ``log10(p)`` has the sign of
``n^2`` and does not fold. The independent variable is ``log10(p/bar)``
on ``(-30, 0]``.

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
        }


@dataclass(frozen=True)
class _Carrier:
    species: str
    pressure_Pa: float
    exponent: float | None
    p_ref_bar: float | None
    inverted: bool


def _needs_surface_balance(carrier: _Carrier) -> bool:
    """True when this carrier's pressure was published at a melt fO2.

    The exponent and the reference pO2 are what the mass-action law used.
    The certified-band floor is not the gate: 1e-9 bar is still a melt
    state, and the free-surface root is a different number.
    """

    return (
        carrier.exponent is not None
        and carrier.exponent < 0.0
        and carrier.p_ref_bar is not None
        and carrier.pressure_Pa > 0.0
    )


def apply_langmuir_surface_oxygen(
    pressures_Pa: Mapping[str, float],
    *,
    temperature_K: float,
    provenance_by_species: Mapping[str, Mapping[str, Any]] | None = None,
    source_reaction_fO2_bar: float | None = None,
    transport_pO2_bar: float | None = None,
    legacy_catalog: Mapping[str, Any] | None = None,
    compiled_catalog: Any | None = None,
) -> LangmuirSurfaceOxygen:
    """Return flux pressures at one self-consistent surface pO2.

    The caller owns ``pressures_Pa``. This function does not mutate it.
    A successful root copies the map and rescales every included
    oxygen-coupled carrier, in band and below it. A failed root, or a
    lower-bracket pin that is not a root, drops only the below-band
    carriers and keeps in-band pressures with a warning. Neither path
    writes a silent 0.
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
        )

    return _solve_or_refuse(
        pressures_Pa,
        carriers=carriers,
        inverted=inverted,
        persisted=persisted,
        temperature_K=temperature,
        rows=legacy,
        legacy_catalog=legacy_catalog,
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


def _exponent(
    species: str,
    provenance: Mapping[str, Any],
    *,
    evaluator: Any | None,
    row: Mapping[str, Any] | None,
) -> float | None:
    if _condensed_activity_capped(provenance):
        # Capped activity no longer moves with pO2. Treating it as n<0
        # would scale a pure-metal P_sat that the rail has already frozen.
        return 0.0
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
        return _Carrier(species, 0.0, None, None, False)
    evaluator = _evaluator(compiled_catalog, species)
    exponent = _exponent(
        species,
        provenance,
        evaluator=evaluator,
        row=row if isinstance(row, Mapping) else None,
    )
    p_ref = _reference_pO2_bar(
        species,
        provenance,
        evaluator=evaluator,
        source_reaction_fO2_bar=source_reaction_fO2_bar,
        transport_pO2_bar=transport_pO2_bar,
    )
    inverted = bool(
        exponent is not None
        and exponent < 0.0
        and p_ref is not None
        and p_ref < _certified_min_bar()
    )
    return _Carrier(species, pressure, exponent, p_ref, inverted)


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


def _solve_or_refuse(
    pressures_Pa: Mapping[str, float],
    *,
    carriers: list[_Carrier],
    inverted: list[_Carrier],
    persisted: Mapping[str, float],
    temperature_K: float,
    rows: Mapping[str, Any],
    legacy_catalog: Mapping[str, Any] | None,
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
        if carrier.exponent is None or carrier.p_ref_bar is None:
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
        demand = _parent_oxygen_demand(oxygen_atoms, carrier.exponent)
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
                pO2_exponent=carrier.exponent,
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
            assert carrier.exponent is not None
            assert carrier.p_ref_bar is not None
            modeled[carrier.species] = _scaled_pressure_Pa(
                carrier.pressure_Pa,
                carrier.exponent,
                carrier.p_ref_bar,
                surface,
            )
        if "O2" not in laws:
            from simulator.physical_constants import PA_PER_BAR

            modeled["O2"] = surface * PA_PER_BAR
        return modeled

    try:
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
        )

    adjusted = dict(pressures_Pa)
    for species in refused:
        adjusted.pop(species, None)
    for carrier in coupled:
        if carrier.species in refused or carrier.exponent is None:
            continue
        solved = balance.pressures_Pa.get(carrier.species)
        if solved is None:
            continue
        adjusted[carrier.species] = float(solved)
    warning = (
        f"{LANGMUIR_SURFACE_PO2_REASON}: "
        f"surface_pO2_bar={balance.pO2_bar:.6e} "
        f"relative_residual={residual:.3e}"
    )
    if refused:
        warning += " refused=" + ",".join(sorted(refused))
    return LangmuirSurfaceOxygen(
        pressures_Pa=adjusted,
        applied=True,
        surface_pO2_bar=float(balance.pO2_bar),
        relative_residual=residual,
        warning=warning,
        refused_species=dict(refused),
        persisted_pO2_bar_by_species=dict(persisted),
    )


def _refuse_inverted(
    pressures_Pa: Mapping[str, float],
    inverted: list[_Carrier],
    persisted: Mapping[str, float],
    *,
    detail: str,
    extra_refused: Mapping[str, str] | None = None,
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
    )
