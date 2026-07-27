"""Origin-first terminal yield-disposition projection."""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Callable

from simulator.accounting.exceptions import OriginUnresolvedError
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.lots import allocate_pool_withdrawal
from simulator.accounting.queries import TERMINAL_RUMP_REFRACTORY_OXIDES


YIELD_DISPOSITION_SCHEMA_VERSION = "5.0"
YIELD_DISPOSITION_BINS: tuple[str, ...] = (
    "product_condensed",
    "product_tapped",
    "product_oxygen",
    "cleanup_volatile_product",
    "melt_retained",
    "metal_phase_retained",
    "wall_deposit",
    "charge_unprocessed",
    "redox_buffer_retained",
    "cleanup_sequestered",
    "offgas_vented",
    "overhead_terminal_inventory",
)
MELT_RETAINED_SUBDISPOSITIONS: tuple[str, ...] = (
    "oxide_unextracted",
    "refractory_rump",
    "residual_reductant",
    "stage0_slag",
)

_ACCOUNT_TO_BIN: Mapping[str, str] = {
    "process.cleaned_melt": "melt_retained",
    "process.c7_al_credit": "charge_unprocessed",
    "process.condensation_retained_holdup": "overhead_terminal_inventory",
    "process.metal_phase": "metal_phase_retained",
    "process.metal_phase_bottom_pool": "metal_phase_retained",
    "process.metal_phase_float_layer": "metal_phase_retained",
    "process.overhead_gas": "overhead_terminal_inventory",
    "process.raw_feedstock": "charge_unprocessed",
    "process.reagent_inventory": "product_condensed",
    "process.solid_char_carbon": "melt_retained",
    "process.spent_reductant_residue": "melt_retained",
    "reservoir.fo2_buffer": "redox_buffer_retained",
    "reservoir.oxygen_cistern_liquid_inventory": "product_oxygen",
    "reservoir.reagent.C": "charge_unprocessed",
    "reservoir.reagent.K": "charge_unprocessed",
    "reservoir.reagent.Mg": "charge_unprocessed",
    "reservoir.reagent.Na": "charge_unprocessed",
    "terminal.chromium_condensed_oxide_stored": "product_condensed",
    "terminal.drain_tap_material": "product_tapped",
    "terminal.offgas": "offgas_vented",
    "terminal.oxygen_melt_offgas_captured": "product_oxygen",
    "terminal.oxygen_melt_offgas_stored": "product_oxygen",
    "terminal.oxygen_melt_offgas_vented_to_vacuum": "offgas_vented",
    "terminal.oxygen_mre_anode_stored": "product_oxygen",
    "terminal.oxygen_stage0_stored": "product_oxygen",
    "terminal.slag": "melt_retained",
    "terminal.stage0_chloride_salt_phase": "cleanup_sequestered",
    "terminal.stage0_residual_carbonate_carbon": "cleanup_sequestered",
    "terminal.stage0_residual_refractory_carbon": "cleanup_sequestered",
    "terminal.stage0_salt_phase": "cleanup_sequestered",
    "terminal.stage0_sulfide_matte": "cleanup_sequestered",
    "vent": "offgas_vented",
}
_CONDENSATION_ACCOUNT = "process.condensation_train"
_CLEANUP_CAMPAIGNS = frozenset({"C0", "C0B"})
_CONDENSATION_AMALGAMATED_ELEMENTS = frozenset({"Na", "K"})
_WALL_PREFIX = "process.wall_deposit_segment_"
_CLOSURE_LIMIT_FRACTION = 5.0e-14
_ATOM_EPS = 1.0e-18
_ORIGIN_UNATTRIBUTED = "origin_unattributed"


class YieldDispositionError(RuntimeError):
    """Terminal state cannot satisfy the rev5 disposition contract."""


@dataclass(frozen=True)
class _Portion:
    account: str
    destination: str
    species_mol: Mapping[str, float]
    feedstock_atoms: Mapping[str, float]
    reagent_atoms: Mapping[str, float]
    origin_unattributed_atoms: Mapping[str, float]
    attribution_method_by_element: Mapping[str, str]
    campaign_scope: str = "terminal"
    subdisposition: str | None = None


def capture_ledger_snapshot(sim: Any, snapshot: Any) -> None:
    """Capture terminal-account and origin balances needed for campaign splits."""

    ledger = getattr(sim, "atom_ledger", None)
    if ledger is None or not callable(getattr(ledger, "mol_by_account", None)):
        return
    rows = getattr(sim, "_yield_disposition_ledger_snapshots", None)
    if not isinstance(rows, dict):
        rows = {}
        setattr(sim, "_yield_disposition_ledger_snapshots", rows)
    campaign = _campaign_name(getattr(snapshot, "campaign", ""))
    key = (
        int(getattr(snapshot, "hour", len(rows))),
        campaign,
        int(getattr(snapshot, "campaign_hour", 0)),
    )
    balances = ledger.mol_by_account()
    origin_balances = ledger.origin_atom_moles_by_account()
    origin_unattributed = ledger.unresolved_origin_atom_moles_by_account()
    origin_methods = ledger.origin_attribution_methods_by_account()
    gross_flows = ledger.gross_account_flows()
    rows[key] = {
        "hour": key[0],
        "campaign": campaign,
        "campaign_hour": key[2],
        "ledger": {
            _CONDENSATION_ACCOUNT: copy.deepcopy(
                balances.get(_CONDENSATION_ACCOUNT, {})
            )
        },
        "material_origin_atom_moles_by_account": {
            _CONDENSATION_ACCOUNT: copy.deepcopy(
                origin_balances.get(_CONDENSATION_ACCOUNT, {})
            )
        },
        "origin_unattributed_atom_moles_by_account": {
            _CONDENSATION_ACCOUNT: copy.deepcopy(
                origin_unattributed.get(_CONDENSATION_ACCOUNT, {})
            )
        },
        "origin_attribution_methods_by_account": {
            _CONDENSATION_ACCOUNT: copy.deepcopy(
                origin_methods.get(_CONDENSATION_ACCOUNT, {})
            )
        },
        "gross_inputs": _pool_flow_snapshot(
            gross_flows.get("inputs", {}),
            _CONDENSATION_ACCOUNT,
        ),
        "gross_withdrawals": _pool_flow_snapshot(
            gross_flows.get("withdrawals", {}),
            _CONDENSATION_ACCOUNT,
        ),
        "gross_events": [
            copy.deepcopy(event)
            for event in ledger.gross_account_flow_events()
            if event.get("account") == _CONDENSATION_ACCOUNT
        ],
    }


def ledger_snapshots_from_sim(sim: Any) -> tuple[Mapping[str, Any], ...]:
    rows = getattr(sim, "_yield_disposition_ledger_snapshots", {})
    if not isinstance(rows, Mapping):
        return ()
    return tuple(row for row in rows.values() if isinstance(row, Mapping))


