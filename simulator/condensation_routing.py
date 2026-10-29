"""Canonical condenser-stage and extraction-product routing."""

from __future__ import annotations


STAGE_KEY_BY_NUMBER: dict[int, str] = {
    1: 'stage_1_fe_condenser',
    2: 'stage_2_cr_oxide_harvest',
    3: 'stage_3_sio_zone',
    4: 'stage_4_alkali_mg_cyclone',
}

# Stage 4 keeps its key. The K trap is a sub-stage of 4, not a new
# public stage number. sub_order 0 is every existing stage.
NA_COLLECTOR_STAGE_KEY = 'stage_4_alkali_mg_cyclone'
K_TRAP_STAGE_KEY = 'stage_4b_k_trap'
K_TRAP_STAGE_NUMBER = 4
K_TRAP_SUB_ORDER = 1
STAGE_KEY_BY_IDENTITY: dict[tuple[int, int], str] = {
    (number, 0): key for number, key in STAGE_KEY_BY_NUMBER.items()
}
STAGE_KEY_BY_IDENTITY[(K_TRAP_STAGE_NUMBER, K_TRAP_SUB_ORDER)] = K_TRAP_STAGE_KEY
# Collector bands the integer registry cannot name. Completeness checks
# require these keys; they do not renumber stages 5-7.
SUBSTAGE_COLLECTOR_KEYS: tuple[str, ...] = (K_TRAP_STAGE_KEY,)

# Config includes ducts and downstream monitors as well as product collectors.
# Keep the product/report registry above stable; reuse its canonical keys here.
COLLECTOR_STAGE_KEY_BY_NUMBER: dict[int, str] = {
    0: 'stage_0_hot_duct',
    **STAGE_KEY_BY_NUMBER,
    5: 'stage_5_vortex_dust_filter',
    6: 'stage_6_turbine_compressor',
    7: 'stage_7_accumulator',
}

STAGE_NUMBER_BY_KEY: dict[str, int] = {
    key: number for number, key in STAGE_KEY_BY_NUMBER.items()
}

# Backward/short names used by prior reviews and handoff prompts.
STAGE_NUMBER_BY_KEY.update({
    'stage_2_cr_harvester': 2,
    'stage_3_silica_baffle': 3,
    'stage_4_alkali_cyclone': 4,
    K_TRAP_STAGE_KEY: K_TRAP_STAGE_NUMBER,
})

STAGE_TARGET_SPECIES: dict[int, tuple[str, ...]] = {
    1: ('Fe',),
    2: ('Cr', 'CrO2', 'Mn'),
    3: ('SiO',),
    4: ('Na', 'Mg'),
}
# The integer map cannot hold two target tuples for stage 4.
STAGE_TARGET_SPECIES_BY_KEY: dict[str, tuple[str, ...]] = {
    K_TRAP_STAGE_KEY: ('K',),
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
    'Na': NA_COLLECTOR_STAGE_KEY,
    'K': K_TRAP_STAGE_KEY,
    'Mg': NA_COLLECTOR_STAGE_KEY,
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
        'Mg': NA_COLLECTOR_STAGE_KEY,
        'Ca': 'metal_phase_ca',
        'Na': NA_COLLECTOR_STAGE_KEY,
        'K': K_TRAP_STAGE_KEY,
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
    """Species that may land in a stage without counting as impurity.

    A sub-stage key that shares this number is not accepted here. K is
    designated to the K trap, so stage 4's accepted set is Na and Mg.
    """

    canonical = STAGE_KEY_BY_NUMBER.get(stage_number)
    accepted = {
        species
        for species, destination in DESIGNATED_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
        and (canonical is None or destination == canonical)
    }
    accepted.update(
        species
        for species, destination in CONDENSATION_COPRODUCT_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
        and (canonical is None or destination == canonical)
    )
    return frozenset(accepted)


def accepted_species_for_stage_key(stage_key: str) -> frozenset[str]:
    """Accepted species for one stage key, including a sub-stage key."""

    number = STAGE_NUMBER_BY_KEY.get(stage_key)
    if (
        number is not None
        and STAGE_KEY_BY_NUMBER.get(number) == stage_key
    ):
        return accepted_species_for_stage_number(number)
    accepted = {
        species
        for species, destination in DESIGNATED_STAGE.items()
        if destination == stage_key
    }
    accepted.update(
        species
        for species, destination in CONDENSATION_COPRODUCT_STAGE.items()
        if destination == stage_key
    )
    return frozenset(accepted)


def species_accepted_on_stage(stage: object, species: str) -> bool:
    """True when this stage object, not merely its number, accepts species."""

    key = getattr(stage, 'stage_key', '') or ''
    if key:
        if DESIGNATED_STAGE.get(species) == key:
            return True
        if CONDENSATION_COPRODUCT_STAGE.get(species) == key:
            return True
        if key == STAGE_KEY_BY_NUMBER.get(int(stage.stage_number)):
            return is_designated_for_stage(species, int(stage.stage_number))
        return False
    return is_designated_for_stage(species, int(stage.stage_number))


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


def target_species_for_stage_key(stage_key: str) -> list[str]:
    """Primary targets for a stage key. Sub-stage keys are not the integer map."""

    if stage_key in STAGE_TARGET_SPECIES_BY_KEY:
        return list(STAGE_TARGET_SPECIES_BY_KEY[stage_key])
    number = STAGE_NUMBER_BY_KEY.get(stage_key)
    if number is not None and STAGE_KEY_BY_NUMBER.get(number) == stage_key:
        return target_species_for_stage_number(number)
    return []


def stage_list_index_for_destination(train: object, destination: str | None) -> int | None:
    """List index of the stage object for a destination key.

    Public stage numbers stay 0-7. The K trap shares number 4 and is
    appended after stage 7, so its list index is not its stage number.
    Metal-phase destinations are not condenser stages.
    """

    if not destination or not str(destination).startswith('stage_'):
        return None
    stages = getattr(train, 'stages', ())
    for index, stage in enumerate(stages):
        if getattr(stage, 'stage_key', '') == destination:
            return index
    number = stage_number_for_destination(destination)
    if number is None:
        return None
    for index, stage in enumerate(stages):
        if (
            int(stage.stage_number) == number
            and int(getattr(stage, 'sub_order', 0) or 0) == 0
        ):
            return index
    return None


def collection_stage_index(train: object, stage_number: int, species: str) -> int | None:
    """List index that should receive a capture keyed by public stage number.

    When two stages share a number, a species designated to one of their
    keys lands on that stage. Every other species lands on sub_order 0.
    """

    stages = getattr(train, 'stages', ())
    matches = [
        index
        for index, stage in enumerate(stages)
        if int(stage.stage_number) == int(stage_number)
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    designated = DESIGNATED_STAGE.get(species)
    if designated:
        for index in matches:
            if getattr(stages[index], 'stage_key', '') == designated:
                return index
    for index in matches:
        if int(getattr(stages[index], 'sub_order', 0) or 0) == 0:
            return index
    return matches[0]


def product_destination(recipe: str, species: str) -> str | None:
    """Return the honest destination for a recipe-extracted product."""

    return PRODUCT_DESTINATIONS.get(recipe, {}).get(species)


def product_stage_number(recipe: str, species: str) -> int | None:
    """Return stage number only when product destination is a condenser stage."""

    return stage_number_for_destination(product_destination(recipe, species))


def is_designated_for_stage(species: str, stage_number: int) -> bool:
    """True when species belongs to the stage's accepted-species set."""

    return species in accepted_species_for_stage_number(stage_number)
