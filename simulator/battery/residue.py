"""Mol-native free-evaporation inventory integration for residue predictions."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
from simulator.accounting.formulas import parse_formula
from simulator.battery.oxygen_balance import (
    _VacuumOxygenChannel,
    _solve_vacuum_oxygen_balance,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView


_SECONDS_PER_HOUR = 3_600.0
_OXYGEN_CHANNELS = frozenset({"O", "O2"})


class ResidueInventoryRefusal(ValueError):
    """Typed absence or invalid input at the residue integration boundary."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


@dataclass(frozen=True)
class ResidueChannel:
    """Runtime evaporation channel data needed by oxygen and flux kernels."""

    species: str
    formula: str
    parent_oxide: str | None
    alpha: float
    alpha_source: str


@dataclass(frozen=True)
class ResidueInventoryResult:
    residue_mol: Mapping[str, float]
    evaporated_mol: Mapping[str, float]
    pO2_bar_by_step: tuple[float | None, ...]
    atom_closure_mol: Mapping[str, float]


@dataclass(frozen=True)
class _ChannelTerms:
    source: ResidueChannel
    molar_mass_kg_mol: float
    atom_counts: Mapping[str, float]
    parent_moles_per_product: float
    parent_oxygen_demand: float
    oxide_per_product_kg: float
    o2_per_product_kg: float


def _formula_terms(formula_text: str) -> tuple[dict[str, float], float]:
    try:
        formula = parse_formula(formula_text)
        atoms = {str(element): float(count) for element, count in formula.elements.items()}
        molar_mass = float(formula.molar_mass_kg_per_mol())
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ResidueInventoryRefusal(
            "residue_formula_invalid", f"invalid formula {formula_text!r}: {exc}"
        ) from exc
    if (
        not atoms
        or not math.isfinite(molar_mass)
        or molar_mass <= 0.0
        or any(not math.isfinite(value) or value <= 0.0 for value in atoms.values())
    ):
        raise ResidueInventoryRefusal(
            "residue_formula_invalid", f"formula {formula_text!r} is not finite and positive"
        )
    return atoms, molar_mass


def _channel_terms(channels: Sequence[ResidueChannel]) -> tuple[_ChannelTerms, ...]:
    terms: list[_ChannelTerms] = []
    seen: set[str] = set()
    for channel in channels:
        if not channel.species or channel.species in seen:
            raise ResidueInventoryRefusal(
                "residue_channel_invalid", f"missing or duplicate channel {channel.species!r}"
            )
        seen.add(channel.species)
        if (
            not math.isfinite(float(channel.alpha))
            or float(channel.alpha) < 0.0
            or float(channel.alpha) > 1.0
        ):
            raise ResidueInventoryRefusal(
                "residue_alpha_invalid", f"alpha for {channel.species!r} must be in [0, 1]"
            )
        if not isinstance(channel.alpha_source, str) or not channel.alpha_source.strip():
            raise ResidueInventoryRefusal(
                "residue_alpha_source_missing", f"alpha source missing for {channel.species!r}"
            )
        atom_counts, molar_mass = _formula_terms(channel.formula)
        parent_moles_per_product = 0.0
        parent_oxygen_demand = 0.0
        oxide_per_product_kg = 0.0
        o2_per_product_kg = 0.0
        if channel.parent_oxide:
            parent_atoms, parent_molar_mass = _formula_terms(channel.parent_oxide)
            gas_metals = {
                element: count
                for element, count in atom_counts.items()
                if element != "O"
            }
            parent_metals = {
                element: count
                for element, count in parent_atoms.items()
                if element != "O"
            }
            if not gas_metals or set(gas_metals) != set(parent_metals):
                raise ResidueInventoryRefusal(
                    "residue_stoichiometry_invalid",
                    f"{channel.species!r} and parent {channel.parent_oxide!r} do not share the same non-oxygen elements",
                )
            ratios = [gas_metals[element] / parent_metals[element] for element in gas_metals]
            parent_moles_per_product = ratios[0]
            if any(
                not math.isclose(ratio, parent_moles_per_product, rel_tol=1.0e-12, abs_tol=1.0e-14)
                for ratio in ratios[1:]
            ):
                raise ResidueInventoryRefusal(
                    "residue_stoichiometry_invalid",
                    f"{channel.species!r} does not preserve parent non-oxygen atom ratios",
                )
            parent_oxygen_demand = parent_moles_per_product * parent_atoms.get("O", 0.0)
            oxide_per_product_kg = parent_moles_per_product * parent_molar_mass / molar_mass
            oxygen_difference = parent_oxygen_demand - atom_counts.get("O", 0.0)
            _oxygen_atoms, oxygen_molar_mass = _formula_terms("O2")
            o2_per_product_kg = (
                oxygen_difference * 0.5 * oxygen_molar_mass / molar_mass
            )
            if not math.isclose(
                oxide_per_product_kg,
                1.0 + o2_per_product_kg,
                rel_tol=1.0e-6,
                abs_tol=1.0e-9,
            ):
                raise ResidueInventoryRefusal(
                    "residue_stoichiometry_invalid",
                    f"{channel.species!r} mass and atom stoichiometry disagree",
                )
        elif channel.species not in _OXYGEN_CHANNELS:
            raise ResidueInventoryRefusal(
                "residue_parent_missing",
                f"evaporation channel {channel.species!r} has no parent oxide",
            )
        terms.append(
            _ChannelTerms(
                source=channel,
                molar_mass_kg_mol=molar_mass,
                atom_counts=atom_counts,
                parent_moles_per_product=parent_moles_per_product,
                parent_oxygen_demand=parent_oxygen_demand,
                oxide_per_product_kg=oxide_per_product_kg,
                o2_per_product_kg=o2_per_product_kg,
            )
        )
    if not terms:
        raise ResidueInventoryRefusal("residue_channels_missing")
    if not _OXYGEN_CHANNELS.intersection(seen):
        raise ResidueInventoryRefusal(
            "residue_oxygen_channels_missing", "vacuum closure requires an O or O2 channel"
        )
    return tuple(terms)


