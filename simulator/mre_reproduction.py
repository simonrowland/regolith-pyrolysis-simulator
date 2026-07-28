"""Intent-isolated literature reproduction for molten-regolith electrolysis."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from simulator.backends import (
    BackendSelectionPolicy,
    SimulatorBuildConfig,
    build_simulator,
    resolve_backend,
)
from simulator.diagnostic_helpers.reproduction_compare import content_digest
from simulator.state import Atmosphere, MOLAR_MASS


MRE_REPRODUCTION_SCHEMA_VERSION = "mre_reproduction.v1"
MRE_PRESET_SCHEMA_VERSION = "mre_reproduction_preset.v1"
MRE_REPRODUCTION_ORIGIN = "literature-reproduction"
PLANT_EXECUTION_ORIGIN = "plant"
_FORBIDDEN_PLANT_POLICY_FIELDS = frozenset(
    {
        "c5_enabled",
        "mre_target_species",
        "mre_max_voltage_V",
        "mre_voltage_ladder",
        "mre_voltage_sequence",
        "mre_minimum_hold_hours",
        "minimum_hold_hours",
        "min_hold_hours",
        "limited_c5_current_A",
        "baseline_mre_current_A",
        "c5_current_A",
        "mre_baseline_current_A",
    }
)


class MREReproductionError(ValueError):
    """Named refusal for invalid reproduction inputs or execution."""


@dataclass(frozen=True)
class MREReproductionInterval:
    start_h: float
    end_h: float
    dt_h: float
    applied_current_A: float
    applied_voltage_V: float
    temperature_C: float
    pO2_bar: float | None
    source_locator: Mapping[str, Any] | str
    pO2_status: str = "not_reported"

    def __post_init__(self) -> None:
        numeric = (
            self.start_h,
            self.end_h,
            self.dt_h,
            self.applied_current_A,
            self.applied_voltage_V,
            self.temperature_C,
        )
        if not all(math.isfinite(float(value)) for value in numeric):
            raise MREReproductionError("reproduction interval values must be finite")
        if self.start_h < 0.0 or self.end_h <= self.start_h:
            raise MREReproductionError("reproduction interval bounds are invalid")
        if not math.isclose(
            self.dt_h,
            self.end_h - self.start_h,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise MREReproductionError("reproduction interval dt_h must match bounds")
        if self.applied_current_A < 0.0 or self.applied_voltage_V < 0.0:
            raise MREReproductionError("reproduction electrical controls must be non-negative")
        if self.pO2_bar is not None and (
            not math.isfinite(float(self.pO2_bar)) or self.pO2_bar < 0.0
        ):
            raise MREReproductionError("reproduction pO2_bar must be non-negative")


@dataclass(frozen=True)
class PlantMREIntervalControl:
    dt_h: float
    applied_current_A: float
    applied_voltage_V: float
    temperature_C: float
    pO2_bar: float
    allowed_oxides: tuple[str, ...] | None = None
    c5_step_info: Mapping[str, Any] | None = None
    c5_rung_advanced: bool = False
    replay_state_before_dispatch: Mapping[str, Any] = field(default_factory=dict)
    diagnostic_state_before_dispatch: Mapping[str, Any] = field(default_factory=dict)
    source_locator: str = "plant_mre_policy"


@dataclass(frozen=True)
class MREReproductionProgram:
    case_id: str
    duration_h: float
    sampling_times_h: tuple[float, ...]
    current_program: Mapping[str, Any]
    voltage_program: tuple[Mapping[str, Any], ...]
    temperature_program: Mapping[str, Any]
    gas_boundary: Mapping[str, Any]
    cell_provenance: Mapping[str, Any]
    feedstock_id: str
    mass_kg: float
    paper_id: str
    paper_citation_id: str
    measurement_id: str
    controls_digest: str
    session_kind: str = "literature-reproduction"
    domain: str = "mre"

    def intervals(self) -> tuple[MREReproductionInterval, ...]:
        voltage_points = tuple(self.voltage_program)
        return tuple(
            MREReproductionInterval(
                start_h=start_h,
                end_h=end_h,
                dt_h=end_h - start_h,
                applied_current_A=float(self.current_program["current_A"]),
                applied_voltage_V=_linear_point_value(
                    voltage_points,
                    (start_h + end_h) / 2.0,
                ),
                temperature_C=float(self.temperature_program["temperature_C"]),
                pO2_bar=(
                    float(self.gas_boundary["runtime_inlet_pO2_bar"])
                    if self.gas_boundary.get("runtime_inlet_pO2_bar") is not None
                    else None
                ),
                pO2_status=str(
                    self.gas_boundary.get("runtime_inlet_pO2_status")
                    or "not_reported"
                ),
                source_locator={
                    "current": self.current_program["source_locator"],
                    "voltage": _point_source_locator(
                        voltage_points,
                        (start_h + end_h) / 2.0,
                    ),
                },
            )
            for start_h, end_h in zip(
                self.sampling_times_h,
                self.sampling_times_h[1:],
            )
        )


def load_mre_reproduction_program(
    preset: Mapping[str, Any],
    observations: Mapping[str, Any],
    case_id: str,
) -> MREReproductionProgram:
    """Strictly load one published MRE case without plant-policy surfaces."""

    recipe = _mapping(preset, "preset")
    source = _mapping(observations, "observations")
    if recipe.get("schema_version") != MRE_PRESET_SCHEMA_VERSION:
        raise MREReproductionError("malformed_mre_preset: unsupported schema_version")
    if recipe.get("preset_kind") != "mre_reproduction":
        raise MREReproductionError("malformed_mre_preset: preset_kind must be mre_reproduction")
    if recipe.get("execution_scope") != "literature_reproduction_only":
        raise MREReproductionError(
            "malformed_mre_preset: execution_scope must be "
            "literature_reproduction_only"
        )
    forbidden = sorted(_find_forbidden_plant_policy_fields(recipe))
    if forbidden:
        raise MREReproductionError(
            "mre_reproduction_forbids_plant_policy: " + ", ".join(forbidden)
        )
    if _contains_key(recipe, "expected_value"):
        raise MREReproductionError(
            "malformed_mre_preset: expected observations belong in the sidecar"
        )

    resolved_case = str(case_id or "").strip()
    cases = _mapping(recipe.get("cases"), "cases")
    if resolved_case not in cases:
        raise MREReproductionError(
            f"unknown_mre_reproduction_case: {resolved_case!r}; expected one of "
            + ", ".join(sorted(str(key) for key in cases))
        )
    case = _mapping(cases[resolved_case], f"cases.{resolved_case}")
    duration_h = _numeric_leaf(
        case.get("electrolysis_duration"),
        f"cases.{resolved_case}.electrolysis_duration",
        expected_unit="h",
    )
    if duration_h <= 0.0:
        raise MREReproductionError("electrolysis duration must be positive")

    paper_id = _required_text(recipe.get("paper_id"), "paper_id")
    paper_citation_id = _required_text(
        recipe.get("paper_citation_id"),
        "paper_citation_id",
    )
    measurement_id = _required_text(recipe.get("measurement_id"), "measurement_id")
    sources = _mapping(recipe.get("sources"), "sources")
    paper_source = _mapping(sources.get("paper"), "sources.paper")
    source_doi = _required_text(paper_source.get("doi"), "sources.paper.doi")
    if source_doi != "10.1016/j.actaastro.2025.06.028":
        raise MREReproductionError("malformed_mre_preset: unexpected Yu DOI")
    measurement = _mapping(
        _mapping(source.get("measurements"), "observations.measurements").get(
            measurement_id
        ),
        f"observations.measurements.{measurement_id}",
    )
    source_citation = _required_text(
        _mapping(measurement.get("paper_citation"), "paper_citation").get(
            "citation_id"
        ),
        "paper_citation.citation_id",
    )
    if source_citation != paper_citation_id:
        raise MREReproductionError(
            "mre preset paper_citation_id does not match observation source"
        )
    sidecar_doi = _required_text(
        _mapping(measurement.get("paper_citation"), "paper_citation").get("doi"),
        "paper_citation.doi",
    )
    if sidecar_doi != source_doi:
        raise MREReproductionError(
            "mre preset DOI does not match observation source"
        )

    feed = _mapping(recipe.get("feed"), "feed")
    feedstock_id = _required_text(feed.get("feedstock_id"), "feed.feedstock_id")
    mass_kg = _numeric_leaf(
        feed.get("sample_mass"),
        "feed.sample_mass",
        expected_unit="g",
    ) / 1000.0
    if not math.isclose(mass_kg, 0.020, rel_tol=0.0, abs_tol=1e-12):
        raise MREReproductionError("malformed_mre_preset: Yu sample mass must be 20 g")
    thermal = _mapping(recipe.get("thermal_program"), "thermal_program")
    temperature_C = _numeric_leaf(
        thermal.get("operational_temperature"),
        "thermal_program.operational_temperature",
        expected_unit="degC",
    )
    if not math.isclose(temperature_C, 1600.0, rel_tol=0.0, abs_tol=1e-12):
        raise MREReproductionError(
            "malformed_mre_preset: Yu operational temperature must be 1600 degC"
        )
    electrical = _mapping(recipe.get("electrical_program"), "electrical_program")
    if electrical.get("control_mode") != "constant_current":
        raise MREReproductionError(
            "malformed_mre_preset: Yu requires control_mode constant_current"
        )
    current_A = _numeric_leaf(
        electrical.get("applied_current"),
        "electrical_program.applied_current",
        expected_unit="A",
    )
    if not math.isclose(current_A, 0.5, rel_tol=0.0, abs_tol=1e-12):
        raise MREReproductionError("malformed_mre_preset: Yu applied current must be 0.5 A")
    trajectory_id = _required_text(
        _mapping(
            electrical.get("voltage_response_replay"),
            "electrical_program.voltage_response_replay",
        ).get("trajectory_id"),
        "electrical_program.voltage_response_replay.trajectory_id",
    )
    trajectories = _mapping(
        measurement.get("control_trajectories"),
        f"{measurement_id}.control_trajectories",
    )
    trajectory = _mapping(
        trajectories.get(trajectory_id),
        f"{measurement_id}.control_trajectories.{trajectory_id}",
    )
    if trajectory.get("role") != "published_measured_response_replay":
        raise MREReproductionError(
            "voltage replay must have role published_measured_response_replay"
        )
    voltage_case = _mapping(
        _mapping(trajectory.get("cases"), f"{trajectory_id}.cases").get(
            resolved_case
        ),
        f"{trajectory_id}.cases.{resolved_case}",
    )
    voltage_points = _validated_voltage_points(
        voltage_case.get("points"),
        duration_h=duration_h,
        field=f"{trajectory_id}.cases.{resolved_case}.points",
    )

    sampling = _mapping(recipe.get("sampling"), "sampling")
    max_interval_min = _numeric_leaf(
        sampling.get("max_interval_min"),
        "sampling.max_interval_min",
        expected_unit="min",
    )
    max_interval_h = max_interval_min / 60.0
    if max_interval_h <= 0.0:
        raise MREReproductionError("sampling.max_interval_min must be positive")
    observation_times = [
        _finite_float(value, "sampling.observation_times_h")
        for value in sampling.get("observation_times_h", ())
    ]
    event_grid = _event_grid(
        duration_h=duration_h,
        max_interval_h=max_interval_h,
        knots=(
            [float(point["time_h"]) for point in voltage_points]
            + observation_times
        ),
    )
    gas_boundary = _mapping(recipe.get("gas_boundary"), "gas_boundary")
    if gas_boundary.get("carrier_gas") != "Ar":
        raise MREReproductionError("malformed_mre_preset: Yu carrier gas must be Ar")
    carrier_flow_sccm = _numeric_leaf(
        gas_boundary.get("carrier_flow"),
        "gas_boundary.carrier_flow",
        expected_unit="sccm",
    )
    if not math.isclose(carrier_flow_sccm, 100.0, rel_tol=0.0, abs_tol=1e-12):
        raise MREReproductionError(
            "malformed_mre_preset: Yu carrier flow must be 100 sccm"
        )
    runtime_pO2 = _numeric_leaf(
        gas_boundary.get("runtime_inlet_pO2"),
        "gas_boundary.runtime_inlet_pO2",
        expected_unit="bar",
    )
    runtime_total_pressure = _numeric_leaf(
        gas_boundary.get("runtime_total_pressure"),
        "gas_boundary.runtime_total_pressure",
        expected_unit="bar",
    )
    if runtime_total_pressure <= 0.0 or runtime_pO2 > runtime_total_pressure:
        raise MREReproductionError(
            "gas boundary requires 0 <= pO2 <= total pressure"
        )
    current_program = {
        "control_mode": "constant_current",
        "current_A": current_A,
        "start_h": 0.0,
        "end_h": duration_h,
        "source_locator": _mapping(
            electrical.get("applied_current"),
            "electrical_program.applied_current",
        )["source_locator"],
    }
    temperature_program = {
        "temperature_C": temperature_C,
        "source_locator": _mapping(
            thermal.get("operational_temperature"),
            "thermal_program.operational_temperature",
        )["source_locator"],
    }
    gas_program = {
        **copy.deepcopy(dict(gas_boundary)),
        "runtime_inlet_pO2_bar": runtime_pO2,
        "runtime_total_pressure_bar": runtime_total_pressure,
        "runtime_inlet_pO2_status": _mapping(
            gas_boundary.get("runtime_inlet_pO2"),
            "gas_boundary.runtime_inlet_pO2",
        )["status"],
    }
    controls = {
        "case_id": resolved_case,
        "duration_h": duration_h,
        "current_program": current_program,
        "voltage_program": voltage_points,
        "temperature_program": temperature_program,
        "gas_boundary": gas_program,
        "sampling_times_h": event_grid,
    }
    return MREReproductionProgram(
        case_id=resolved_case,
        duration_h=duration_h,
        sampling_times_h=event_grid,
        current_program=current_program,
        voltage_program=voltage_points,
        temperature_program=temperature_program,
        gas_boundary=gas_program,
        cell_provenance=copy.deepcopy(
            dict(_mapping(recipe.get("cell"), "cell"))
        ),
        feedstock_id=feedstock_id,
        mass_kg=mass_kg,
        paper_id=paper_id,
        paper_citation_id=paper_citation_id,
        measurement_id=measurement_id,
        controls_digest=content_digest(controls),
    )


def run_mre_reproduction(
    program: MREReproductionProgram,
    *,
    feedstocks: Mapping[str, Any],
    setpoints: Mapping[str, Any],
    vapor_pressures: Mapping[str, Any],
    materials: Mapping[str, Any] | None,
    backend_name: str,
    backend_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute only ELECTROLYSIS_STEP intervals for a literature program."""

    if not isinstance(program, MREReproductionProgram):
        raise MREReproductionError("reproduction driver requires MREReproductionProgram")
    backend = resolve_backend(
        backend_name,
        BackendSelectionPolicy.RUNNER_STRICT,
        unavailable_error_cls=MREReproductionError,
        backend_config=backend_config,
        feedstock_id=program.feedstock_id,
        feedstocks=feedstocks,
    )
    sim = build_simulator(
        SimulatorBuildConfig(
            backend=backend,
            setpoints=setpoints,
            feedstocks=feedstocks,
            vapor_pressures=vapor_pressures,
            materials=materials,
        )
    )
    sim.load_batch(program.feedstock_id, mass_kg=program.mass_kg)
    sim.melt.temperature_C = float(program.temperature_program["temperature_C"])
    sim.melt.atmosphere = Atmosphere.ARGON_FLOW
    sim.melt.p_total_mbar = (
        float(program.gas_boundary["runtime_total_pressure_bar"]) * 1000.0
    )
    sim.melt.pO2_mbar = float(
        program.gas_boundary["runtime_inlet_pO2_bar"]
    ) * 1000.0
    sim.melt.update_total_mass()

    intervals: list[dict[str, Any]] = []
    for interval in program.intervals():
        sim.melt.temperature_C = interval.temperature_C
        sim.melt.pO2_mbar = float(interval.pO2_bar or 0.0) * 1000.0
        outcome = sim._execute_mre_interval(
            interval,
            execution_origin=MRE_REPRODUCTION_ORIGIN,
            sample_time_h=interval.end_h,
        )
        interval_row = {
            "start_h": interval.start_h,
            "end_h": interval.end_h,
            "dt_h": interval.dt_h,
            "applied_current_A": interval.applied_current_A,
            "applied_voltage_V": interval.applied_voltage_V,
            "temperature_C": interval.temperature_C,
            "pO2_bar": interval.pO2_bar,
            "pO2_status": interval.pO2_status,
            "applied_charge_C": interval.applied_current_A
            * interval.dt_h
            * 3600.0,
            "committed_electron_charge_C": outcome["committed_electron_charge_C"],
            "effective_current_A": outcome["effective_current_A"],
            "faradaic_efficiency_fraction": (
                outcome["committed_electron_charge_C"]
                / (interval.applied_current_A * interval.dt_h * 3600.0)
                if interval.applied_current_A > 0.0
                else 0.0
            ),
            "mre_anode_o2_delta_mol": outcome["mre_anode_o2_delta_mol"],
            "mre_anode_o2_delta_kg": outcome["mre_anode_o2_delta_kg"],
            "metals_delta_mol_by_species": outcome["metals_delta_mol_by_species"],
            "metals_delta_kg_by_species": outcome["metals_delta_kg_by_species"],
            "kernel_commit_disposition": outcome["kernel_commit_disposition"],
            "mass_balance_error_pct": outcome["mass_balance_error_pct"],
            "control_source_locator": copy.deepcopy(interval.source_locator),
        }
        intervals.append(interval_row)

    cumulative_metals_mol = _sum_species_rows(
        row["metals_delta_mol_by_species"] for row in intervals
    )
    cumulative_metals_kg = {
        species: mol * MOLAR_MASS[species] / 1000.0
        for species, mol in cumulative_metals_mol.items()
    }
    cumulative = {
        "applied_charge_C": sum(row["applied_charge_C"] for row in intervals),
        "committed_electron_charge_C": sum(
            row["committed_electron_charge_C"] for row in intervals
        ),
        "mre_anode_o2_mol": sum(
            row["mre_anode_o2_delta_mol"] for row in intervals
        ),
        "mre_anode_o2_kg": sum(
            row["mre_anode_o2_delta_kg"] for row in intervals
        ),
        "metals_mol_by_species": cumulative_metals_mol,
        "metals_kg_by_species": cumulative_metals_kg,
        "mass_balance_error_pct": (
            intervals[-1]["mass_balance_error_pct"] if intervals else 0.0
        ),
    }
    return {
        "status": "ok",
        "reason": "",
        "error_message": "",
        "run_metadata": {
            "execution_origin": MRE_REPRODUCTION_ORIGIN,
            "domain": "mre",
            "paper_id": program.paper_id,
            "case_id": program.case_id,
            "feedstock_id": program.feedstock_id,
            "mass_kg": program.mass_kg,
        },
        "mre_reproduction": {
            "schema_version": MRE_REPRODUCTION_SCHEMA_VERSION,
            "execution_origin": MRE_REPRODUCTION_ORIGIN,
            "case_id": program.case_id,
            "controls_digest": program.controls_digest,
            "temperature_C": float(program.temperature_program["temperature_C"]),
            "gas_boundary": {
                "carrier_gas": str(program.gas_boundary["carrier_gas"]),
                "carrier_flow_sccm": float(
                    program.gas_boundary["carrier_flow"]["value"]
                ),
                "runtime_total_pressure_bar": float(
                    program.gas_boundary["runtime_total_pressure_bar"]
                ),
                "runtime_total_pressure_status": str(
                    program.gas_boundary["runtime_total_pressure"]["status"]
                ),
                "runtime_inlet_pO2_bar": float(
                    program.gas_boundary["runtime_inlet_pO2_bar"]
                ),
                "runtime_inlet_pO2_status": str(
                    program.gas_boundary["runtime_inlet_pO2_status"]
                ),
            },
            "intervals": intervals,
            "cumulative": cumulative,
        },
    }


