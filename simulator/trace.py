"""Structured in-process physics trace for optimizer scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Mapping

from simulator.accounting.queries import AccountingQueries, stage_purity
from simulator.state import PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX, HourSnapshot


def _freeze_mapping(values: Mapping[Any, Any]) -> Mapping[Any, Any]:
    return MappingProxyType(dict(values))


def _freeze_nested_mapping(
    values: Mapping[Any, Mapping[Any, Any]],
) -> Mapping[Any, Mapping[Any, Any]]:
    return MappingProxyType({
        key: _freeze_mapping(inner)
        for key, inner in values.items()
    })


@dataclass(frozen=True)
class PhysicsTrace:
    """Read-only scoring surface.

    Snapshot delta maps are per tick. Cumulative maps are running sums of those
    deltas or final ledger projections; gates consume deltas when timing matters.
    """

    snapshots: tuple[HourSnapshot, ...] = ()
    product_ledger_kg: Mapping[str, float] = field(default_factory=dict)
    terminal_rump_by_species_kg: Mapping[str, float] = field(default_factory=dict)
    terminal_rump_by_class_kg: Mapping[str, float] = field(default_factory=dict)
    oxygen_terminal_partition_kg: Mapping[str, float] = field(default_factory=dict)
    condensation_totals_kg: Mapping[str, float] = field(default_factory=dict)
    wall_deposit_by_segment_species_kg: Mapping[
        tuple[str, str], float] = field(default_factory=dict)
    wall_zone_by_segment: Mapping[str, str] = field(default_factory=dict)
    stage_purity_pct: Mapping[int, Mapping[str, float]] = field(
        default_factory=dict)
    condensed_by_stage_species_delta: tuple[
        Mapping[tuple[int, str], float], ...] = ()
    wall_deposit_by_segment_species_delta: tuple[
        Mapping[tuple[str, str], float], ...] = ()
    impurity_delta: tuple[Mapping[tuple[int, str], float], ...] = ()

    @classmethod
    def from_simulator(cls, sim: Any) -> "PhysicsTrace":
        queries = AccountingQueries(sim)
        snapshots = tuple(getattr(sim.record, "snapshots", ()))
        return cls(
            snapshots=snapshots,
            product_ledger_kg=_freeze_mapping(queries.product_ledger()),
            terminal_rump_by_species_kg=_freeze_mapping(
                queries.terminal_rump_by_species()),
            terminal_rump_by_class_kg=_freeze_mapping(
                queries.terminal_rump_by_class()),
            oxygen_terminal_partition_kg=_freeze_mapping(
                queries.oxygen_terminal_partition_kg()),
            condensation_totals_kg=_freeze_mapping(
                queries.condensation_totals_with_terminal_oxygen()),
            wall_deposit_by_segment_species_kg=(
                _freeze_mapping(wall_deposit_by_segment_species_kg(
                    sim.atom_ledger))),
            wall_zone_by_segment=_freeze_mapping(wall_zone_by_segment(sim)),
            stage_purity_pct=_freeze_nested_mapping(stage_purity(sim.train)),
            condensed_by_stage_species_delta=tuple(
                _freeze_mapping(snapshot.condensed_by_stage_species_delta)
                for snapshot in snapshots
            ),
            wall_deposit_by_segment_species_delta=tuple(
                _freeze_mapping(
                    snapshot.wall_deposit_by_segment_species_delta)
                for snapshot in snapshots
            ),
            impurity_delta=tuple(
                _freeze_mapping(snapshot.impurity_delta)
                for snapshot in snapshots
            ),
        )


def wall_deposit_by_segment_species_kg(ledger: Any) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    for account, species_kg in ledger.kg_by_account().items():
        if not str(account).startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):
            continue
        segment = str(account)[len(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX):]
        for species, kg in species_kg.items():
            amount = float(kg)
            if amount > 1e-12:
                result[(segment, species)] = amount
    return result


WALL_DEPOSIT_ZONE_NAMES = ("Hottest", "Hot", "Rest")


def wall_zone_by_segment(sim: Any) -> dict[str, str]:
    model = getattr(sim, "condensation_model", None)
    temperatures: dict[str, float] = {}
    for entry in tuple(getattr(model, "operating_history", ()) or ()):
        if not isinstance(entry, Mapping):
            continue
        segment_temperatures = entry.get("pipe_segment_temperatures_C", {}) or {}
        if not isinstance(segment_temperatures, Mapping):
            continue
        for segment, value in segment_temperatures.items():
            try:
                temperature = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(temperature):
                continue
            name = str(segment)
            temperatures[name] = max(temperature, temperatures.get(name, -math.inf))
    segments = tuple(getattr(model, "pipe_segments", ()) or ())
    for segment in segments:
        name = str(getattr(segment, "name", "") or "")
        if not name:
            continue
        if name in temperatures:
            continue
        try:
            temperature = float(getattr(segment, "wall_temperature_C"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(temperature):
            temperatures[name] = temperature
    return bucket_wall_segments_by_temperature(temperatures)


def bucket_wall_segments_by_temperature(
    temperatures_C: Mapping[str, float],
) -> dict[str, str]:
    finite: dict[str, float] = {}
    for segment, value in temperatures_C.items():
        try:
            temperature = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(temperature):
            finite[str(segment)] = temperature
    if not finite:
        return {}
    hottest = max(finite.values())
    coolest = min(finite.values())
    zones: dict[str, str] = {}
    for segment, temperature in finite.items():
        if temperature == hottest:
            zones[segment] = "Hottest"
        elif temperature == coolest:
            zones[segment] = "Rest"
        else:
            zones[segment] = "Hot"
    return zones


def wall_deposit_kg_by_zone_species(
    wall_deposit_by_segment_species: Mapping[tuple[str, str], float],
    zone_by_segment: Mapping[str, str],
) -> dict[str, dict[str, float]]:
    zone_totals: dict[str, dict[str, float]] = {
        zone: {} for zone in WALL_DEPOSIT_ZONE_NAMES
    }
    for key, kg in wall_deposit_by_segment_species.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("wall deposit key must be (segment, species)")
        segment, species = str(key[0]), str(key[1])
        if segment not in zone_by_segment:
            raise ValueError(f"missing wall zone for segment {segment}")
        zone = str(zone_by_segment[segment])
        if zone not in zone_totals:
            raise ValueError(f"unknown wall zone {zone!r} for segment {segment}")
        amount = float(kg)
        if amount <= 1e-12:
            continue
        species_totals = zone_totals[zone]
        species_totals[species] = species_totals.get(species, 0.0) + amount
    return {
        zone: dict(sorted(species_totals.items()))
        for zone, species_totals in zone_totals.items()
    }