def _atom_totals(
    residue_mol: Mapping[str, float], evaporated_mol: Mapping[str, float]
) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for species, amount in (*residue_mol.items(), *evaporated_mol.items()):
        atoms, _molar_mass = _formula_terms(species)
        for element, count in atoms.items():
            totals[element] += float(amount) * count
    return dict(totals)


def _finite_step_fluxes(
    terms: Sequence[_ChannelTerms],
    inventory_mol: Mapping[str, float],
    *,
    temperature_K: float,
    area_m2: float,
    pressures_Pa: Mapping[str, float],
) -> dict[str, float]:
    """Run the authoritative HKL provider at the solved channel pressures."""
    parent_channels = [term for term in terms if term.parent_moles_per_product > 0.0]
    if not parent_channels:
        return {}
    provider = BuiltinEvaporationFluxProvider()
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(inventory_mol)},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=0.0,
        control_inputs={
            "vapour_batch_flux_pressures_Pa": {
                term.source.species: float(pressures_Pa.get(term.source.species, 0.0))
                for term in parent_channels
            },
            "overhead_partials_Pa": {
                term.source.species: 0.0 for term in parent_channels
            },
            "molar_mass_kg_mol": {
                term.source.species: term.molar_mass_kg_mol for term in parent_channels
            },
            "stoich_by_species": {
                term.source.species: {
                    "parent_oxide": term.source.parent_oxide,
                    "oxide_per_product_kg": term.oxide_per_product_kg,
                    "O2_per_product_kg": term.o2_per_product_kg,
                }
                for term in parent_channels
            },
            "available_oxide_kg": {
                term.source.species: float(inventory_mol.get(term.source.parent_oxide or "", 0.0))
                * _formula_terms(term.source.parent_oxide or "")[1]
                for term in parent_channels
            },
            "melt_surface_area_m2": area_m2,
            "alpha": {
                term.source.species: float(term.source.alpha) for term in parent_channels
            },
            "evaporation_series_resistance": {
                "gas_resistance_enabled": False,
                "melt_resistance_enabled": False,
            },
            "overhead_pressure_pa": 0.0,
            "commanded_pressure_pa": 0.0,
            "stir_factor": 0.0,
        },
    )
    result = provider.dispatch(request)
    diagnostic = dict(result.diagnostic or {})
    if str(result.status) != "ok":
        raise ResidueInventoryRefusal(
            "residue_flux_refused",
            f"builtin HKL provider returned {result.status}: {diagnostic}",
        )
    if diagnostic.get("missing_alpha"):
        raise ResidueInventoryRefusal(
            "missing_evaporation_alpha",
            f"catalog channels have no executable alpha: {sorted(diagnostic['missing_alpha'])}",
        )
    return {
        str(species): float(rate) / _SECONDS_PER_HOUR
        for species, rate in dict(diagnostic.get("evaporation_flux_kg_hr") or {}).items()
    }