def _event_grid(
    *,
    duration_h: float,
    max_interval_h: float,
    knots: Sequence[float],
) -> tuple[float, ...]:
    values = [0.0, float(duration_h)]
    values.extend(
        float(value)
        for value in knots
        if 0.0 <= float(value) <= float(duration_h)
    )
    steps = int(math.ceil(duration_h / max_interval_h))
    values.extend(
        min(duration_h, index * max_interval_h)
        for index in range(1, steps + 1)
    )
    grid = tuple(sorted({round(value, 12) for value in values}))
    if any(right <= left for left, right in zip(grid, grid[1:])):
        raise MREReproductionError(
            "reproduction event grid must be strictly increasing"
        )
    # A sub-quantum duration rounds every event to 0.0 and yields a one-point
    # grid whose strict-increase check is vacuously true while intervals() is
    # empty — an accepted run that executes nothing. No physical paper run is
    # below the 1e-12 h grid quantum; refuse rather than silently no-op.
    if len(grid) < 2:
        raise MREReproductionError(
            "reproduction event grid collapsed to a single point: "
            f"duration_h={duration_h!r} is below the 1e-12 h grid quantum"
        )
    return grid


def _validated_voltage_points(
    value: Any,
    *,
    duration_h: float,
    field: str,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise MREReproductionError(f"{field} must be a sequence")
    points: list[dict[str, Any]] = []
    for index, raw_point in enumerate(value):
        point = _mapping(raw_point, f"{field}[{index}]")
        time_h = _finite_float(point.get("time_h"), f"{field}[{index}].time_h")
        voltage_V = _finite_float(
            point.get("voltage_V"),
            f"{field}[{index}].voltage_V",
        )
        if point.get("status") != "published_digitized":
            raise MREReproductionError(
                f"{field}[{index}].status must be published_digitized"
            )
        if point.get("unit") != "V":
            raise MREReproductionError(f"{field}[{index}].unit must be V")
        source_locator = point.get("source_locator")
        if not isinstance(source_locator, Mapping):
            raise MREReproductionError(
                f"{field}[{index}].source_locator must be a mapping"
            )
        points.append(
            {
                "time_h": time_h,
                "voltage_V": voltage_V,
                "unit": "V",
                "status": "published_digitized",
                "source_locator": copy.deepcopy(dict(source_locator)),
                "digitization_uncertainty_V": _finite_float(
                    point.get("digitization_uncertainty_V"),
                    f"{field}[{index}].digitization_uncertainty_V",
                ),
            }
        )
    if len(points) < 2:
        raise MREReproductionError(f"{field} requires at least two points")
    if any(
        right["time_h"] <= left["time_h"]
        for left, right in zip(points, points[1:])
    ):
        raise MREReproductionError(f"{field} times must be strictly increasing")
    if not math.isclose(points[0]["time_h"], 0.0, rel_tol=0.0, abs_tol=1e-9):
        raise MREReproductionError(f"{field} must start at 0 h")
    if not math.isclose(
        points[-1]["time_h"],
        duration_h,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise MREReproductionError(f"{field} must end at the case duration")
    return tuple(points)


def _linear_point_value(points: Sequence[Mapping[str, Any]], time_h: float) -> float:
    for left, right in zip(points, points[1:]):
        if float(left["time_h"]) <= time_h <= float(right["time_h"]):
            span = float(right["time_h"]) - float(left["time_h"])
            fraction = (time_h - float(left["time_h"])) / span
            return float(left["voltage_V"]) + fraction * (
                float(right["voltage_V"]) - float(left["voltage_V"])
            )
    return float(points[-1]["voltage_V"])


def _point_source_locator(
    points: Sequence[Mapping[str, Any]],
    time_h: float,
) -> Mapping[str, Any]:
    for point in reversed(points):
        if float(point["time_h"]) <= time_h:
            return copy.deepcopy(dict(point["source_locator"]))
    return copy.deepcopy(dict(points[0]["source_locator"]))


def _sum_species_rows(rows: Sequence[Mapping[str, float]] | Any) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        for species, value in row.items():
            totals[str(species)] = totals.get(str(species), 0.0) + float(value)
    return dict(sorted(totals.items()))


def _numeric_leaf(value: Any, field: str, *, expected_unit: str) -> float:
    leaf = _mapping(value, field)
    if leaf.get("unit") != expected_unit:
        raise MREReproductionError(f"{field}.unit must be {expected_unit}")
    if not str(leaf.get("status") or "").strip():
        raise MREReproductionError(f"{field}.status is required")
    if not isinstance(leaf.get("source_locator"), Mapping):
        raise MREReproductionError(f"{field}.source_locator must be a mapping")
    return _finite_float(leaf.get("value"), f"{field}.value")


def _find_forbidden_plant_policy_fields(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_PLANT_POLICY_FIELDS:
                found.add(key_text)
            found.update(_find_forbidden_plant_policy_fields(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            found.update(_find_forbidden_plant_policy_fields(item))
    return found


def _contains_key(value: Any, target: str) -> bool:
    if isinstance(value, Mapping):
        return target in value or any(
            _contains_key(item, target) for item in value.values()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_key(item, target) for item in value)
    return False


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MREReproductionError(f"{field} must be a mapping")
    return dict(value)


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MREReproductionError(f"{field} is required")
    return text


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MREReproductionError(f"{field} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MREReproductionError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise MREReproductionError(f"{field} must be finite")
    return number