def _pool_flow_snapshot(
    flow: Mapping[str, Any],
    account: str,
) -> dict[str, Any]:
    species = flow.get("species_mol_by_account", {})
    origins = flow.get("material_origin_atom_moles_by_account", {})
    unattributed = flow.get("origin_unattributed_atom_moles_by_account", {})
    return {
        "ledger": {
            account: copy.deepcopy(
                species.get(account, {}) if isinstance(species, Mapping) else {}
            )
        },
        "material_origin_atom_moles_by_account": {
            account: copy.deepcopy(
                origins.get(account, {}) if isinstance(origins, Mapping) else {}
            )
        },
        "origin_unattributed_atom_moles_by_account": {
            account: copy.deepcopy(
                (
                    unattributed.get(account, {})
                    if isinstance(unattributed, Mapping)
                    else {}
                )
            )
        },
    }


def _snapshot_pool_species(
    row: Mapping[str, Any],
    flow_name: str,
    account: str,
) -> dict[str, float]:
    flow = row.get(flow_name)
    if not isinstance(flow, Mapping):
        raise OriginUnresolvedError(f"missing {flow_name} pool counters")
    ledger = flow.get("ledger")
    if not isinstance(ledger, Mapping):
        raise OriginUnresolvedError(f"malformed {flow_name} physical counters")
    return _positive_species(ledger.get(account, {}))


def _snapshot_pool_origin(
    row: Mapping[str, Any],
    flow_name: str,
    account: str,
    material_origin: str,
) -> dict[str, float]:
    flow = row.get(flow_name)
    if not isinstance(flow, Mapping):
        raise OriginUnresolvedError(f"missing {flow_name} pool counters")
    tracked = flow.get("material_origin_atom_moles_by_account")
    if not isinstance(tracked, Mapping):
        raise OriginUnresolvedError(f"malformed {flow_name} origin counters")
    account_origins = tracked.get(account, {})
    if not isinstance(account_origins, Mapping):
        raise OriginUnresolvedError(
            f"malformed {flow_name} origin counters for {account}"
        )
    return _element_mol_atoms(
        {
            element: origins.get(material_origin, 0.0)
            for element, origins in account_origins.items()
            if isinstance(origins, Mapping)
        }
    )


def _snapshot_pool_unattributed(
    row: Mapping[str, Any],
    flow_name: str,
    account: str,
) -> dict[str, float]:
    flow = row.get(flow_name)
    if not isinstance(flow, Mapping):
        raise OriginUnresolvedError(f"missing {flow_name} pool counters")
    values = flow.get("origin_unattributed_atom_moles_by_account")
    if not isinstance(values, Mapping):
        raise OriginUnresolvedError(
            f"malformed {flow_name} origin-unattributed counters"
        )
    account_values = values.get(account, {})
    if not isinstance(account_values, Mapping):
        raise OriginUnresolvedError(
            f"malformed {flow_name} origin-unattributed counters for {account}"
        )
    return _element_mol_atoms(account_values)


def _snapshot_pool_events(
    row: Mapping[str, Any],
    account: str,
) -> tuple[Mapping[str, Any], ...]:
    raw_events = row.get("gross_events")
    if not isinstance(raw_events, list):
        raise OriginUnresolvedError("missing ordered gross pool events")
    events = []
    for event in raw_events:
        if not isinstance(event, Mapping):
            raise OriginUnresolvedError("malformed ordered gross pool event")
        if event.get("account") != account:
            raise OriginUnresolvedError(
                f"gross pool event account mismatch for {account}"
            )
        if event.get("direction") not in {"inputs", "withdrawals"}:
            raise OriginUnresolvedError("malformed gross pool event direction")
        events.append(event)
    return tuple(events)


