"""Canonical condenser-stage and extraction-product routing."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import math

from simulator.scalar_boundary import is_declared_real_scalar


STAGE_KEY_BY_NUMBER: dict[int, str] = {
    1: 'stage_1_fe_condenser',
    2: 'stage_2_cr_oxide_harvest',
    3: 'stage_3_sio_zone',
    4: 'stage_4_alkali_mg_cyclone',
}

STAGE_NUMBER_BY_KEY: dict[str, int] = {
    key: number for number, key in STAGE_KEY_BY_NUMBER.items()
}

# Backward/short names used by prior reviews and handoff prompts.
STAGE_NUMBER_BY_KEY.update({
    'stage_2_cr_harvester': 2,
    'stage_3_silica_baffle': 3,
    'stage_4_alkali_cyclone': 4,
})

STAGE_TARGET_SPECIES: dict[int, tuple[str, ...]] = {
    1: ('Fe',),
    2: ('Cr', 'CrO2', 'Mn'),
    3: ('SiO',),
    4: ('Na', 'K', 'Mg'),
}

# Per CLAUDE.md sections 2/5 and Review F routing table. Product aliases
# such as SiO2/Cr2O3 are accepted only as condensate products of SiO/CrO2.
DESIGNATED_STAGE: dict[str, str] = {
    'Fe': 'stage_1_fe_condenser',
    'Cr': 'stage_2_cr_oxide_harvest',
    'CrO2': 'stage_2_cr_oxide_harvest',
    'Cr2O3': 'stage_2_cr_oxide_harvest',
    'Mn': 'stage_2_cr_oxide_harvest',
    'SiO': 'stage_3_sio_zone',
    'SiO2': 'stage_3_sio_zone',
    'Na': 'stage_4_alkali_mg_cyclone',
    'K': 'stage_4_alkali_mg_cyclone',
    'Mg': 'stage_4_alkali_mg_cyclone',
}

CONDENSATION_COPRODUCT_STAGE: dict[str, str] = {
    'Si': 'stage_3_sio_zone',
}

METAL_PHASE_DESTINATIONS: dict[str, str] = {
    'Al': 'metal_phase_al',
    'Ca': 'metal_phase_ca',
    'Si': 'metal_phase_si',
    'Ti': 'metal_phase_ti',
}

# Recipe-extracted products are not automatically condenser targets. A stage
# key here means the product is physically routed to that train stage; a
# metal_phase_* value means the atom ledger's metal-phase account is the honest
# destination and no condenser-stage projection should be minted.
PRODUCT_DESTINATIONS: dict[str, dict[str, str]] = {
    'C3': {
        'Fe': 'stage_1_fe_condenser',
        'Cr': 'stage_2_cr_oxide_harvest',
        'CrO2': 'stage_2_cr_oxide_harvest',
        'Ti': 'metal_phase_ti',
    },
    'C5': {
        'Fe': 'stage_1_fe_condenser',
        'Cr': 'stage_2_cr_oxide_harvest',
        'Mn': 'stage_2_cr_oxide_harvest',
        'Si': 'metal_phase_si',
        'Ti': 'metal_phase_ti',
    },
    'C6': {
        'Al': 'metal_phase_al',
        'Si': 'metal_phase_si',
    },
    'C7': {
        'Ca': 'stage_4_alkali_mg_cyclone',
    },
    'MRE': {
        'Fe': 'stage_1_fe_condenser',
        'Cr': 'stage_2_cr_oxide_harvest',
        'Mn': 'stage_2_cr_oxide_harvest',
        'Si': 'metal_phase_si',
        'Ti': 'metal_phase_ti',
        'Al': 'metal_phase_al',
        'Mg': 'stage_4_alkali_mg_cyclone',
        'Ca': 'metal_phase_ca',
        'Na': 'stage_4_alkali_mg_cyclone',
        'K': 'stage_4_alkali_mg_cyclone',
    },
}


def stage_number_for_destination(destination: str | None) -> int | None:
    """Return condenser stage number for a stage destination key."""

    if not destination:
        return None
    return STAGE_NUMBER_BY_KEY.get(destination)


def designated_stage_number(species: str) -> int | None:
    """Return canonical condenser stage for vapor species, if any."""

    return stage_number_for_destination(DESIGNATED_STAGE.get(species))


def accepted_species_for_stage_number(stage_number: int) -> frozenset[str]:
    """Species that may land in a stage without counting as impurity."""

    accepted = {
        species
        for species, destination in DESIGNATED_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
    }
    accepted.update(
        species
        for species, destination in CONDENSATION_COPRODUCT_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
    )
    return frozenset(accepted)


def coproduct_species_for_stage_number(stage_number: int) -> frozenset[str]:
    """Condensation coproducts accepted in a stage but not primary targets."""

    return frozenset(
        species
        for species, destination in CONDENSATION_COPRODUCT_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
    )


def target_species_for_stage_number(stage_number: int) -> list[str]:
    """Primary target species for public stage definitions."""

    return list(STAGE_TARGET_SPECIES.get(stage_number, ()))


def product_destination(recipe: str, species: str) -> str | None:
    """Return the honest destination for a recipe-extracted product."""

    return PRODUCT_DESTINATIONS.get(recipe, {}).get(species)


def product_stage_number(recipe: str, species: str) -> int | None:
    """Return stage number only when product destination is a condenser stage."""

    return stage_number_for_destination(product_destination(recipe, species))


def is_designated_for_stage(species: str, stage_number: int) -> bool:
    """True when species belongs to the stage's accepted-species set."""

    return species in accepted_species_for_stage_number(stage_number)