def integrate_residue_inventory(
    initial_inventory_mol: Mapping[str, float],
    channels: Sequence[ResidueChannel],
    pressure_model: Callable[[Mapping[str, float], float], Mapping[str, float]],
    *,
    temperature_K: float,
    duration_s: float,
    area_evolution_m2: Sequence[float] | None = None,
) -> ResidueInventoryResult:
    """Integrate free evaporation with an engine-specific pressure callback.

    Premise: a vacuum melt loses gas while each vapor channel debits the oxide
    parent that supplies its non-oxygen atoms. At the start of every finite
    sub-step the callback evaluates that engine's pressure laws at trial
    ``log10(pO2 / bar)`` and the shared R1a balance selects the congruent root.
    The builtin HKL provider then evaluates
    ``J_i = alpha_i * (p_i - p_bulk,i) / sqrt(2*pi*M_i*R*T)`` in mol m^-2 s^-1;
    here ``p_bulk=0`` and the provider receives the declared area, so its
    returned kg h^-1 is converted at the boundary back to mol s^-1.

    For a parent with inventory ``N`` mol and requested parent draw ``r`` mol
    s^-1, analytic depletion debits ``N * (1 - exp(-r*dt/N))`` mol during the
    step. Concurrent channels sharing a parent divide that debit in proportion
    to their HKL parent draw. Inventory and atom closure stay mol-native; gas
    mass is never subtracted as oxide mass. After those finite parent debits,
    the oxygen atom remainder is computed from the actual gas products and
    divided between O and O2 in the solved pressure-root flux ratio. This is
    the finite-step correction: ``nO_coproduct = Σ(nO_parent * parent_debit) -
    Σ(nO_gas * product_moles)`` and ``nO + 2*nO2 = nO_coproduct``. The frozen
    instantaneous O/O2 rates set only the partition ratio; they are not
    multiplied by dt and called the committed coproduct.

    Temperatures are K, pressure-model outputs are Pa, area is m^2, duration
    and areas are seconds and m^2, inventories and gas amounts are mol, and
    formula masses are kg mol^-1. A zero alpha or zero pressure gives zero
    channel flux; as dt tends to zero the analytic debit tends to ``r*dt``;
    for large dt no parent can be depleted below zero. Missing area evolution
    is a typed absence because the integrator must not choose geometry policy.
    """
    if area_evolution_m2 is None or len(area_evolution_m2) == 0:
        raise ResidueInventoryRefusal(
            "melt_surface_area_evolution_missing",
            "free evaporation requires an explicit per-step area evolution",
        )
    temperature = float(temperature_K)
    duration = float(duration_s)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ResidueInventoryRefusal("residue_temperature_invalid")
    if not math.isfinite(duration) or duration <= 0.0:
        raise ResidueInventoryRefusal("residue_duration_invalid")
    areas = tuple(float(area) for area in area_evolution_m2)
    if any(not math.isfinite(area) or area <= 0.0 for area in areas):
        raise ResidueInventoryRefusal("melt_surface_area_invalid")
    inventory = {str(species): float(amount) for species, amount in initial_inventory_mol.items()}
    if not inventory or any(not math.isfinite(value) or value < 0.0 for value in inventory.values()):
        raise ResidueInventoryRefusal("residue_inventory_invalid")
    terms = _channel_terms(channels)
    term_by_species = {term.source.species: term for term in terms}
    dt_s = duration / len(areas)
    evaporated: defaultdict[str, float] = defaultdict(float)
    pO2_steps: list[float | None] = []

    # Supplied channels define the supported pressure bundle. Root metadata
    # uses formula atom counts, matching R1a's (nO_gas - nO_parent) balance.
    for area_m2 in areas:
        active_parent_terms = [
            term
            for term in terms
            if term.parent_moles_per_product > 0.0
            and term.source.alpha > 0.0
            and inventory.get(term.source.parent_oxide or "", 0.0) > 0.0
        ]
        if not active_parent_terms:
            pO2_steps.append(None)
            continue

        def pressure_at(log10_pO2_bar: float) -> Mapping[str, float]:
            return pressure_model(dict(inventory), float(log10_pO2_bar))

        try:
            balance = _solve_vacuum_oxygen_balance(
                pressure_at,
                tuple(
                    _VacuumOxygenChannel(
                        species=term.source.species,
                        molar_mass_kg_per_mol=term.molar_mass_kg_mol,
                        oxygen_atoms=term.atom_counts.get("O", 0.0),
                        parent_oxygen_demand=term.parent_oxygen_demand,
                        pO2_exponent=(
                            term.atom_counts.get("O", 0.0)
                            - term.parent_oxygen_demand
                        )
                        / 2.0,
                        alpha=float(term.source.alpha),
                        alpha_source=term.source.alpha_source,
                        active=(
                            term.parent_moles_per_product == 0.0
                            or inventory.get(term.source.parent_oxide or "", 0.0) > 0.0
                        ),
                    )
                    for term in terms
                ),
                temperature_K=temperature,
            )
        except ValueError as exc:
            # No positive parent pressure is the zero-flux limit, not a
            # fabricated zero oxygen root. Other missing roots remain typed.
            low_pressure = pressure_at(-15.0)
            if not any(
                float(low_pressure.get(term.source.species, 0.0) or 0.0) > 0.0
                for term in active_parent_terms
            ):
                pO2_steps.append(None)
                continue
            raise ResidueInventoryRefusal(
                "residue_oxygen_balance_failed", str(exc)
            ) from exc
        pO2_steps.append(balance.pO2_bar)

        requested_rates_kg_s = _finite_step_fluxes(
            terms,
            inventory,
            temperature_K=temperature,
            area_m2=area_m2,
            pressures_Pa=balance.pressures_Pa,
        )
        requested_product_mol_s = {
            species: rate_kg_s / term_by_species[species].molar_mass_kg_mol
            for species, rate_kg_s in requested_rates_kg_s.items()
        }
        channels_by_parent: defaultdict[str, list[tuple[_ChannelTerms, float]]] = defaultdict(list)
        for species, product_rate in requested_product_mol_s.items():
            term = term_by_species[species]
            parent = term.source.parent_oxide
            if parent is None or product_rate <= 0.0:
                continue
            channels_by_parent[parent].append((term, product_rate))

        actual_parent_debits: dict[str, float] = {}
        actual_products: dict[str, float] = {}
        for parent, parent_channels in channels_by_parent.items():
            available_parent = float(inventory.get(parent, 0.0))
            requested_parent_rate = math.fsum(
                product_rate * term.parent_moles_per_product
                for term, product_rate in parent_channels
            )
            if available_parent <= 0.0 or requested_parent_rate <= 0.0:
                continue
            # Analytic first-order depletion in mol: 1-exp(-k*dt), with
            # k = requested parent mol s^-1 / current parent mol.
            debit = available_parent * -math.expm1(
                -requested_parent_rate * dt_s / available_parent
            )
            debit = min(available_parent, max(0.0, debit))
            actual_parent_debits[parent] = debit
            inventory[parent] = max(0.0, available_parent - debit)
            for term, product_rate in parent_channels:
                share = (
                    product_rate * term.parent_moles_per_product
                    / requested_parent_rate
                )
                product_mol = (
                    debit * share / term.parent_moles_per_product
                )
                actual_products[term.source.species] = product_mol
                evaporated[term.source.species] += product_mol

        oxygen_removed_mol = math.fsum(
            debit
            * _formula_terms(parent)[0].get("O", 0.0)
            for parent, debit in actual_parent_debits.items()
        )
        oxygen_in_parent_gases_mol = math.fsum(
            amount * term_by_species[species].atom_counts.get("O", 0.0)
            for species, amount in actual_products.items()
        )
        finite_step_oxygen_mol = oxygen_removed_mol - oxygen_in_parent_gases_mol
        oxygen_scale = max(oxygen_removed_mol, oxygen_in_parent_gases_mol, 1.0e-300)
        if finite_step_oxygen_mol < -max(1.0e-15, 1.0e-12 * oxygen_scale):
            raise ResidueInventoryRefusal(
                "finite_step_oxygen_requires_inbound_oxygen",
                f"actual parent debit requires {-finite_step_oxygen_mol:.17g} mol oxygen input",
            )
        finite_step_oxygen_mol = max(0.0, finite_step_oxygen_mol)
        raw_o = balance.channel_fluxes_mol_m2_s.get("O", 0.0) * area_m2 * dt_s
        raw_o2 = balance.channel_fluxes_mol_m2_s.get("O2", 0.0) * area_m2 * dt_s
        raw_oxygen_atoms = raw_o + 2.0 * raw_o2
        if finite_step_oxygen_mol > 0.0:
            if raw_oxygen_atoms <= 0.0:
                raise ResidueInventoryRefusal(
                    "finite_step_oxygen_partition_missing",
                    "solved O/O2 pressure partition has no positive oxygen flux",
                )
            partition_scale = finite_step_oxygen_mol / raw_oxygen_atoms
            actual_o = raw_o * partition_scale
            actual_o2 = raw_o2 * partition_scale
            if actual_o > 0.0:
                evaporated["O"] += actual_o
            if actual_o2 > 0.0:
                evaporated["O2"] += actual_o2

    initial_totals = _atom_totals(initial_inventory_mol, {})
    final_totals = _atom_totals(inventory, evaporated)
    closure = {
        element: initial_totals.get(element, 0.0) - final_totals.get(element, 0.0)
        for element in sorted(set(initial_totals) | set(final_totals))
    }
    for element, residual in closure.items():
        scale = max(abs(initial_totals.get(element, 0.0)), abs(final_totals.get(element, 0.0)), 1.0e-300)
        if abs(residual) > max(1.0e-15, 5.0e-12 * scale):
            raise ResidueInventoryRefusal(
                "residue_atom_balance_failed",
                f"{element} atom residual {residual:.17g} mol exceeds scale-aware tolerance",
            )
    return ResidueInventoryResult(
        residue_mol=dict(inventory),
        evaporated_mol=dict(evaporated),
        pO2_bar_by_step=tuple(pO2_steps),
        atom_closure_mol=closure,
    )