def build_yield_disposition(
    sim: Any,
    ledger_snapshots: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Project feedstock-origin element mol-atoms into the twelve rev5 bins."""

    ledger = sim.atom_ledger
    registry = ledger.registry
    terminal = ledger.mol_by_account()
    snapshots = tuple(ledger_snapshots)
    feedstock_input, reagent_input = _input_atoms(ledger)
    origin_unattributed_input = _element_mol_atoms(
        ledger.initial_unattributed_atom_moles()
    )
    origin_dust_mol_atoms = _origin_dust_tolerance(
        ledger,
        feedstock_input,
        reagent_input,
    )
    origin_unattributed_by_account = (
        ledger.unresolved_origin_atom_moles_by_account()
    )
    terminal_origin_unattributed = _sum_account_elements(
        origin_unattributed_by_account
    )
    cumulative_origin_unattributed = _cumulative_origin_unattributed(
        ledger.cumulative_origin_unattributed_atom_moles(),
        terminal_origin_unattributed,
    )
    _assert_origin_unattributed_within_limit(
        cumulative_origin_unattributed,
        origin_dust_mol_atoms,
    )
    closure_rows, closure_max_residual, closure_max_residual_mol_atoms = (
        _full_atom_closure(
            terminal,
            registry,
            feedstock_input,
            reagent_input,
            origin_unattributed_input,
        )
    )
    portions, reagent_terminal, reagent_methods, streams = _terminal_portions(
        ledger,
        terminal,
        registry,
        snapshots,
        origin_dust_mol_atoms,
    )
    _assert_reagent_cycle_closes(
        reagent_input,
        reagent_terminal,
        origin_dust_mol_atoms,
    )

    atoms_by_bin: dict[str, defaultdict[str, float]] = {
        name: defaultdict(float) for name in YIELD_DISPOSITION_BINS
    }
    subdisposition_atoms: dict[str, defaultdict[str, float]] = {
        name: defaultdict(float) for name in MELT_RETAINED_SUBDISPOSITIONS
    }
    accounts_by_link: dict[tuple[str, str], set[str]] = defaultdict(set)
    methods_by_link: dict[tuple[str, str], set[str]] = defaultdict(set)
    subdisposition_methods: dict[str, defaultdict[str, set[str]]] = {
        name: defaultdict(set) for name in MELT_RETAINED_SUBDISPOSITIONS
    }
    for portion in portions:
        for element, raw_feedstock in portion.feedstock_atoms.items():
            feedstock = float(raw_feedstock)
            if feedstock <= 0.0:
                continue
            method = portion.attribution_method_by_element.get(element)
            if method not in {"tracked", "pool_ratio"}:
                raise OriginUnresolvedError(
                    f"attribution method unresolved for "
                    f"{portion.account}.{element}"
                )
            atoms_by_bin[portion.destination][element] += feedstock
            accounts_by_link[(element, portion.destination)].add(portion.account)
            methods_by_link[(element, portion.destination)].add(method)
            if portion.subdisposition is not None:
                subdisposition_atoms[portion.subdisposition][element] += feedstock
                subdisposition_methods[portion.subdisposition][element].add(method)

    fraction_rows: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    max_origin_residual = 0.0
    max_origin_residual_mol_atoms = 0.0
    for element, denominator in sorted(feedstock_input.items()):
        if denominator <= 0.0:
            continue
        mol_atoms = {
            destination: float(atoms_by_bin[destination].get(element, 0.0))
            for destination in YIELD_DISPOSITION_BINS
        }
        # Derivation: each fraction is destination mol-atoms / charged
        # feedstock mol-atoms, so sum(fractions)-1 is exactly the normalized
        # terminal-minus-input atom residual. No scale-to-close is applied.
        fractions = {
            destination: amount / denominator
            for destination, amount in mol_atoms.items()
        }
        residual = math.fsum(fractions.values()) - 1.0
        residual_mol_atoms = math.fsum(mol_atoms.values()) - denominator
        if abs(residual_mol_atoms) > origin_dust_mol_atoms:
            raise OriginUnresolvedError(
                f"feedstock origin attribution unresolved for {element}: "
                f"residual={residual:.12g} fraction, "
                f"residual_mol_atoms={residual_mol_atoms:.12g}"
            )
        max_origin_residual = max(max_origin_residual, abs(residual))
        max_origin_residual_mol_atoms = max(
            max_origin_residual_mol_atoms,
            abs(residual_mol_atoms),
        )
        destination_methods = {
            destination: _merged_attribution_method(
                methods_by_link.get((element, destination), set())
            )
            for destination, amount in mol_atoms.items()
            if amount > 0.0
        }
        fraction_rows.append(
            {
                "element": element,
                "feedstock_input_mol_atoms": denominator,
                "destination_mol_atoms": mol_atoms,
                "destination_fractions": fractions,
                "attribution_method": _merged_attribution_method(
                    destination_methods.values()
                ),
                "attribution_method_by_destination": destination_methods,
                "closure_residual_fraction": residual,
                "closure_residual_mol_atoms": residual_mol_atoms,
                "origin_attribution_residual_fraction": residual,
                "origin_attribution_residual_mol_atoms": residual_mol_atoms,
            }
        )
        for destination, amount in mol_atoms.items():
            if amount <= 0.0:
                continue
            links.append(
                {
                    "source": f"feedstock_element:{element}",
                    "target": f"destination:{destination}",
                    "element": element,
                    "destination": destination,
                    "mol_atoms": amount,
                    "fraction_of_feedstock_element": amount / denominator,
                    "source_accounts": sorted(accounts_by_link[(element, destination)]),
                    "attribution_method": _merged_attribution_method(
                        methods_by_link[(element, destination)]
                    ),
                }
            )

    melt_sub_rows = _melt_subdisposition_rows(
        atoms_by_bin["melt_retained"],
        subdisposition_atoms,
        subdisposition_methods,
    )
    nodes = [
        {
            "id": f"feedstock_element:{row['element']}",
            "kind": "feedstock_element",
            "element": row["element"],
            "mol_atoms": row["feedstock_input_mol_atoms"],
        }
        for row in fraction_rows
    ]
    nodes.extend(
        {
            "id": f"destination:{destination}",
            "kind": "destination",
            "destination": destination,
        }
        for destination in YIELD_DISPOSITION_BINS
    )
    reagent_rows = []
    reagent_max_residual = 0.0
    for element in sorted(set(reagent_input) | set(reagent_terminal)):
        input_atoms = float(reagent_input.get(element, 0.0))
        terminal_atoms = float(reagent_terminal.get(element, 0.0))
        residual = (
            (terminal_atoms - input_atoms) / input_atoms
            if input_atoms > _ATOM_EPS
            else 0.0
        )
        reagent_max_residual = max(reagent_max_residual, abs(residual))
        reagent_rows.append(
            {
                "element": element,
                "input_mol_atoms": input_atoms,
                "terminal_excluded_mol_atoms": terminal_atoms,
                "attribution_method": reagent_methods.get(element, "tracked"),
                "closure_residual_fraction": residual,
            }
        )

    return {
        "schema_version": YIELD_DISPOSITION_SCHEMA_VERSION,
        "basis": "feedstock_element_atom_fraction",
        "destination_bins": list(YIELD_DISPOSITION_BINS),
        "fraction_table": {
            "basis": "feedstock_element_atom_fraction",
            "rows": fraction_rows,
        },
        "melt_retained_subdispositions": {
            "basis": "feedstock_element_mol_atoms",
            "subdispositions": list(MELT_RETAINED_SUBDISPOSITIONS),
            "rows": melt_sub_rows,
        },
        "nodes": nodes,
        "links": links,
        "terminal_species_streams": streams,
        "reagent_cycle": {
            "basis": "excluded_from_feedstock_atom_denominator",
            "rows": reagent_rows,
            "closure_residual_fraction": reagent_max_residual,
        },
        _ORIGIN_UNATTRIBUTED: {
            "basis": "element_mol_atoms",
            "limit_mol_atoms": origin_dust_mol_atoms,
            "input_mol_atoms_by_element": origin_unattributed_input,
            "terminal_mol_atoms_by_element": terminal_origin_unattributed,
            "cumulative_mol_atoms_by_element": cumulative_origin_unattributed,
            "terminal_mol_atoms_by_account": origin_unattributed_by_account,
        },
        "closure": {
            "limit_fraction": _CLOSURE_LIMIT_FRACTION,
            "rows": closure_rows,
            "maximum_residual_fraction": closure_max_residual,
            "origin_dust_limit_mol_atoms": origin_dust_mol_atoms,
            "maximum_residual_mol_atoms": closure_max_residual_mol_atoms,
            "maximum_feedstock_origin_attribution_residual_fraction": (
                max_origin_residual
            ),
            "maximum_feedstock_origin_attribution_residual_mol_atoms": (
                max_origin_residual_mol_atoms
            ),
        },
    }


def _input_atoms(
    ledger: Any,
) -> tuple[dict[str, float], dict[str, float]]:
    external = ledger.external_origin_atom_moles()
    return (
        _element_mol_atoms(external.get("feedstock", {})),
        _element_mol_atoms(external.get("reagent", {})),
    )


def _terminal_portions(
    ledger: Any,
    terminal: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
    snapshots: tuple[Mapping[str, Any], ...],
    origin_dust_mol_atoms: float,
) -> tuple[
    list[_Portion],
    dict[str, float],
    dict[str, str],
    list[dict[str, Any]],
]:
    origin_balances = ledger.origin_atom_moles_by_account()
    unresolved_balances = ledger.unresolved_origin_atom_moles_by_account()
    origin_methods = ledger.origin_attribution_methods_by_account()
    reagent_terminal: defaultdict[str, float] = defaultdict(float)
    reagent_method_sets: defaultdict[str, set[str]] = defaultdict(set)
    portions: list[_Portion] = []
    streams: list[dict[str, Any]] = []

    for account, raw_species in terminal.items():
        species_mol = _positive_species(raw_species)
        if not species_mol:
            continue
        account = str(account)
        physical_atoms = _element_atoms(species_mol, registry)
        account_origins = origin_balances.get(account, {})
        account_unresolved = unresolved_balances.get(account, {})
        methods = origin_methods.get(account, {})
        feedstock_atoms: dict[str, float] = {}
        reagent_atoms: dict[str, float] = {}
        origin_unattributed_atoms: dict[str, float] = {}
        resolved_methods: dict[str, str] = {}
        for element, physical in physical_atoms.items():
            unresolved = float(account_unresolved.get(element, 0.0))
            tolerance = _atom_tolerance(physical)
            typed = account_origins.get(element, {})
            feedstock = float(typed.get("feedstock", 0.0))
            reagent = float(typed.get("reagent", 0.0))
            if not math.isclose(
                feedstock + reagent + unresolved,
                float(physical),
                rel_tol=0.0,
                abs_tol=tolerance,
            ):
                raise OriginUnresolvedError(
                    f"typed origin does not close for {account}.{element}: "
                    f"feedstock={feedstock:.12g}, reagent={reagent:.12g}, "
                    f"origin_unattributed={unresolved:.12g}, "
                    f"physical={physical:.12g} mol-atoms"
                )
            if unresolved > 0.0:
                origin_unattributed_atoms[element] = unresolved
            method = methods.get(element)
            if feedstock > 0.0 or reagent > 0.0:
                if method not in {"tracked", "pool_ratio"}:
                    raise OriginUnresolvedError(
                        f"attribution method unresolved for {account}.{element}"
                    )
                resolved_methods[element] = method
            if feedstock > 0.0:
                feedstock_atoms[element] = feedstock
            if reagent > 0.0:
                reagent_atoms[element] = reagent
                reagent_terminal[element] += reagent
                reagent_method_sets[element].add(method)

        if account == _CONDENSATION_ACCOUNT:
            condensation = _condensation_portions(
                species_mol,
                feedstock_atoms,
                reagent_atoms,
                origin_unattributed_atoms,
                resolved_methods,
                snapshots,
                registry,
                origin_dust_mol_atoms,
            )
            for portion in condensation:
                portions.append(portion)
                streams.extend(_stream_rows(portion, registry))
                for element in portion.reagent_atoms:
                    reagent_method_sets[element].add(
                        portion.attribution_method_by_element[element]
                    )
            continue

        destination = _destination_for_account(account)
        if destination is None:
            raise YieldDispositionError(
                f"unknown nonzero terminal account: {account}"
            )
        split = _account_portions(
            account,
            destination,
            species_mol,
            feedstock_atoms,
            reagent_atoms,
            origin_unattributed_atoms,
            resolved_methods,
            registry,
        )
        for portion in split:
            portions.append(portion)
            streams.extend(_stream_rows(portion, registry))
    return (
        portions,
        dict(reagent_terminal),
        {
            element: _merged_attribution_method(methods)
            for element, methods in reagent_method_sets.items()
        },
        streams,
    )


def _account_portions(
    account: str,
    destination: str,
    species_mol: Mapping[str, float],
    feedstock_atoms: Mapping[str, float],
    reagent_atoms: Mapping[str, float],
    origin_unattributed_atoms: Mapping[str, float],
    attribution_methods: Mapping[str, str],
    registry: Mapping[str, Any],
) -> list[_Portion]:
    subdisposition = _account_subdisposition(account)
    if account != "process.cleaned_melt":
        return [
            _Portion(
                account=account,
                destination=destination,
                species_mol=species_mol,
                feedstock_atoms=feedstock_atoms,
                reagent_atoms=reagent_atoms,
                origin_unattributed_atoms=origin_unattributed_atoms,
                attribution_method_by_element=attribution_methods,
                subdisposition=subdisposition,
            )
        ]
    groups = {
        "refractory_rump": {
            species: amount
            for species, amount in species_mol.items()
            if species in TERMINAL_RUMP_REFRACTORY_OXIDES
        },
        "oxide_unextracted": {
            species: amount
            for species, amount in species_mol.items()
            if species not in TERMINAL_RUMP_REFRACTORY_OXIDES
        },
    }
    group_atoms = {
        name: _element_atoms(values, registry)
        for name, values in groups.items()
        if values
    }
    group_feedstock = _allocate_origin_to_unique_groups(
        account,
        "feedstock",
        feedstock_atoms,
        group_atoms,
    )
    group_reagent = _allocate_origin_to_unique_groups(
        account,
        "reagent",
        reagent_atoms,
        group_atoms,
    )
    group_unattributed = _allocate_origin_to_unique_groups(
        account,
        _ORIGIN_UNATTRIBUTED,
        origin_unattributed_atoms,
        group_atoms,
    )
    return [
        _Portion(
            account=account,
            destination=destination,
            species_mol=values,
            feedstock_atoms=group_feedstock.get(name, {}),
            reagent_atoms=group_reagent.get(name, {}),
            origin_unattributed_atoms=group_unattributed.get(name, {}),
            attribution_method_by_element={
                element: attribution_methods[element]
                for element in set(group_feedstock.get(name, {}))
                | set(group_reagent.get(name, {}))
            },
            subdisposition=name,
        )
        for name, values in groups.items()
        if values
    ]


def _condensation_portions(
    terminal_species: Mapping[str, float],
    terminal_feedstock: Mapping[str, float],
    terminal_reagent: Mapping[str, float],
    terminal_origin_unattributed: Mapping[str, float],
    attribution_methods: Mapping[str, str],
    snapshots: tuple[Mapping[str, Any], ...],
    registry: Mapping[str, Any],
    origin_dust_mol_atoms: float,
) -> list[_Portion]:
    _validate_condensation_snapshots(
        snapshots,
        _CONDENSATION_ACCOUNT,
        registry,
    )
    cleanup_species, pooled_species = _campaign_retained_species(
        snapshots,
        _CONDENSATION_ACCOUNT,
        registry,
        origin_dust_mol_atoms,
    )
    cleanup_species = {
        species: min(amount, terminal_species.get(species, 0.0))
        for species, amount in cleanup_species.items()
        if min(amount, terminal_species.get(species, 0.0)) > 0.0
    }
    main_species = {
        species: max(0.0, amount - cleanup_species.get(species, 0.0))
        for species, amount in terminal_species.items()
        if amount - cleanup_species.get(species, 0.0) > 0.0
    }
    cleanup_feedstock, pooled_feedstock_elements = _campaign_retained_origin_atoms(
        snapshots,
        _CONDENSATION_ACCOUNT,
        "feedstock",
        origin_dust_mol_atoms,
    )
    cleanup_reagent, pooled_reagent_elements = _campaign_retained_origin_atoms(
        snapshots,
        _CONDENSATION_ACCOUNT,
        "reagent",
        origin_dust_mol_atoms,
    )
    (
        cleanup_origin_unattributed,
        pooled_origin_unattributed_elements,
    ) = _campaign_retained_origin_unattributed_atoms(
        snapshots,
        _CONDENSATION_ACCOUNT,
        origin_dust_mol_atoms,
    )
    pooled_elements = (
        {
            element
            for species in pooled_species
            for element in resolve_species_formula(species, registry).elements
        }
        | pooled_feedstock_elements
        | pooled_reagent_elements
        | pooled_origin_unattributed_elements
    )
    if (terminal_feedstock or terminal_reagent) and not snapshots and cleanup_species:
        raise OriginUnresolvedError(
            "condensation-train typed origin lacks campaign snapshots"
        )
    for element, amount in tuple(cleanup_feedstock.items()):
        cleanup_feedstock[element] = min(
            float(amount),
            float(terminal_feedstock.get(element, 0.0)),
        )
    for element, amount in tuple(cleanup_reagent.items()):
        cleanup_reagent[element] = min(
            float(amount),
            float(terminal_reagent.get(element, 0.0)),
        )
    for element, amount in tuple(cleanup_origin_unattributed.items()):
        cleanup_origin_unattributed[element] = min(
            float(amount),
            float(terminal_origin_unattributed.get(element, 0.0)),
        )
    main_reagent = {
        element: max(0.0, float(amount) - cleanup_reagent.get(element, 0.0))
        for element, amount in terminal_reagent.items()
        if float(amount) - cleanup_reagent.get(element, 0.0) > 0.0
    }
    main_feedstock = {
        element: max(0.0, float(amount) - cleanup_feedstock.get(element, 0.0))
        for element, amount in terminal_feedstock.items()
        if float(amount) - cleanup_feedstock.get(element, 0.0) > 0.0
    }
    main_origin_unattributed = {
        element: max(
            0.0,
            float(amount) - cleanup_origin_unattributed.get(element, 0.0),
        )
        for element, amount in terminal_origin_unattributed.items()
        if float(amount) - cleanup_origin_unattributed.get(element, 0.0) > 0.0
    }
    _assert_origin_split_closes(
        "condensation cleanup split",
        cleanup_species,
        cleanup_feedstock,
        cleanup_reagent,
        cleanup_origin_unattributed,
        registry,
    )
    _assert_origin_split_closes(
        "condensation main split",
        main_species,
        main_feedstock,
        main_reagent,
        main_origin_unattributed,
        registry,
    )
    result = []
    if cleanup_species:
        result.append(
            _Portion(
                account=_CONDENSATION_ACCOUNT,
                destination="cleanup_volatile_product",
                species_mol=cleanup_species,
                feedstock_atoms=cleanup_feedstock,
                reagent_atoms=cleanup_reagent,
                origin_unattributed_atoms=cleanup_origin_unattributed,
                attribution_method_by_element={
                    element: (
                        "pool_ratio"
                        if element in pooled_elements
                        else attribution_methods[element]
                    )
                    for element in set(cleanup_feedstock) | set(cleanup_reagent)
                },
                campaign_scope="C0/C0B",
            )
        )
    if main_species:
        result.append(
            _Portion(
                account=_CONDENSATION_ACCOUNT,
                destination="product_condensed",
                species_mol=main_species,
                feedstock_atoms=main_feedstock,
                reagent_atoms=main_reagent,
                origin_unattributed_atoms=main_origin_unattributed,
                attribution_method_by_element={
                    element: (
                        "pool_ratio"
                        if element in pooled_elements
                        else attribution_methods[element]
                    )
                    for element in set(main_feedstock) | set(main_reagent)
                },
                campaign_scope="main_sequence",
            )
        )
    return result


def _validate_condensation_snapshots(
    snapshots: tuple[Mapping[str, Any], ...],
    account: str,
    registry: Mapping[str, Any],
) -> None:
    for index, row in enumerate(snapshots):
        ledger = row.get("ledger")
        if not isinstance(ledger, Mapping):
            raise OriginUnresolvedError(
                f"malformed physical condensation snapshot {index}"
            )
        physical_species = _positive_species(ledger.get(account, {}))
        if not physical_species:
            continue
        physical_atoms = _element_atoms(physical_species, registry)

        tracked = row.get("material_origin_atom_moles_by_account")
        if not isinstance(tracked, Mapping):
            raise OriginUnresolvedError(
                f"missing typed-origin snapshot for nonzero {account}"
            )
        account_origins = tracked.get(account)
        if not isinstance(account_origins, Mapping):
            raise OriginUnresolvedError(
                f"malformed typed-origin snapshot for {account}"
            )
        unattributed_rows = row.get(
            "origin_unattributed_atom_moles_by_account"
        )
        if not isinstance(unattributed_rows, Mapping):
            raise OriginUnresolvedError(
                f"missing origin-unattributed snapshot for {account}"
            )
        account_unattributed = unattributed_rows.get(account)
        if not isinstance(account_unattributed, Mapping):
            raise OriginUnresolvedError(
                f"malformed origin-unattributed snapshot for {account}"
            )

        method_rows = row.get("origin_attribution_methods_by_account")
        if not isinstance(method_rows, Mapping):
            raise OriginUnresolvedError(
                f"missing attribution snapshot for nonzero {account}"
            )
        account_methods = method_rows.get(account)
        if not isinstance(account_methods, Mapping):
            raise OriginUnresolvedError(
                f"malformed attribution snapshot for {account}"
            )

        for element in set(physical_atoms) | set(account_origins):
            origins = account_origins.get(element)
            physical = float(physical_atoms.get(element, 0.0))
            if origins is None:
                origins = {}
            elif not isinstance(origins, Mapping):
                raise OriginUnresolvedError(
                    f"malformed typed-origin snapshot for {account}.{element}"
                )
            unknown_origins = {
                str(origin)
                for origin in origins
                if str(origin) not in {"feedstock", "reagent"}
            }
            if unknown_origins:
                raise OriginUnresolvedError(
                    f"unknown typed origins for {account}.{element}: "
                    f"{sorted(unknown_origins)!r}"
                )
            feedstock = _typed_origin_amount(
                origins.get("feedstock", 0.0),
                f"{account}.{element}.feedstock",
            )
            reagent = _typed_origin_amount(
                origins.get("reagent", 0.0),
                f"{account}.{element}.reagent",
            )
            origin_unattributed = _typed_origin_amount(
                account_unattributed.get(element, 0.0),
                f"{account}.{element}.origin_unattributed",
            )
            if not math.isclose(
                feedstock + reagent + origin_unattributed,
                physical,
                rel_tol=0.0,
                abs_tol=_atom_tolerance(physical),
            ):
                raise OriginUnresolvedError(
                    f"typed-origin snapshot does not close for "
                    f"{account}.{element}: feedstock={feedstock:.12g}, "
                    f"reagent={reagent:.12g}, "
                    f"origin_unattributed={origin_unattributed:.12g}, "
                    f"physical={physical:.12g} "
                    "mol-atoms"
                )
            if feedstock > 0.0 or reagent > 0.0:
                method = account_methods.get(element)
                if method not in {"tracked", "pool_ratio"}:
                    raise OriginUnresolvedError(
                        f"attribution snapshot unresolved for "
                        f"{account}.{element}"
                    )


def _assert_origin_split_closes(
    label: str,
    species_mol: Mapping[str, float],
    feedstock_atoms: Mapping[str, float],
    reagent_atoms: Mapping[str, float],
    origin_unattributed_atoms: Mapping[str, float],
    registry: Mapping[str, Any],
) -> None:
    physical_atoms = _element_atoms(species_mol, registry)
    for element in (
        set(physical_atoms)
        | set(feedstock_atoms)
        | set(reagent_atoms)
        | set(origin_unattributed_atoms)
    ):
        physical = float(physical_atoms.get(element, 0.0))
        feedstock = float(feedstock_atoms.get(element, 0.0))
        reagent = float(reagent_atoms.get(element, 0.0))
        origin_unattributed = float(origin_unattributed_atoms.get(element, 0.0))
        if not math.isclose(
            feedstock + reagent + origin_unattributed,
            physical,
            rel_tol=0.0,
            abs_tol=_atom_tolerance(physical),
        ):
            raise OriginUnresolvedError(
                f"{label} typed origin does not close for {element}: "
                f"feedstock={feedstock:.12g}, reagent={reagent:.12g}, "
                f"origin_unattributed={origin_unattributed:.12g}, "
                f"physical={physical:.12g} mol-atoms"
            )


def _campaign_retained_species(
    snapshots: tuple[Mapping[str, Any], ...],
    account: str,
    registry: Mapping[str, Any],
    origin_dust_mol_atoms: float,
) -> tuple[dict[str, float], set[str]]:
    return _campaign_retained_pool(
        snapshots,
        account,
        event_values=_pool_event_species,
        counter_values=lambda row, direction: _snapshot_pool_species(
            row,
            direction,
            account,
        ),
        tolerance_for_key=lambda species: (
            origin_dust_mol_atoms
            / math.fsum(
                resolve_species_formula(species, registry).elements.values()
            )
        ),
        poolable_keys=_CONDENSATION_AMALGAMATED_ELEMENTS,
        refusal_prefix="condensation withdrawal commingles cleanup/main",
    )


def _campaign_retained_origin_atoms(
    snapshots: tuple[Mapping[str, Any], ...],
    account: str,
    material_origin: str,
    origin_dust_mol_atoms: float,
) -> tuple[dict[str, float], set[str]]:
    return _campaign_retained_pool(
        snapshots,
        account,
        event_values=lambda event: _pool_event_origin(
            event,
            material_origin,
        ),
        counter_values=lambda row, direction: _snapshot_pool_origin(
            row,
            direction,
            account,
            material_origin,
        ),
        tolerance_for_key=lambda _element: origin_dust_mol_atoms,
        poolable_keys=_CONDENSATION_AMALGAMATED_ELEMENTS,
        refusal_prefix=(
            f"condensation {material_origin} withdrawal "
            "commingles cleanup/main"
        ),
    )


def _campaign_retained_origin_unattributed_atoms(
    snapshots: tuple[Mapping[str, Any], ...],
    account: str,
    origin_dust_mol_atoms: float,
) -> tuple[dict[str, float], set[str]]:
    return _campaign_retained_pool(
        snapshots,
        account,
        event_values=_pool_event_unattributed,
        counter_values=lambda row, direction: _snapshot_pool_unattributed(
            row,
            direction,
            account,
        ),
        tolerance_for_key=lambda _element: origin_dust_mol_atoms,
        poolable_keys=_CONDENSATION_AMALGAMATED_ELEMENTS,
        refusal_prefix="condensation unattributed withdrawal commingles cleanup/main",
        allow_origin_unattributed_withdrawal=True,
    )


def _campaign_retained_pool(
    snapshots: tuple[Mapping[str, Any], ...],
    account: str,
    *,
    event_values: Callable[[Mapping[str, Any]], dict[str, float]],
    counter_values: Callable[[Mapping[str, Any], str], dict[str, float]],
    tolerance_for_key: Callable[[str], float],
    poolable_keys: frozenset[str],
    refusal_prefix: str,
    allow_origin_unattributed_withdrawal: bool = False,
) -> tuple[dict[str, float], set[str]]:
    classes = ("cleanup", "main")
    cumulative_inputs = {name: defaultdict(float) for name in classes}
    cumulative_withdrawals = {name: defaultdict(float) for name in classes}
    pooled_keys: set[str] = set()
    previous_events: tuple[Mapping[str, Any], ...] = ()
    for row in snapshots:
        current_events = _snapshot_pool_events(row, account)
        if (
            len(current_events) < len(previous_events)
            or current_events[: len(previous_events)] != previous_events
        ):
            raise OriginUnresolvedError(
                "ordered gross pool event journal changed or decreased"
            )
        _assert_pool_event_counters_match(
            row,
            current_events,
            event_values,
            counter_values,
        )
        input_class = (
            "cleanup"
            if _campaign_name(row.get("campaign")) in _CLEANUP_CAMPAIGNS
            else "main"
        )
        for event in current_events[len(previous_events) :]:
            values = event_values(event)
            if event["direction"] == "inputs":
                for key, amount in values.items():
                    cumulative_inputs[input_class][key] += amount
                continue
            for key, withdrawal in values.items():
                tolerance = float(tolerance_for_key(key))
                balances = {
                    name: max(
                        0.0,
                        cumulative_inputs[name][key]
                        - cumulative_withdrawals[name][key],
                    )
                    for name in classes
                }
                live_classes = {
                    name
                    for name, amount in balances.items()
                    if amount > tolerance
                }
                if len(live_classes) > 1 and key not in poolable_keys:
                    raise OriginUnresolvedError(f"{refusal_prefix} {key}")
                allocation_balances = {
                    name: amount if name in live_classes else 0.0
                    for name, amount in balances.items()
                }
                available = math.fsum(allocation_balances.values())
                total_available = math.fsum(balances.values())
                if withdrawal > available and withdrawal <= total_available:
                    allocation_balances = balances
                    available = total_available
                if withdrawal > available and (
                    allow_origin_unattributed_withdrawal
                    or withdrawal - available <= tolerance
                ):
                    newly_unattributed = withdrawal - available
                    cumulative_inputs[input_class][key] += newly_unattributed
                    allocation_balances[input_class] += newly_unattributed
                allocation = allocate_pool_withdrawal(
                    allocation_balances,
                    withdrawal,
                    absolute_tolerance=tolerance,
                )
                if sum(amount > 0.0 for amount in allocation.values()) > 1:
                    pooled_keys.add(key)
                for name, amount in allocation.items():
                    cumulative_withdrawals[name][key] += amount
        previous_events = current_events
    retained = {
        key: max(
            0.0,
            cumulative_inputs["cleanup"][key]
            - cumulative_withdrawals["cleanup"][key],
        )
        for key in set(cumulative_inputs["cleanup"])
        | set(cumulative_withdrawals["cleanup"])
    }
    return (
        {
            key: amount
            for key, amount in retained.items()
            if amount > float(tolerance_for_key(key))
        },
        pooled_keys,
    )


def _pool_event_species(event: Mapping[str, Any]) -> dict[str, float]:
    values = event.get("species_mol")
    if not isinstance(values, Mapping):
        raise OriginUnresolvedError("malformed gross pool species event")
    return _positive_species(values)


def _pool_event_origin(
    event: Mapping[str, Any],
    material_origin: str,
) -> dict[str, float]:
    values = event.get("material_origin_atom_moles")
    if not isinstance(values, Mapping):
        raise OriginUnresolvedError("malformed gross pool origin event")
    return _element_mol_atoms(
        {
            element: origins.get(material_origin, 0.0)
            for element, origins in values.items()
            if isinstance(origins, Mapping)
        }
    )


def _pool_event_unattributed(
    event: Mapping[str, Any],
) -> dict[str, float]:
    values = event.get("origin_unattributed_atom_moles")
    if not isinstance(values, Mapping):
        raise OriginUnresolvedError(
            "malformed gross pool origin-unattributed event"
        )
    return _element_mol_atoms(values)


def _assert_pool_event_counters_match(
    row: Mapping[str, Any],
    events: tuple[Mapping[str, Any], ...],
    event_values: Callable[[Mapping[str, Any]], dict[str, float]],
    counter_values: Callable[[Mapping[str, Any], str], dict[str, float]],
) -> None:
    for direction, counter_name in (
        ("inputs", "gross_inputs"),
        ("withdrawals", "gross_withdrawals"),
    ):
        journal: defaultdict[str, float] = defaultdict(float)
        for event in events:
            if event["direction"] != direction:
                continue
            for key, amount in event_values(event).items():
                journal[key] += amount
        counters = counter_values(row, counter_name)
        for key in set(journal) | set(counters):
            recorded = float(counters.get(key, 0.0))
            expected = float(journal.get(key, 0.0))
            if not math.isclose(
                recorded,
                expected,
                rel_tol=0.0,
                abs_tol=_atom_tolerance(max(recorded, expected)),
            ):
                raise OriginUnresolvedError(
                    f"{counter_name} counter disagrees with ordered events "
                    f"for {key}: counter={recorded:.12g}, "
                    f"events={expected:.12g}"
                )


def _typed_origin_amount(value: Any, label: str) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise OriginUnresolvedError(
            f"invalid typed-origin snapshot balance {label}={value!r}"
        ) from exc
    if not math.isfinite(amount) or amount < -_ATOM_EPS:
        raise OriginUnresolvedError(
            f"invalid typed-origin snapshot balance {label}={value!r}"
        )
    return max(0.0, amount)


def _element_mol_atoms(values: Mapping[str, Any]) -> dict[str, float]:
    result = {}
    for element, raw_amount in values.items():
        amount = float(raw_amount)
        if not math.isfinite(amount) or amount < -_ATOM_EPS:
            raise OriginUnresolvedError(
                f"invalid reagent origin balance {element}={raw_amount!r} mol-atoms"
            )
        if amount > 0.0:
            result[str(element)] = amount
    return result


def _assert_reagent_cycle_closes(
    reagent_input: Mapping[str, float],
    reagent_terminal: Mapping[str, float],
    origin_dust_mol_atoms: float,
) -> None:
    for element in sorted(set(reagent_input) | set(reagent_terminal)):
        input_atoms = float(reagent_input.get(element, 0.0))
        terminal_atoms = float(reagent_terminal.get(element, 0.0))
        if input_atoms <= _ATOM_EPS:
            if terminal_atoms > _ATOM_EPS:
                raise OriginUnresolvedError(
                    f"terminal reagent-origin {element} has no reagent input"
                )
            continue
        # Same normalization as the feedstock gate, but over the separately
        # excluded reagent atom basis.
        difference = terminal_atoms - input_atoms
        residual = difference / input_atoms
        if (
            abs(difference) > origin_dust_mol_atoms
            and abs(residual) > _CLOSURE_LIMIT_FRACTION
        ):
            raise OriginUnresolvedError(
                f"reagent origin does not close for {element}: "
                f"input={input_atoms:.12g}, terminal={terminal_atoms:.12g}, "
                f"residual={residual:.12g} fraction"
            )


def _melt_subdisposition_rows(
    melt_atoms: Mapping[str, float],
    subdisposition_atoms: Mapping[str, Mapping[str, float]],
    subdisposition_methods: Mapping[str, Mapping[str, set[str]]],
) -> list[dict[str, Any]]:
    rows = []
    for element in sorted(melt_atoms):
        total = float(melt_atoms[element])
        amounts = {
            name: float(subdisposition_atoms[name].get(element, 0.0))
            for name in MELT_RETAINED_SUBDISPOSITIONS
        }
        residual = math.fsum(amounts.values()) - total
        if abs(residual) > _atom_tolerance(total):
            raise YieldDispositionError(
                f"melt-retained subdispositions do not close for {element}: "
                f"residual={residual:.12g} mol-atoms"
            )
        rows.append(
            {
                "element": element,
                "melt_retained_mol_atoms": total,
                "subdisposition_mol_atoms": amounts,
                "subdisposition_fractions": {
                    name: amount / total if total > 0.0 else 0.0
                    for name, amount in amounts.items()
                },
                "attribution_method": _merged_attribution_method(
                    method
                    for name in MELT_RETAINED_SUBDISPOSITIONS
                    for method in subdisposition_methods[name].get(element, set())
                ),
            }
        )
    return rows


def _stream_rows(
    portion: _Portion,
    registry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for species, mol in sorted(portion.species_mol.items()):
        formula = resolve_species_formula(species, registry)
        element_methods = {
            portion.attribution_method_by_element[element]
            for element in formula.elements
            if element in portion.attribution_method_by_element
        }
        unattributed_elements = {
            element
            for element in formula.elements
            if float(portion.origin_unattributed_atoms.get(element, 0.0)) > 0.0
        }
        if element_methods:
            attribution_method = _merged_attribution_method(element_methods)
            origin_scope = (
                "mixed_typed_and_unattributed_origin"
                if unattributed_elements
                else "typed_material_origin"
            )
        elif unattributed_elements:
            attribution_method = _ORIGIN_UNATTRIBUTED
            origin_scope = _ORIGIN_UNATTRIBUTED
        else:
            raise OriginUnresolvedError(
                f"stream origin missing for {portion.account}.{species}"
            )
        rows.append(
            {
                "destination": portion.destination,
                "account": portion.account,
                "campaign_scope": portion.campaign_scope,
                "subdisposition": portion.subdisposition,
                "species": species,
                "terminal_mol": mol,
                "terminal_kg": mol * formula.molar_mass_kg_per_mol(),
                "origin_scope": origin_scope,
                "attribution_method": attribution_method,
            }
        )
    return rows


def _allocate_origin_to_unique_groups(
    account: str,
    material_origin: str,
    origin_atoms: Mapping[str, float],
    group_atoms: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for element, amount in origin_atoms.items():
        candidates = [
            name
            for name, atoms in group_atoms.items()
            if float(atoms.get(element, 0.0)) > 0.0
        ]
        if len(candidates) == 1:
            result.setdefault(candidates[0], {})[element] = float(amount)
            continue
        physical = math.fsum(
            float(group_atoms[name].get(element, 0.0))
            for name in candidates
        )
        if candidates and math.isclose(
            float(amount),
            physical,
            rel_tol=0.0,
            abs_tol=_atom_tolerance(physical),
        ):
            for name in candidates:
                result.setdefault(name, {})[element] = float(
                    group_atoms[name][element]
                )
            continue
        if len(candidates) != 1:
            raise OriginUnresolvedError(
                f"{account}.{element} {material_origin} origin spans "
                f"{len(candidates)} disposition subgroups"
            )
    return result


def _element_atoms(
    species_mol: Mapping[str, float],
    registry: Mapping[str, Any],
) -> dict[str, float]:
    result: defaultdict[str, float] = defaultdict(float)
    for species, amount in species_mol.items():
        formula = resolve_species_formula(str(species), registry)
        for element, atom_mol in formula.atom_moles(float(amount)).items():
            result[element] += float(atom_mol)
    return dict(result)


def _positive_species(values: Any) -> dict[str, float]:
    if not isinstance(values, Mapping):
        return {}
    result = {}
    for species, raw_amount in values.items():
        amount = float(raw_amount)
        if not math.isfinite(amount):
            raise YieldDispositionError(
                f"non-finite terminal balance {species}={raw_amount!r}"
            )
        if amount > 0.0:
            result[str(species)] = amount
    return result


def _destination_for_account(account: str) -> str | None:
    if account in _ACCOUNT_TO_BIN:
        return _ACCOUNT_TO_BIN[account]
    if account == "process.wall_deposit" or account.startswith(_WALL_PREFIX):
        return "wall_deposit"
    return None


def _account_subdisposition(account: str) -> str | None:
    if account == "terminal.slag":
        return "stage0_slag"
    if account in {"process.solid_char_carbon", "process.spent_reductant_residue"}:
        return "residual_reductant"
    return None


def _campaign_name(value: Any) -> str:
    raw = getattr(value, "name", getattr(value, "value", value))
    return str(raw or "").upper()


def _merge_atoms(target: defaultdict[str, float], source: Mapping[str, float]) -> None:
    for element, amount in source.items():
        target[str(element)] += float(amount)


def _merged_attribution_method(methods: Iterable[str]) -> str:
    values = {str(method) for method in methods if str(method)}
    if "pool_ratio" in values:
        return "pool_ratio"
    if values == {"tracked"}:
        return "tracked"
    if not values:
        raise OriginUnresolvedError("attribution method set is empty")
    raise OriginUnresolvedError(
        f"unsupported attribution methods {sorted(values)!r}"
    )


def _sum_account_elements(
    values: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    result: defaultdict[str, float] = defaultdict(float)
    for account_values in values.values():
        for element, raw_amount in account_values.items():
            amount = _typed_origin_amount(
                raw_amount,
                f"origin_unattributed.{element}",
            )
            result[str(element)] += amount
    return dict(result)


def _cumulative_origin_unattributed(
    cumulative: Mapping[str, float],
    terminal: Mapping[str, float],
) -> dict[str, float]:
    result = {
        str(element): float(amount)
        for element, amount in cumulative.items()
        if float(amount) > 0.0
    }
    for element, amount in terminal.items():
        result[str(element)] = max(
            float(result.get(str(element), 0.0)),
            float(amount),
        )
    return {
        str(element): float(amount)
        for element, amount in result.items()
        if float(amount) > 0.0
    }


def _assert_origin_unattributed_within_limit(
    cumulative: Mapping[str, float],
    limit_mol_atoms: float,
) -> None:
    for element, raw_amount in sorted(cumulative.items()):
        amount = _typed_origin_amount(
            raw_amount,
            f"cumulative origin_unattributed.{element}",
        )
        if amount > limit_mol_atoms:
            raise OriginUnresolvedError(
                f"cumulative origin_unattributed exceeds attribution limit "
                f"for {element}: total={amount:.12g}, "
                f"limit={limit_mol_atoms:.12g} mol-atoms"
            )


def _full_atom_closure(
    terminal: Mapping[str, Mapping[str, float]],
    registry: Mapping[str, Any],
    feedstock_input: Mapping[str, float],
    reagent_input: Mapping[str, float],
    origin_unattributed_input: Mapping[str, float],
) -> tuple[list[dict[str, float | str]], float, float]:
    input_atoms: defaultdict[str, float] = defaultdict(float)
    for values in (
        feedstock_input,
        reagent_input,
        origin_unattributed_input,
    ):
        _merge_atoms(input_atoms, values)
    terminal_atoms: defaultdict[str, float] = defaultdict(float)
    for species_mol in terminal.values():
        _merge_atoms(
            terminal_atoms,
            _element_atoms(_positive_species(species_mol), registry),
        )

    rows: list[dict[str, float | str]] = []
    maximum_residual_fraction = 0.0
    maximum_residual_mol_atoms = 0.0
    for element in sorted(set(input_atoms) | set(terminal_atoms)):
        charged = float(input_atoms.get(element, 0.0))
        discharged = float(terminal_atoms.get(element, 0.0))
        difference = discharged - charged
        if charged <= _ATOM_EPS:
            if abs(difference) > _ATOM_EPS:
                raise YieldDispositionError(
                    f"full atom balance has terminal {element} without input: "
                    f"terminal={discharged:.12g} mol-atoms"
                )
            residual = 0.0
        else:
            residual = difference / charged
            if abs(residual) > _CLOSURE_LIMIT_FRACTION:
                raise YieldDispositionError(
                    f"full atom balance does not close for {element}: "
                    f"residual={residual:.12g} fraction, "
                    f"residual_mol_atoms={difference:.12g}"
                )
        maximum_residual_fraction = max(
            maximum_residual_fraction,
            abs(residual),
        )
        maximum_residual_mol_atoms = max(
            maximum_residual_mol_atoms,
            abs(difference),
        )
        rows.append(
            {
                "element": element,
                "input_mol_atoms": charged,
                "terminal_mol_atoms": discharged,
                "residual_fraction": residual,
                "residual_mol_atoms": difference,
            }
        )
    return rows, maximum_residual_fraction, maximum_residual_mol_atoms


def _origin_dust_tolerance(
    ledger: Any,
    feedstock_input: Mapping[str, float],
    reagent_input: Mapping[str, float],
) -> float:
    charged_atom_moles = math.fsum(
        max(0.0, float(amount))
        for values in (feedstock_input, reagent_input)
        for amount in values.values()
    )
    relative_tolerance = max(0.0, float(ledger.relative_tolerance))
    # At charged scale C, the established closure band is r*C mol-atoms; below
    # that band origin differences are indistinguishable from ledger roundoff.
    return relative_tolerance * charged_atom_moles


def _atom_tolerance(amount: float) -> float:
    return max(_ATOM_EPS, abs(float(amount)) * _CLOSURE_LIMIT_FRACTION)