class CrownStationRefusal(ValueError):
    """A station input/surface law is unavailable, rather than zero recovery."""


@dataclass(frozen=True)
class CrownCarrierAllocation:
    """Tick-average mol/s; inventory is mol. No ledger mutation."""

    forward_mol_s: float
    gross_mol_s: float
    incidence_mol_s: float
    reemission_mol_s: float
    return_mol_s: float
    net_source_mol_s: float
    dish_gain_mol_s: float
    sky_mol_s: float
    oxidized_mol_s: float
    inventory_mol: float



def crown_ledger_route(
    accounts: Mapping[str, Mapping[str, float]],
    *,
    station: str,
    operation: str,
    allocations: Mapping[str, CrownCarrierAllocation],
    parent_debit_mol: Mapping[str, float],
    duration_s: float | None,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Translate a net station allocation into mol-native account movements.

    Parent debits are the source owner's *net* MO release, integrated over
    source area and tick duration. Returning atoms are already included there;
    adding a separate return credit would count them twice. The kernel checks
    the joint source/dish/sky atom balance and available stock before writing.
    No surface chemistry or oxygen recombination is inferred by this route.
    """
    from simulator.account_ids import CROWN_STATION_ACCOUNTS, OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT

    if station not in CROWN_STATION_ACCOUNTS:
        raise CrownStationRefusal(f"unknown crown station: {station!r}")
    dish = CROWN_STATION_ACCOUNTS[station]
    stock = accounts.get(dish, {})
    if operation == "harvest":
        # Emptying this physical inventory once makes a second harvest a no-op.
        # The existing C6 recovered-condensate path draws Mg from this account.
        if allocations or parent_debit_mol:
            raise CrownStationRefusal("harvest cannot also collect a source allocation")
        product = {s: n for s, n in stock.items() if n > 0}
        return ({dish: product}, {"process.condensation_train": product}) if product else ({}, {})
    if operation != "collect":
        raise CrownStationRefusal(f"unknown crown operation: {operation!r}")
    if duration_s is None or not is_declared_real_scalar(duration_s) or not math.isfinite(duration_s) or duration_s <= 0:
        raise CrownStationRefusal("duration_s must be finite and positive")

    debits: dict[str, dict[str, float]] = {}
    credits: dict[str, dict[str, float]] = {}

    def movement(account: str, species: str, amount: float) -> None:
        if not math.isfinite(amount):
            raise CrownStationRefusal(f"{species}: nonfinite crown movement")
        if amount:
            side = credits if amount > 0 else debits
            side.setdefault(account, {})[species] = abs(amount)

    for parent, amount in parent_debit_mol.items():
        if parent not in {"MgO", "CaO"} or not is_declared_real_scalar(amount):
            raise CrownStationRefusal(f"unsupported crown parent debit: {parent!r}")
        movement("process.cleaned_melt", parent, -amount)
    for species, allocation in allocations.items():
        if not isinstance(allocation, CrownCarrierAllocation):
            raise CrownStationRefusal(f"{species}: missing station allocation")
        if allocation.oxidized_mol_s != 0:
            raise CrownStationRefusal("crown oxidation requires an explicit reaction allocation")
        if allocation.sky_mol_s < 0 or allocation.inventory_mol < 0:
            raise CrownStationRefusal(f"{species}: negative station disposition")
        if not math.isclose(
            allocation.net_source_mol_s,
            allocation.dish_gain_mol_s + allocation.sky_mol_s,
            rel_tol=1e-12, abs_tol=1e-15,
        ):
            raise CrownStationRefusal(f"{species}: station disposition does not close")
        gain = allocation.dish_gain_mol_s * duration_s
        if not math.isclose(stock.get(species, 0.0) + gain, allocation.inventory_mol,
                            rel_tol=1e-12, abs_tol=1e-15):
            raise CrownStationRefusal(f"{species}: stale or inconsistent dish inventory")
        movement(dish, species, gain)
        destination = (OXYGEN_MELT_OFFGAS_VENTED_ACCOUNT
                       if species == "O2" else "terminal.offgas")
        movement(destination, species, allocation.sky_mol_s * duration_s)
    return debits, credits
