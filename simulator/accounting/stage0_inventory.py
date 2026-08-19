"""C0 target-inventory diagnostic (instrument-first; no ledger writes).

Remaining unevolved inventory is Stage-0 feed accounts plus leftover
target carriers in ``process.cleaned_melt``. Frozen ``HourSnapshot.inventory``
and ``vapor_contract_completeness`` are not sources. Stage-0 currently
disposes that inventory at load; every record carries
``stage0_release_kinetics`` so that instant depletion cannot be read as a
fast bake.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from simulator.accounting.exceptions import UnknownSpeciesError
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL, resolve_species_formula
from simulator.accounting.yield_disposition import _ACCOUNT_TO_BIN


STAGE0_RELEASE_KINETICS = "not_modelled_load_time_disposal"
TARGET_INVENTORY_SIBLING_SCHEMA_VERSION = 1
TARGET_INVENTORY_SIBLING_KIND = "target_inventory"
DEFAULT_EPSILON = 1.0e-6
DEFAULT_EXCLUDE_ELEMENTS: tuple[str, ...] = ("P",)
CHNOPS_ELEMENTS: tuple[str, ...] = ("C", "H", "N", "O", "P", "S")
CHNOPS_TRACKED_ELEMENTS: tuple[str, ...] = ("C", "H", "N", "S")
SEMANTIC_TARGET_CHNOPS = "CHNOPS"
STATUS_OK = "ok"
STATUS_ABSENT_FROM_LEDGER = "absent_from_ledger"
STATUS_DEFERRED_SEMANTIC_SPLIT = "deferred_semantic_split"
STATUS_ZERO_DENOMINATOR = "zero_denominator"
FB33_ABSENT_ELEMENTS: frozenset[str] = frozenset({"H", "C", "N"})

REMAINING_ACCOUNTS: tuple[str, ...] = (
    "process.stage0_volatile_feed",
    "process.stage0_salt_feed",
    "process.stage0_foulant",
    "process.stage0_carbonate_feed",
    "process.stage0_perchlorate_feed",
    "process.cleaned_melt",
    "process.raw_feedstock",
)
_DESTINATION_ACCOUNTS: tuple[str, ...] = tuple(
    account
    for account in _ACCOUNT_TO_BIN
    if account not in REMAINING_ACCOUNTS
)

_INITIAL_INVENTORY_FIELDS: tuple[str, ...] = (
    "gas_volatiles_kg",
    "sulfide_matte_kg",
    "salt_phase_kg",
    "chloride_salt_phase_kg",
    "cation_sulfate_feed_kg",
    "residual_components_kg",
    "melt_oxide_kg",
    "inert_melt_components_kg",
)

_SPECIES_TARGET_ALIASES: Mapping[str, frozenset[str]] = {
    "H2O": frozenset({"H2O"}),
    "CO2": frozenset({"CO2"}),
    "S2": frozenset({"S2", "S"}),
}

_S2_ELEMENT = "S"
_CHARACTERISTIC_ELEMENT = {
    "H2O": "H",
    "CO2": "C",
    "S2": "S",
}


def c0_target_keys(setpoints: Mapping[str, Any] | None) -> tuple[str, ...]:
    campaigns = (setpoints or {}).get("campaigns") if isinstance(setpoints, Mapping) else None
    if not isinstance(campaigns, Mapping):
        return ()
    c0 = campaigns.get("C0")
    if not isinstance(c0, Mapping):
        return ()
    raw = c0.get("target_species")
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw if item)
    return ()


def chnops_element_set(exclude: tuple[str, ...] | list[str] = DEFAULT_EXCLUDE_ELEMENTS) -> tuple[str, ...]:
    excluded = {str(element).strip() for element in exclude if str(element).strip()}
    return tuple(element for element in CHNOPS_ELEMENTS if element not in excluded)


def expansion_map(
    exclude_elements: tuple[str, ...] | list[str] = DEFAULT_EXCLUDE_ELEMENTS,
) -> dict[str, str]:
    excluded = {str(element).strip() for element in exclude_elements if str(element).strip()}
    mapping: dict[str, str] = {}
    for element in CHNOPS_ELEMENTS:
        if element in excluded:
            mapping[element] = "excluded"
        elif element == "O":
            mapping[element] = "excluded_oxide_bound"
        else:
            mapping[element] = "included"
    return mapping


def tracked_chnops_elements(
    exclude_elements: tuple[str, ...] | list[str] = DEFAULT_EXCLUDE_ELEMENTS,
) -> tuple[str, ...]:
    excluded = {str(element).strip() for element in exclude_elements if str(element).strip()}
    return tuple(
        element
        for element in CHNOPS_TRACKED_ELEMENTS
        if element not in excluded
    )


def sibling_artifact_payload(records: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    return {
        "schema_version": TARGET_INVENTORY_SIBLING_SCHEMA_VERSION,
        "kind": TARGET_INVENTORY_SIBLING_KIND,
        "stage0_release_kinetics": STAGE0_RELEASE_KINETICS,
        "records": [dict(record) for record in records],
    }


def initial_c0_inventory_kg(
    initial_inventory: Any,
    registry: Mapping[str, Any] | None,
    *,
    targets: tuple[str, ...] | None = None,
    exclude_elements: tuple[str, ...] = DEFAULT_EXCLUDE_ELEMENTS,
) -> dict[str, float]:
    buckets = _initial_species_kg(initial_inventory)
    keys = targets if targets is not None else ("H2O", "CO2", "S2", SEMANTIC_TARGET_CHNOPS)
    out: dict[str, float] = {}
    for key in keys:
        if key == SEMANTIC_TARGET_CHNOPS:
            mass = 0.0
            for element in tracked_chnops_elements(exclude_elements):
                mass += _element_kg_in_species_map(buckets, element, registry)
            out[key] = mass
            continue
        out[key] = _target_kg_in_species_map(buckets, key, registry)
    return out


def remaining_c0_inventory_kg(
    ledger: Any,
    registry: Mapping[str, Any] | None,
    *,
    targets: tuple[str, ...] | None = None,
    exclude_elements: tuple[str, ...] = DEFAULT_EXCLUDE_ELEMENTS,
) -> dict[str, dict[str, Any]]:
    by_account = _remaining_species_kg_by_account(ledger)
    keys = targets if targets is not None else ("H2O", "CO2", "S2", SEMANTIC_TARGET_CHNOPS)
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        if key == SEMANTIC_TARGET_CHNOPS:
            kg = 0.0
            mol_atoms = 0.0
            accounts: list[str] = []
            for element in tracked_chnops_elements(exclude_elements):
                element_kg, element_mol, element_accounts = _element_in_accounts(
                    by_account, element, registry
                )
                kg += element_kg
                mol_atoms += element_mol
                accounts.extend(element_accounts)
            out[key] = {
                "kg": kg,
                "mol_atoms": mol_atoms,
                "accounts": tuple(dict.fromkeys(accounts)),
            }
            continue
        kg, mol_atoms, accounts = _target_in_accounts(by_account, key, registry)
        out[key] = {
            "kg": kg,
            "mol_atoms": mol_atoms,
            "accounts": accounts,
        }
    return out


def depletion_record(
    *,
    hour: int,
    campaign: str,
    temperature_C: float,
    ledger: Any,
    initial_inventory: Any,
    registry: Mapping[str, Any] | None = None,
    setpoints: Mapping[str, Any] | None = None,
    feedstocks: Mapping[str, Any] | None = None,
    feedstock_key: str = "",
    previous_depletion_hour: int | str | None = None,
    epsilon: float = DEFAULT_EPSILON,
    exclude_elements: tuple[str, ...] = DEFAULT_EXCLUDE_ELEMENTS,
) -> dict[str, Any]:
    if registry is None:
        registry = getattr(ledger, "registry", None)
    targets = c0_target_keys(setpoints)
    if not targets:
        targets = ("H2O", "CO2", "S2", SEMANTIC_TARGET_CHNOPS)
    exclude = tuple(str(element) for element in exclude_elements if str(element).strip())
    expansion = expansion_map(exclude)
    tracked_elements = tracked_chnops_elements(exclude)
    fb33_absent = _fb33_absent_elements(feedstocks, feedstock_key)

    initial_buckets = _initial_species_kg(initial_inventory)
    remaining_by_account = _remaining_species_kg_by_account(ledger)
    released_by_account = _released_species_kg_by_account(ledger)

    rows: list[dict[str, Any]] = []
    expansion_rows: dict[str, dict[str, Any]] = {}

    for key in targets:
        if key == SEMANTIC_TARGET_CHNOPS:
            continue
        rows.append(
            _species_target_row(
                key=key,
                initial_buckets=initial_buckets,
                remaining_by_account=remaining_by_account,
                released_by_account=released_by_account,
                registry=registry,
            )
        )

    for element in tracked_elements:
        expansion_rows[element] = _element_row(
            element=element,
            initial_buckets=initial_buckets,
            remaining_by_account=remaining_by_account,
            released_by_account=released_by_account,
            registry=registry,
            fb33_absent=fb33_absent,
        )

    if SEMANTIC_TARGET_CHNOPS in targets:
        rows.append(
            _chnops_row(expansion_rows)
        )

    present_fractions = [
        float(row["remaining_fraction"])
        for row in rows
        if row.get("status") == STATUS_OK and row.get("remaining_fraction") is not None
    ]
    if present_fractions:
        aggregate = max(present_fractions)
    else:
        aggregate = 0.0
    depleted = bool(aggregate <= float(epsilon))
    if previous_depletion_hour is not None:
        depletion_hour: int | str | None = previous_depletion_hour
    elif depleted:
        depletion_hour = "load" if int(hour) <= 0 else int(hour)
    else:
        depletion_hour = None

    campaign_name = str(campaign or "")
    return {
        "hour": int(hour),
        "campaign": campaign_name,
        "T_C": float(temperature_C),
        "targets": rows,
        "chnops_expansion": {
            "map": expansion,
            "included_elements": list(tracked_elements),
            "exclude_elements": list(exclude),
            "oxide_bound_O": "excluded",
            "elements": expansion_rows,
        },
        "aggregate_remaining_fraction": aggregate,
        "depleted": depleted,
        "depletion_hour": depletion_hour,
        "would_be_inventory_advance": bool(depleted and campaign_name == "C0"),
        "epsilon": float(epsilon),
        "exclude_elements": list(exclude),
        "stage0_release_kinetics": STAGE0_RELEASE_KINETICS,
    }


def _species_target_row(
    *,
    key: str,
    initial_buckets: Mapping[str, float],
    remaining_by_account: Mapping[str, Mapping[str, float]],
    released_by_account: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    initial_kg, initial_mol = _target_totals(initial_buckets, key, registry)
    remaining_kg, remaining_mol, remaining_accounts = _target_in_accounts(
        remaining_by_account, key, registry
    )
    destinations = _target_destinations(released_by_account, key, registry)
    status = _status_for_quantities(
        initial_mol_atoms=initial_mol,
        remaining_mol_atoms=remaining_mol,
        fb33_member=False,
    )
    remaining_fraction = None
    if status == STATUS_OK and initial_mol > 0.0:
        remaining_fraction = remaining_mol / initial_mol
    return _row(
        key=key,
        status=status,
        initial_kg=initial_kg if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_kg=remaining_kg if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_fraction=remaining_fraction,
        initial_mol_atoms=initial_mol if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_mol_atoms=remaining_mol if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_accounts=remaining_accounts,
        destinations=destinations,
    )


def _element_row(
    *,
    element: str,
    initial_buckets: Mapping[str, float],
    remaining_by_account: Mapping[str, Mapping[str, float]],
    released_by_account: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any] | None,
    fb33_absent: frozenset[str],
) -> dict[str, Any]:
    initial_kg = _element_kg_in_species_map(initial_buckets, element, registry)
    initial_mol = _element_mol_in_species_map(initial_buckets, element, registry)
    remaining_kg, remaining_mol, remaining_accounts = _element_in_accounts(
        remaining_by_account, element, registry
    )
    destinations = _element_destinations(released_by_account, element, registry)
    status = _status_for_quantities(
        initial_mol_atoms=initial_mol,
        remaining_mol_atoms=remaining_mol,
        fb33_member=element in fb33_absent,
    )
    remaining_fraction = None
    if status == STATUS_OK and initial_mol > 0.0:
        remaining_fraction = remaining_mol / initial_mol
    return _row(
        key=element,
        status=status,
        initial_kg=initial_kg if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_kg=remaining_kg if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_fraction=remaining_fraction,
        initial_mol_atoms=initial_mol if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_mol_atoms=remaining_mol if status != STATUS_ABSENT_FROM_LEDGER else None,
        remaining_accounts=remaining_accounts,
        destinations=destinations,
    )


def _chnops_row(
    expansion_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    present = [
        row
        for row in expansion_rows.values()
        if row.get("status") == STATUS_OK
    ]
    initial_kg = sum(float(row["initial_kg"] or 0.0) for row in present)
    remaining_kg = sum(float(row["remaining_kg"] or 0.0) for row in present)
    initial_mol = sum(float(row["initial_mol_atoms"] or 0.0) for row in present)
    remaining_mol = sum(float(row["remaining_mol_atoms"] or 0.0) for row in present)
    accounts: list[str] = []
    for row in present:
        accounts.extend(row.get("remaining_accounts") or ())
    remaining_fraction = None
    if initial_mol > 0.0:
        remaining_fraction = remaining_mol / initial_mol
    destinations: dict[str, float] = {}
    for row in present:
        for dest, amount in dict(row.get("released_kg_by_destination") or {}).items():
            destinations[dest] = destinations.get(dest, 0.0) + float(amount)
    return _row(
        key=SEMANTIC_TARGET_CHNOPS,
        status=STATUS_DEFERRED_SEMANTIC_SPLIT,
        initial_kg=initial_kg,
        remaining_kg=remaining_kg,
        remaining_fraction=remaining_fraction,
        initial_mol_atoms=initial_mol,
        remaining_mol_atoms=remaining_mol,
        remaining_accounts=tuple(dict.fromkeys(accounts)),
        destinations=destinations,
    )


def _row(
    *,
    key: str,
    status: str,
    initial_kg: float | None,
    remaining_kg: float | None,
    remaining_fraction: float | None,
    initial_mol_atoms: float | None,
    remaining_mol_atoms: float | None,
    remaining_accounts: tuple[str, ...],
    destinations: Mapping[str, float],
) -> dict[str, Any]:
    primary = None
    if destinations:
        primary = max(destinations.items(), key=lambda item: item[1])[0]
    return {
        "key": key,
        "initial_kg": initial_kg,
        "remaining_kg": remaining_kg,
        "remaining_fraction": remaining_fraction,
        "initial_mol_atoms": initial_mol_atoms,
        "remaining_mol_atoms": remaining_mol_atoms,
        "remaining_accounts": list(remaining_accounts),
        "status": status,
        "released_kg_by_destination": dict(sorted(destinations.items())),
        "primary_destination": primary,
    }


def _status_for_quantities(
    *,
    initial_mol_atoms: float,
    remaining_mol_atoms: float,
    fb33_member: bool,
) -> str:
    if initial_mol_atoms > 0.0 or remaining_mol_atoms > 0.0:
        return STATUS_OK
    if fb33_member:
        return STATUS_ABSENT_FROM_LEDGER
    return STATUS_ZERO_DENOMINATOR


def _fb33_absent_elements(
    feedstocks: Mapping[str, Any] | None,
    feedstock_key: str,
) -> frozenset[str]:
    if not isinstance(feedstocks, Mapping) or not feedstock_key:
        return frozenset()
    fs = feedstocks.get(feedstock_key)
    if not isinstance(fs, Mapping):
        return frozenset()
    solar_wind = fs.get("solar_wind_volatiles")
    if not isinstance(solar_wind, Mapping):
        return frozenset()
    status = str(solar_wind.get("status") or "")
    if "inventory-only" not in status and "FB-33" not in status:
        return frozenset()
    species_ppm = solar_wind.get("species_ppm") or {}
    if not isinstance(species_ppm, Mapping):
        return frozenset()
    listed: set[str] = set()
    for raw_key in species_ppm:
        symbol = str(raw_key).strip()
        if symbol in ATOMIC_WEIGHTS_G_PER_MOL:
            listed.add(symbol)
            continue
        stripped = symbol.rstrip("0123456789")
        if stripped in ATOMIC_WEIGHTS_G_PER_MOL:
            listed.add(stripped)
    return frozenset(FB33_ABSENT_ELEMENTS & listed)


def _initial_species_kg(initial_inventory: Any) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    if initial_inventory is None:
        return {}
    for field_name in _INITIAL_INVENTORY_FIELDS:
        bucket = getattr(initial_inventory, field_name, None)
        if not isinstance(bucket, Mapping):
            continue
        for species, kg in bucket.items():
            try:
                value = float(kg)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value != 0.0:
                totals[str(species)] += value
    return dict(totals)


def _remaining_species_kg_by_account(ledger: Any) -> dict[str, dict[str, float]]:
    return _species_kg_by_accounts(ledger, REMAINING_ACCOUNTS)


def _released_species_kg_by_account(ledger: Any) -> dict[str, dict[str, float]]:
    return _species_kg_by_accounts(ledger, _DESTINATION_ACCOUNTS)


def _species_kg_by_accounts(
    ledger: Any,
    accounts: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if ledger is None:
        return out
    for account in accounts:
        try:
            species_kg = ledger.project_account_kg(account)
        except Exception:
            try:
                species_kg = ledger.kg_by_account(account)
            except Exception:
                continue
        if not isinstance(species_kg, Mapping):
            continue
        cleaned: dict[str, float] = {}
        for species, kg in species_kg.items():
            try:
                value = float(kg)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value != 0.0:
                cleaned[str(species)] = value
        if cleaned:
            out[account] = cleaned
    return out


def _target_kg_in_species_map(
    species_kg: Mapping[str, float],
    target: str,
    registry: Mapping[str, Any] | None,
) -> float:
    kg, _mol = _target_totals(species_kg, target, registry)
    return kg


def _target_totals(
    species_kg: Mapping[str, float],
    target: str,
    registry: Mapping[str, Any] | None,
) -> tuple[float, float]:
    if target == "S2":
        return (
            _element_kg_in_species_map(species_kg, _S2_ELEMENT, registry),
            _element_mol_in_species_map(species_kg, _S2_ELEMENT, registry),
        )
    kg = 0.0
    mol = 0.0
    aliases = _SPECIES_TARGET_ALIASES.get(target, frozenset({target}))
    characteristic = _CHARACTERISTIC_ELEMENT.get(target)
    for species, amount in species_kg.items():
        if not _species_matches_target(species, target, aliases, registry):
            continue
        kg += float(amount)
        if characteristic:
            mol += _element_mol_in_species(species, float(amount), characteristic, registry)
        else:
            mol += _species_mol(species, float(amount), registry)
    return kg, mol


def _target_in_accounts(
    by_account: Mapping[str, Mapping[str, float]],
    target: str,
    registry: Mapping[str, Any] | None,
) -> tuple[float, float, tuple[str, ...]]:
    kg = 0.0
    mol = 0.0
    accounts: list[str] = []
    for account, species_kg in by_account.items():
        part_kg, part_mol = _target_totals(species_kg, target, registry)
        if part_kg > 0.0 or part_mol > 0.0:
            kg += part_kg
            mol += part_mol
            accounts.append(account)
    return kg, mol, tuple(accounts)


def _element_in_accounts(
    by_account: Mapping[str, Mapping[str, float]],
    element: str,
    registry: Mapping[str, Any] | None,
) -> tuple[float, float, tuple[str, ...]]:
    kg = 0.0
    mol = 0.0
    accounts: list[str] = []
    for account, species_kg in by_account.items():
        part_kg = _element_kg_in_species_map(species_kg, element, registry)
        part_mol = _element_mol_in_species_map(species_kg, element, registry)
        if part_kg > 0.0 or part_mol > 0.0:
            kg += part_kg
            mol += part_mol
            accounts.append(account)
    return kg, mol, tuple(accounts)


def _target_destinations(
    released_by_account: Mapping[str, Mapping[str, float]],
    target: str,
    registry: Mapping[str, Any] | None,
) -> dict[str, float]:
    destinations: dict[str, float] = defaultdict(float)
    for account, species_kg in released_by_account.items():
        dest = _ACCOUNT_TO_BIN.get(account)
        if not dest:
            continue
        part_kg, _part_mol = _target_totals(species_kg, target, registry)
        if part_kg > 0.0:
            destinations[dest] += part_kg
    return dict(destinations)


def _element_destinations(
    released_by_account: Mapping[str, Mapping[str, float]],
    element: str,
    registry: Mapping[str, Any] | None,
) -> dict[str, float]:
    destinations: dict[str, float] = defaultdict(float)
    for account, species_kg in released_by_account.items():
        dest = _ACCOUNT_TO_BIN.get(account)
        if not dest:
            continue
        part_kg = _element_kg_in_species_map(species_kg, element, registry)
        if part_kg > 0.0:
            destinations[dest] += part_kg
    return dict(destinations)


def _species_matches_target(
    species: str,
    target: str,
    aliases: frozenset[str],
    registry: Mapping[str, Any] | None,
) -> bool:
    name = str(species)
    if name in aliases:
        return True
    if target == "H2O" and name.lower().startswith("h2o"):
        return True
    if target == "S2":
        return _element_count(name, _S2_ELEMENT, registry) > 0.0
    return False


def _element_kg_in_species_map(
    species_kg: Mapping[str, float],
    element: str,
    registry: Mapping[str, Any] | None,
) -> float:
    total = 0.0
    for species, kg in species_kg.items():
        total += _element_kg_in_species(species, float(kg), element, registry)
    return total


def _element_mol_in_species_map(
    species_kg: Mapping[str, float],
    element: str,
    registry: Mapping[str, Any] | None,
) -> float:
    total = 0.0
    for species, kg in species_kg.items():
        total += _element_mol_in_species(species, float(kg), element, registry)
    return total


def _element_kg_in_species(
    species: str,
    kg: float,
    element: str,
    registry: Mapping[str, Any] | None,
) -> float:
    mol = _element_mol_in_species(species, kg, element, registry)
    weight = ATOMIC_WEIGHTS_G_PER_MOL.get(element)
    if weight is None or mol == 0.0:
        return 0.0
    return mol * float(weight) / 1000.0


def _element_mol_in_species(
    species: str,
    kg: float,
    element: str,
    registry: Mapping[str, Any] | None,
) -> float:
    if not math.isfinite(kg) or kg == 0.0:
        return 0.0
    count = _element_count(species, element, registry)
    if count <= 0.0:
        return 0.0
    species_mol = _species_mol(species, kg, registry)
    if species_mol == 0.0:
        return 0.0
    return species_mol * count


def _element_count(
    species: str,
    element: str,
    registry: Mapping[str, Any] | None,
) -> float:
    formula = _formula(species, registry)
    if formula is None:
        return 0.0
    try:
        return float(formula.elements.get(element, 0.0) or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _species_mol(
    species: str,
    kg: float,
    registry: Mapping[str, Any] | None,
) -> float:
    formula = _formula(species, registry)
    if formula is None:
        return 0.0
    try:
        molar_mass_kg = float(formula.molar_mass_kg_per_mol())
    except Exception:
        return 0.0
    if not math.isfinite(molar_mass_kg) or molar_mass_kg <= 0.0:
        return 0.0
    return float(kg) / molar_mass_kg


def _formula(species: str, registry: Mapping[str, Any] | None) -> Any | None:
    try:
        return resolve_species_formula(str(species), registry)
    except (UnknownSpeciesError, ValueError, TypeError):
        return None
