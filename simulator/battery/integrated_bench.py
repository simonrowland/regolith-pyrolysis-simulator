"""Bind integrated-bench observations to one simulator deck and cache the run.

Car A of b-518. This module does not emit residuals, does not apply a
default, and does not join rows by experiment id. A refusal here is a
disposition. A cached value is a whole-run projection or it is not stored.

The chemistry version for engine ``simulator`` lives in
``simulator.engine_cache``. Bump that version when the projection, the
schedule interpretation, the process timestep, session-config resolution,
or nested melt chemistry changes a cached number. The runner file hash
and the preset digest are provenance and stay out of the cache key.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simulator.condensation import (
    LabGeometryError,
    read_gram_lab_exposed_melt_area,
)
from simulator.engine_cache import (
    EngineCacheValue,
    cached_engine_call,
    record_engine_open,
)
from simulator.lab_geometry import parse_lab_geometry
from simulator.lab_schedule import normalize_lab_schedule
from simulator.process_timestep import resolve_process_timestep
from simulator.runner import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    PyrolysisRun,
    _load_vacuum_preset_run_spec,
)
from simulator.thermal_budget import (
    FURNACE_POWER_DRIVER_KEY,
    _power_trace_curve_s,
)
from simulator.yaml_cache import load_cached_safe_yaml

SIMULATOR_ENGINE = "simulator"

ROBINOT_MAIN_TRIAL = "robinot_2026_main"
ROBINOT_MAIN_LINEAGE = "robinot_2026_main_gas_measurement"
ROBINOT_PRESET = "data/presets/vacuum_pyrolysis/robinot_2026.yaml"
POMEROY_PRESET = "data/presets/vacuum_pyrolysis/pomeroy_cardiff_2006.yaml"
SESKO_PRESET = "data/presets/vacuum_pyrolysis/sesko_2024.yaml"

_MASS_YIELD_ID = (
    "kems-044-robinot-2026::robinot_2026_o2_yield_measured"
    "::field:mass_yield_percent"
)
_FEED_OXYGEN_ID = (
    "kems-044-robinot-2026::robinot_2026_o2_yield_measured"
    "::field:fraction_of_feedstock_oxygen_percent"
)
_PARENT_GAS_ID = "kems-044-robinot-2026::robinot_2026_o2_yield_measured"
_DISTINCT_TRIAL_ID = "kems-044-robinot-2026::robinot_2026_o2_comparable_run_1p17"
_MODEL_VALUE_ID = "kems-044-robinot-2026::robinot_2026_o2_hsc_expected_1p37_model"
_POMEROY_ID = (
    "pomeroy_cardiff_2006_measurements:pomeroy_non_condensed_mass_loss_fraction"
)
_SESKO_IDS = (
    "kems-036-sesko-2024::sesko_2024_crucible_deposit_edx",
    "kems-036-sesko-2024::sesko_2024_deposit_Fe_edx_qualitative",
    "kems-036-sesko-2024::sesko_2024_deposit_K_edx_range",
    "kems-036-sesko-2024::sesko_2024_deposit_Si_edx_range",
    "kems-036-sesko-2024::sesko_2024_fig6_16_figure_only_blocked",
    "kems-036-sesko-2024::sesko_2024_table1_eac1_simulant_composition",
    "kems-036-sesko-2024::sesko_2024_table1_literature_feedstock_compositions",
    "kems-036-sesko-2024::sesko_2024_table2_factsage_and_literature_o2_yield",
)

# Comparison and provenance names. A request that contains one of these
# has copied a measurement into the solve input.
FORBIDDEN_REQUEST_KEYS = frozenset({
    "trajectory_comparison",
    "comparison_points",
    "comparison_contract",
    "expected_value",
    "expected_values",
    "measurement_selectors",
    "sticking_provenance",
    "source_notes",
    "extraction_note",
    "citation_id",
    "digest",
    "digests",
    "elemental_composition_wt_pct",
    "elemental_composition_status",
    "uncertainty",
    "observation_id",
    "observed",
    "sidecar",
    "provenance",
    "qualifier",
    "extraction_status",
    "source_ref",
    "input_role",
    "source_class",
    "basis",
    "lineage",
    "trial_id",
})

_SUCCESS_STATUS = "ok"
_HOUR_NUMERIC = (
    "O2_source_side_potential_kg_cumulative",
    "P_total_bar",
    "pO2_bar",
)
_FINAL_FIELDS = (
    "pump_outlet_by_species_kg",
    "wall_deposit_by_species_kg",
    "deposit_by_surface_species_kg",
)
_RUN_DIAGNOSTICS = (
    "degraded_path_engagement",
    "melt_redox_gate_floor_fallback_engagement",
    "condensation_refusals_by_species",
)
_WALL_AUTHORITY_KEYS = (
    "extrapolation_notices_by_species",
    "wall_deposit_sticking_authority",
)
_POWER_DRIVER_FIELDS = (
    "mass_kg",
    "exposed_area_m2",
    "emissivity",
    "pellet_diameter_m",
    "pellet_height_m",
    "focal_spot_diameter_m",
    "radiating_face_set",
    "solar_absorptance",
    "window_transmittance",
    "conductivity_W_m_K",
)


class IntegratedBenchContractError(ValueError):
    """The deck binding or the cache contract was violated."""

    def __init__(self, message: str, *, kind: str, reason: str) -> None:
        self.kind = kind
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class ObservationDisposition:
    """How one observation relates to a deck. This is not a residual."""

    observation_id: str
    kind: str
    reason: str
    basis: str | None = None
    trial_id: str | None = None
    lineage: str | None = None
    preset_relpath: str | None = None
    leg: str | None = None


@dataclass(frozen=True)
class DeckRequest:
    """Numeric solve inputs, plus lineage that does not enter the cache key.

    ``cache_request`` is what ``cached_engine_call`` packs. ``lineage`` names
    the role of each boundary so a later score can flag it after lookup.
    ``run`` is the runner object the solve calls. It is not packed.
    ``requested_steps`` is the step count ``drive_session`` executes.
    """

    cache_request: dict[str, Any]
    lineage: dict[str, Any]
    preset_digest: str
    run: PyrolysisRun
    requested_steps: int


@dataclass(frozen=True)
class BenchScorePass:
    """One scoring pass over explicit decks. This car emits no residuals."""

    dispositions: tuple[ObservationDisposition, ...]
    requests: tuple[DeckRequest, ...]
    projections: tuple[Any, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bound(
    observation_id: str,
    *,
    basis: str,
) -> ObservationDisposition:
    return ObservationDisposition(
        observation_id=observation_id,
        kind="bound",
        reason="bound",
        basis=basis,
        trial_id=ROBINOT_MAIN_TRIAL,
        lineage=ROBINOT_MAIN_LINEAGE,
        preset_relpath=ROBINOT_PRESET,
        leg="faithful",
    )


_DISPOSITIONS: dict[str, ObservationDisposition] = {
    _MASS_YIELD_ID: _bound(_MASS_YIELD_ID, basis="o2_mass_yield_fraction"),
    _FEED_OXYGEN_ID: _bound(
        _FEED_OXYGEN_ID, basis="feed_oxygen_extraction_fraction"
    ),
    _PARENT_GAS_ID: ObservationDisposition(
        observation_id=_PARENT_GAS_ID,
        kind="shared_lineage",
        reason="same_gas_measurement",
        lineage=ROBINOT_MAIN_LINEAGE,
    ),
    _DISTINCT_TRIAL_ID: ObservationDisposition(
        observation_id=_DISTINCT_TRIAL_ID,
        kind="no_deck",
        reason="distinct_trial",
    ),
    _MODEL_VALUE_ID: ObservationDisposition(
        observation_id=_MODEL_VALUE_ID,
        kind="not_a_measurement",
        reason="model_value",
    ),
    _POMEROY_ID: ObservationDisposition(
        observation_id=_POMEROY_ID,
        kind="missing_capability",
        reason="no_outlet_observable",
        preset_relpath=POMEROY_PRESET,
    ),
}
_DISPOSITIONS.update({
    observation_id: ObservationDisposition(
        observation_id=observation_id,
        kind="missing_capability",
        reason="transport_blocked",
        preset_relpath=SESKO_PRESET,
    )
    for observation_id in _SESKO_IDS
})


def classify_observation(observation_id: str) -> ObservationDisposition:
    """Return the row's deck disposition. Unknown rows are ``no_deck``."""

    known = _DISPOSITIONS.get(observation_id)
    if known is not None:
        return known
    return ObservationDisposition(
        observation_id=observation_id,
        kind="no_deck",
        reason="no_deck",
    )


def bound_observation_ids() -> tuple[str, ...]:
    return tuple(
        observation_id
        for observation_id, disposition in _DISPOSITIONS.items()
        if disposition.kind == "bound"
    )


def consumed_chamber_pressure_dt_s(run: PyrolysisRun) -> float:
    """Seconds ``apply_process_chamber_pressure`` receives for this run.

    Session start sets ``simulator.process_timestep`` from
    ``resolve_process_timestep(run.process_dt_s)``. The one-hour branch
    passes that same ``dt_s`` as the literal 3600. A shorter step passes
    ``process_timestep.dt_s``. Both are this value.
    """

    return resolve_process_timestep(run.process_dt_s).dt_s


def _num(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntegratedBenchContractError(
            f"{field} is not a number",
            kind="not_a_measurement",
            reason="non_numeric",
        )
    number = float(value)
    if not math.isfinite(number):
        raise IntegratedBenchContractError(
            f"{field} is not finite",
            kind="not_a_measurement",
            reason="non_numeric",
        )
    return number


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _role(field: str, raw: Mapping[str, Any], index: int, count: int) -> str:
    """Label one schedule knot from where the paper states it.

    Achieved chamber pressure, the terminal spot temperature, and an
    outcome-conditioned stop are results. They remain in the numeric
    request because the solve uses them, and the lineage calls them
    conditional covariates. An assumption source stays an assumption.
    This label is not chosen from a residual.
    """

    source = str(raw.get("source_class") or "")
    if field == "chamber_pressure_mbar":
        return "conditional_covariate"
    if field == "melt_temperature_C" and index == count - 1:
        return "conditional_covariate"
    if field.startswith("surface_temperature_C"):
        return "assumption"
    if "assumption" in source:
        return "assumption"
    return "printed_input"


def _project_points(
    raw_points: Sequence[Any],
    normalized_points: Sequence[Mapping[str, Any]],
    *,
    field: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if len(raw_points) != len(normalized_points):
        raise IntegratedBenchContractError(
            f"{field} lost a knot during schedule normalization",
            kind="missing_capability",
            reason="schedule_projection",
        )
    values: list[dict[str, Any]] = []
    lineage: list[dict[str, str]] = []
    count = len(raw_points)
    for index, (raw, normalized) in enumerate(zip(raw_points, normalized_points)):
        if not isinstance(raw, Mapping):
            raise IntegratedBenchContractError(
                f"{field}[{index}] is not a knot",
                kind="not_a_measurement",
                reason="non_numeric",
            )
        values.append({
            "t_h": _num(normalized["t_h"], f"{field}[{index}].t_h"),
            "value": _num(normalized["value"], f"{field}[{index}].value"),
            "unit": str(normalized["unit"]),
        })
        lineage.append({
            "input_role": _role(field, raw, index, count),
            "source_class": str(raw.get("source_class") or ""),
        })
    return values, lineage


def _gas_field(
    raw: Mapping[str, Any],
    *names: str,
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for name in names:
        value = raw.get(name)
        if _finite(value):
            projected[name] = _num(value, name)
        elif isinstance(value, str) and value.strip() and " " not in value:
            projected[name] = value.strip()
    return projected


def _project_power_driver(raw_schedule: Mapping[str, Any]) -> dict[str, Any] | None:
    """Pack the loader's power payload, not the comparison trace around it.

    ``melt_temperature_from_furnace_power`` reads the watt curve and the
    named driver fields. Uncertainty columns on the trace are not read.
    """

    driver = raw_schedule.get(FURNACE_POWER_DRIVER_KEY)
    if not isinstance(driver, Mapping):
        return None
    trace = driver.get("trace")
    if not isinstance(trace, Mapping):
        raise IntegratedBenchContractError(
            "furnace power driver is missing its trace",
            kind="missing_capability",
            reason="furnace_power_driver_malformed",
        )
    try:
        curve = _power_trace_curve_s(trace)
    except Exception as exc:
        raise IntegratedBenchContractError(
            f"furnace power trace is not a driver curve: {exc}",
            kind="missing_capability",
            reason="furnace_power_driver_malformed",
        ) from exc
    projected: dict[str, Any] = {
        "power_W": [
            {"t_s": float(time_s), "power_W": float(watts)}
            for time_s, watts in curve
        ],
    }
    for name in _POWER_DRIVER_FIELDS:
        value = driver.get(name)
        if value is None:
            projected[name] = None
        elif name == "radiating_face_set":
            projected[name] = str(value)
        else:
            projected[name] = _num(value, f"furnace_power_driver.{name}")
    return projected


def _project_schedule(
    raw_schedule: Mapping[str, Any],
    raw_geometry: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    geometry = parse_lab_geometry(raw_geometry, allow_temperature_profiles=True)
    profiles = tuple(
        surface.temperature_profile
        for surface in geometry.surfaces
        if surface.temperature_profile
    )
    normalized = normalize_lab_schedule(
        raw_schedule,
        required_surface_profiles=profiles,
    )
    melt, melt_lineage = _project_points(
        raw_schedule["melt_temperature_C"],
        normalized["melt_temperature_C"],
        field="melt_temperature_C",
    )
    pressure, pressure_lineage = _project_points(
        raw_schedule["chamber_pressure_mbar"],
        normalized["chamber_pressure_mbar"],
        field="chamber_pressure_mbar",
    )
    surfaces: dict[str, Any] = {}
    surface_lineage: dict[str, Any] = {}
    raw_surfaces = raw_schedule.get("surface_temperature_C") or {}
    for surface_id, normalized_points in normalized["surface_temperature_C"].items():
        raw_points = raw_surfaces[surface_id]
        values, roles = _project_points(
            raw_points,
            normalized_points,
            field=f"surface_temperature_C.{surface_id}",
        )
        surfaces[str(surface_id)] = values
        surface_lineage[str(surface_id)] = roles
    gas = normalized["gas_boundary"]
    windows = normalized["window_semantics"]
    schedule = {
        "duration_h": _num(normalized["duration_h"], "duration_h"),
        "interpolation": str(normalized["interpolation"]),
        "furnace_ceiling_C": _num(
            normalized["furnace_ceiling_C"], "furnace_ceiling_C"
        ),
        "melt_temperature_C": melt,
        "chamber_pressure_mbar": pressure,
        "surface_temperature_C": surfaces,
        "window_semantics": {
            "preheat_h": _num(windows["preheat_h"], "preheat_h"),
            "measured_window_start_h": _num(
                windows["measured_window_start_h"], "measured_window_start_h"
            ),
            "measured_window_end_h": _num(
                windows["measured_window_end_h"], "measured_window_end_h"
            ),
            "cooldown_h": _num(windows["cooldown_h"], "cooldown_h"),
            "deposit_sample_basis": str(windows["deposit_sample_basis"]),
        },
        "gas_boundary": {
            "background_gas": _gas_field(
                gas["background_gas"], "species", "mole_fraction"
            ),
            "imposed_flow": _gas_field(gas["imposed_flow"], "value", "unit"),
            "pressure_control": _gas_field(
                gas["pressure_control"], "mode", "pressure_driver"
            ),
        },
    }
    robinot_schedule = str(raw_schedule.get("id") or "") == "robinot_2026_schedule"
    lineage = {
        "duration_h": (
            {
                "input_role": "outcome_conditioned",
                "reason": "stop_selected_when_o2_production_ceased",
            }
            if robinot_schedule
            else "conditional_covariate"
        ),
        "interpolation": "assumption",
        "furnace_ceiling_C": "conditional_covariate",
        "melt_temperature_C": melt_lineage,
        "chamber_pressure_mbar": pressure_lineage,
        "surface_temperature_C": surface_lineage,
        "window_semantics": "conditional_covariate",
        "gas_boundary": {
            "background_gas": "printed_input",
            "imposed_flow": "conditional_covariate",
            "pressure_control": "assumption",
        },
    }
    if robinot_schedule:
        lineage["chamber_pressure_history"] = "assumed_constant_between_knots"
    power = _project_power_driver(raw_schedule)
    if power is not None:
        schedule[FURNACE_POWER_DRIVER_KEY] = power
        lineage[FURNACE_POWER_DRIVER_KEY] = "measured_driver"
    cover = raw_schedule.get("pO2_cover")
    if isinstance(cover, Mapping):
        schedule["pO2_cover"] = {
            "enabled": bool(cover.get("enabled", False)),
            "setpoint_mbar": _num(cover["setpoint_mbar"], "pO2_cover.setpoint_mbar"),
            "p_total_mbar": _num(cover["p_total_mbar"], "pO2_cover.p_total_mbar"),
            "effective_pO2_achieved_mbar": _num(
                cover["effective_pO2_achieved_mbar"],
                "pO2_cover.effective_pO2_achieved_mbar",
            ),
            "limited_by_total_pressure": bool(
                cover.get("limited_by_total_pressure", False)
            ),
        }
        lineage["pO2_cover"] = "assumption"
    return schedule, lineage


def _project_geometry(
    raw_geometry: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    sample = raw_geometry.get("sample") if isinstance(raw_geometry.get("sample"), Mapping) else {}
    projected_sample: dict[str, Any] = {}
    sample_lineage: dict[str, str] = {}
    for key in (
        "mass_g",
        "pellet_diameter_mm",
        "pellet_height_mm",
        "pressed_at_tons",
    ):
        if _finite(sample.get(key)):
            projected_sample[key] = _num(sample[key], f"sample.{key}")
            sample_lineage[key] = "printed_input"
    # The runner's area reader. Quoted numerics stay admitted. A null
    # gram-lab face is a refusal, not a filled default.
    try:
        area = read_gram_lab_exposed_melt_area(raw_geometry)
    except LabGeometryError as exc:
        raise IntegratedBenchContractError(
            str(exc),
            kind="missing_capability",
            reason=str(exc.code),
        ) from exc
    if area is not None:
        projected_sample["exposed_melt_area_m2"] = float(area)
        source = str(sample.get("exposed_melt_area_source_class") or "")
        sample_lineage["exposed_melt_area_m2"] = (
            "deck_declared_assumption" if "assumption" in source else "printed_input"
        )
    surfaces = []
    surface_lineage = []
    for surface in raw_geometry.get("surfaces") or ():
        if not isinstance(surface, Mapping):
            continue
        item: dict[str, Any] = {
            "id": str(surface.get("id") or ""),
            "apparatus_role": str(surface.get("role") or ""),
            "area_m2": _num(surface["area_m2"], "surface.area_m2"),
            "view_factor_from_melt": _num(
                surface["view_factor_from_melt"], "surface.view_factor_from_melt"
            ),
            "line_of_sight_to_melt": bool(surface["line_of_sight_to_melt"]),
        }
        if _finite(surface.get("equivalent_diameter_m")):
            item["equivalent_diameter_m"] = _num(
                surface["equivalent_diameter_m"], "surface.equivalent_diameter_m"
            )
        profile = str(surface.get("temperature_profile") or "").strip()
        if profile and " " not in profile:
            item["temperature_profile"] = profile
        surfaces.append(item)
        surface_lineage.append({
            "id": item["id"],
            "input_role": "assumption",
            "source_class": str(surface.get("source_class") or ""),
        })
    pump = raw_geometry.get("pump") if isinstance(raw_geometry.get("pump"), Mapping) else {}
    projected_pump: dict[str, Any] = {}
    pump_lineage: dict[str, str] = {}
    model = str(pump.get("model") or "").strip()
    if model:
        projected_pump["model"] = model
        pump_lineage["model"] = "printed_input"
    if _finite(pump.get("vacuum_limit_mbar")):
        projected_pump["vacuum_limit_mbar"] = _num(
            pump["vacuum_limit_mbar"], "pump.vacuum_limit_mbar"
        )
        pump_lineage["vacuum_limit_mbar"] = "printed_input"
    geometry = {
        "scale": str(raw_geometry.get("scale") or ""),
        "equipment_sizing": str(raw_geometry.get("equipment_sizing") or ""),
        "sample": projected_sample,
        "surfaces": surfaces,
        "pump": projected_pump,
    }
    lineage = {
        "sample": sample_lineage,
        "surfaces": surface_lineage,
        "pump": pump_lineage,
    }
    return geometry, lineage


def _resolved_feedstock_kg(
    feedstocks: Mapping[str, Any],
    feedstock_id: str,
    mass_kg: float,
) -> dict[str, float]:
    """The inventory's prediction-driving masses, not a second formula."""

    try:
        entry = feedstocks[feedstock_id]
    except KeyError as exc:
        raise IntegratedBenchContractError(
            f"feedstock {feedstock_id!r} is not in the session bundle",
            kind="no_deck",
            reason="no_usable_composition",
        ) from exc
    if not isinstance(entry, Mapping):
        raise IntegratedBenchContractError(
            f"feedstock {feedstock_id!r} has no usable composition",
            kind="no_deck",
            reason="no_usable_composition",
        )
    raw = entry.get("composition_wt_pct")
    if not isinstance(raw, Mapping) or not raw:
        raise IntegratedBenchContractError(
            f"feedstock {feedstock_id!r} has no usable oxide vector",
            kind="no_deck",
            reason="no_usable_composition",
        )
    from simulator.core import PyrolysisSimulator

    try:
        masses = PyrolysisSimulator.resolved_prediction_driving_feedstock_kg(
            entry, mass_kg
        )
    except (TypeError, ValueError) as exc:
        raise IntegratedBenchContractError(
            f"feedstock {feedstock_id!r} is not a usable composition: {exc}",
            kind="no_deck",
            reason="no_usable_composition",
        ) from exc
    if not masses:
        raise IntegratedBenchContractError(
            f"feedstock {feedstock_id!r} resolved to no component mass",
            kind="no_deck",
            reason="no_usable_composition",
        )
    return {str(name): float(kg) for name, kg in sorted(masses.items())}


def _mitigation_token(spec: Any, leg: str) -> str:
    pair = spec.document.get("pair")
    block = pair.get(leg) if isinstance(pair, Mapping) else None
    mitigation = block.get("mitigation") if isinstance(block, Mapping) else None
    if mitigation in (None, "", "none"):
        return "none"
    if isinstance(mitigation, Mapping) and "pO2_cover" in spec.lab_schedule:
        return "pO2_cover"
    raise IntegratedBenchContractError(
        f"leg {leg!r} has no resolved mitigation the runner applies",
        kind="missing_capability",
        reason="mitigation_unresolved",
    )


def _refuse_uncacheable_preset(document: Mapping[str, Any]) -> None:
    paper_id = str(document.get("paper_id") or "")
    status = str(document.get("extraction_status") or "")
    if paper_id == "pomeroy_cardiff_2006_vacuum_pyrolysis":
        raise IntegratedBenchContractError(
            "Pomeroy has no pump-outlet observable; the 10 g figure is not a 2006 charge",
            kind="missing_capability",
            reason="no_outlet_observable",
        )
    if (
        paper_id == "sesko_2024_vapor_phase_pyrolysis"
        or status == "condition_capture_p0b_transport_blocked"
    ):
        raise IntegratedBenchContractError(
            "Sesko transport is blocked and its duration is unprinted",
            kind="missing_capability",
            reason="transport_blocked",
        )


def _forbidden_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            child = f"{path}.{name}" if path else name
            if name in FORBIDDEN_REQUEST_KEYS:
                found.append(child)
            found.extend(_forbidden_paths(item, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_forbidden_paths(item, f"{path}[{index}]"))
    return found


def assert_cache_request_has_no_measurement(request: Mapping[str, Any]) -> None:
    found = _forbidden_paths(request)
    if found:
        raise IntegratedBenchContractError(
            "cache request contains measurement or lineage fields: "
            + ", ".join(found),
            kind="not_a_measurement",
            reason="measurement_in_request",
        )


def _read_preset_document(path: Path) -> Mapping[str, Any]:
    try:
        document = load_cached_safe_yaml(path.read_bytes()) or {}
    except (OSError, UnicodeDecodeError) as exc:
        raise IntegratedBenchContractError(
            f"preset {path} is unreadable",
            kind="no_deck",
            reason="preset_unreadable",
        ) from exc
    if not isinstance(document, Mapping):
        raise IntegratedBenchContractError(
            f"preset {path} is not a mapping",
            kind="no_deck",
            reason="preset_unreadable",
        )
    return document


def build_deck_request(
    preset_path: Path,
    *,
    leg: str = "faithful",
    feedstocks_path: Path | None = None,
) -> DeckRequest:
    """Build the allowlisted request from the inputs the runner resolves.

    Comparison provenance is not attached, so the fast-tier comparison
    contract is not a numeric input and does not ban the analytical backend.
    """

    document = _read_preset_document(preset_path)
    _refuse_uncacheable_preset(document)
    spec = _load_vacuum_preset_run_spec(preset_path, leg)
    from simulator.runner import PyrolysisRun

    run = PyrolysisRun(
        feedstock_id=spec.feedstock_id,
        campaign="C0",
        hours=spec.hours,
        mass_kg=spec.mass_kg,
        backend_name=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        setpoints_patch={"lab_geometry": spec.lab_geometry},
        lab_schedule=spec.lab_schedule,
        track="pyrolysis",
        feedstocks_path=feedstocks_path,
        process_dt_s=spec.process_dt_s,
    )
    config = run._session_config()
    schedule, schedule_lineage = _project_schedule(
        spec.lab_schedule, spec.lab_geometry
    )
    geometry, geometry_lineage = _project_geometry(spec.lab_geometry)
    component_kg = _resolved_feedstock_kg(
        config.feedstocks, spec.feedstock_id, float(config.mass_kg)
    )
    additives = {
        str(name): _num(amount, f"additives_kg.{name}")
        for name, amount in sorted(dict(config.additives_kg).items())
    }
    cache_request = {
        "feedstock": {
            "id": spec.feedstock_id,
            "resolved_component_kg": component_kg,
        },
        "session": {
            "campaign": str(config.campaign),
            "hours": int(config.hours),
            "mass_kg": _num(config.mass_kg, "mass_kg"),
            "backend_name": str(config.backend_name),
            "backend_policy": str(config.backend_policy.value),
            "track": str(config.track),
            "c5_enabled": bool(config.c5_enabled),
            "additives_kg": additives,
            "allow_fallback_vapor": bool(run.allow_fallback_vapor),
            "allow_unmeasured_alpha_fallback": bool(
                run.allow_unmeasured_alpha_fallback
            ),
            "force_builtin_vapor_pressure": bool(run.force_builtin_vapor_pressure),
            "high_t_melt_activity": str(config.high_t_melt_activity),
            "until_campaign_complete": bool(config.until_campaign_complete),
            "imcc_activity_shadow": bool(config.imcc_activity_shadow),
            "effective_dt_s": consumed_chamber_pressure_dt_s(run),
            "leg": str(leg),
            "mitigation": _mitigation_token(spec, leg),
            "mre_target_species": str(run.mre_target_species),
            "mre_max_voltage_V": _num(run.mre_max_voltage_V, "mre_max_voltage_V"),
            "campaigns_elapsed": _num(config.campaigns_elapsed, "campaigns_elapsed"),
        },
        "schedule": schedule,
        "geometry": geometry,
    }
    assert_cache_request_has_no_measurement(cache_request)
    if spec.feedstock_id == "lunar_eac_1a":
        feedstock_lineage: dict[str, Any] = {
            "input_role": "proxy",
            "reason": "eac_1a_proxy_for_robinot_charge",
            "registered_id": "lunar_eac_1a",
        }
    else:
        feedstock_lineage = {
            "input_role": "printed_input",
            "registered_id": spec.feedstock_id,
        }
    lineage = {
        "feedstock": feedstock_lineage,
        "schedule": schedule_lineage,
        "geometry": geometry_lineage,
    }
    digest = "sha256:" + hashlib.sha256(preset_path.read_bytes()).hexdigest()
    return DeckRequest(
        cache_request=cache_request,
        lineage=lineage,
        preset_digest=digest,
        run=run,
        requested_steps=int(run.hours),
    )


def _copy_diagnostic(value: Any) -> Any:
    """Keep a runner diagnostic. Measurement-target keys stay out."""

    if isinstance(value, Mapping):
        return {
            str(key): _copy_diagnostic(item)
            for key, item in value.items()
            if str(key) not in FORBIDDEN_REQUEST_KEYS
        }
    if isinstance(value, list):
        return [_copy_diagnostic(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if _finite(value):
        return float(value)
    if isinstance(value, str):
        return value
    return None


def _project_hour_row(row: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {"hour": float(row["hour"])}
    for key in _HOUR_NUMERIC:
        if _finite(row.get(key)):
            projected[key] = float(row[key])
    species = row.get("vapor_species_kg_hr")
    if isinstance(species, Mapping):
        projected["vapor_species_kg_hr"] = _project_numeric_tree(species)
    refusals = row.get("condensation_refusals_by_species")
    if isinstance(refusals, Mapping):
        projected["condensation_refusals_by_species"] = _copy_diagnostic(refusals)
    batch = row.get("vapour_batch_summary")
    if isinstance(batch, Mapping):
        metadata = batch.get("metadata")
        if isinstance(metadata, Mapping):
            kept = {
                key: _copy_diagnostic(metadata[key])
                for key in _WALL_AUTHORITY_KEYS
                if key in metadata
            }
            if kept:
                projected["vapour_batch_summary"] = {"metadata": kept}
    return projected


def _project_numeric_tree(value: Mapping[str, Any], depth: int = 0) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for name, item in value.items():
        if not isinstance(name, str) or " " in name or name in FORBIDDEN_REQUEST_KEYS:
            continue
        if _finite(item):
            projected[name] = float(item)
        elif depth < 2 and isinstance(item, Mapping):
            child = _project_numeric_tree(item, depth + 1)
            if child:
                projected[name] = child
    return dict(sorted(projected.items()))


def _expected_hours(requested_steps: int) -> list[float]:
    return [float(step) for step in range(1, requested_steps + 1)]


def _row_hours(rows: list[dict[str, Any]]) -> list[float] | None:
    """Hour numbers in order, or None when a row is not a finite hour."""

    hours: list[float] = []
    for row in rows:
        if row.get("status") == "malformed_row" or not _finite(row.get("hour")):
            return None
        hours.append(float(row["hour"]))
    return hours


def project_whole_run(
    document: Mapping[str, Any] | Any,
    *,
    requested_steps: int,
) -> tuple[dict[str, Any], bool]:
    """Project one finished run against the step count the deck executed.

    ``ok`` is cacheable only when that count is present in order, a final
    state is present, and no row was malformed. A document that claimed
    success and failed one of those checks is persisted as ``partial``
    with a typed reason. Runner diagnostics are copied on that path too.
    Omitting ``requested_steps`` cannot certify success.
    """

    if (
        isinstance(requested_steps, bool)
        or not isinstance(requested_steps, int)
        or requested_steps < 1
    ):
        raise IntegratedBenchContractError(
            "requested_steps must be the positive step count that was executed",
            kind="no_deck",
            reason="incomplete_steps",
        )
    if not isinstance(document, Mapping):
        return {"status": "missing", "per_hour_summary": [], "final": {}}, False

    rows = document.get("per_hour_summary")
    projected_rows: list[dict[str, Any]] = []
    malformed = False
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping) or not _finite(row.get("hour")):
                projected_rows.append({"status": "malformed_row"})
                malformed = True
                continue
            projected_rows.append(_project_hour_row(row))
    elif rows is not None:
        projected_rows.append({"status": "malformed_row"})
        malformed = True

    final = document.get("final")
    projected_final: dict[str, Any] = {}
    if isinstance(final, Mapping):
        for key in _FINAL_FIELDS:
            value = final.get(key)
            if isinstance(value, Mapping):
                projected_final[key] = _project_numeric_tree(value)

    status = document.get("status")
    status_text = str(status) if isinstance(status, str) else "missing"
    reason: str | None = None
    if status_text == _SUCCESS_STATUS:
        actual = _row_hours(projected_rows)
        if malformed or actual is None:
            status_text = "partial"
            reason = "malformed_row"
        elif not isinstance(final, Mapping):
            status_text = "partial"
            reason = "missing_final_state"
        elif actual != _expected_hours(requested_steps):
            status_text = "partial"
            reason = "incomplete_steps"

    hot: dict[str, Any] = {
        "status": status_text,
        "per_hour_summary": projected_rows,
        "final": projected_final,
    }
    if reason is not None:
        hot["reason"] = reason
    for key in _RUN_DIAGNOSTICS:
        if key in document:
            hot[key] = _copy_diagnostic(document.get(key))
    cacheable = reason is None and status_text == _SUCCESS_STATUS
    return hot, cacheable


def _validated_ok_document(result: Any, requested_steps: int) -> bool:
    """True when a returned ``ok`` document is the complete executed run."""

    if not isinstance(result, Mapping) or result.get("status") != _SUCCESS_STATUS:
        return False
    if result.get("reason"):
        return False
    rows = result.get("per_hour_summary")
    if not isinstance(rows, list):
        return False
    actual = _row_hours([row for row in rows if isinstance(row, Mapping)])
    if actual is None or len(rows) != len(actual):
        return False
    return (
        actual == _expected_hours(requested_steps)
        and isinstance(result.get("final"), Mapping)
    )


def run_cached_deck(
    deck: DeckRequest,
    solve: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> Any:
    """Solve once per packed request. Only a complete ``ok`` run is stored.

    The default solve is the deck's own ``PyrolysisRun.run``. A test may
    pass a stub. A cache hit is returned only when it is still that
    complete ``ok`` document.
    """

    def _execute(_request: Mapping[str, Any]) -> Any:
        return deck.run.run()

    solver = solve if solve is not None else _execute
    cold = {
        "preset_digest": deck.preset_digest,
    }

    def _solve() -> EngineCacheValue:
        document = solver(deck.cache_request)
        hot, cacheable = project_whole_run(
            document,
            requested_steps=deck.requested_steps,
        )
        identity = record_engine_open(SIMULATOR_ENGINE)
        if isinstance(identity, str):
            cold["runtime_identity"] = identity
        return EngineCacheValue(hot=hot, cold=dict(cold), cacheable=cacheable)

    record_engine_open(SIMULATOR_ENGINE)
    result = cached_engine_call(SIMULATOR_ENGINE, deck.cache_request, _solve)
    if isinstance(result, Mapping) and result.get("status") == _SUCCESS_STATUS:
        if not _validated_ok_document(result, deck.requested_steps):
            raise IntegratedBenchContractError(
                "a cache hit returned a document that is not a complete ok run",
                kind="no_deck",
                reason="cache_hit_not_ok",
            )
    return result


def score_bound_decks(
    observation_ids: Sequence[str],
    *,
    solve: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    repo_root: Path | None = None,
) -> BenchScorePass:
    """Group bound rows by trial and call the deck once. No residuals."""

    root = repo_root or _repo_root()
    dispositions = tuple(
        classify_observation(observation_id) for observation_id in observation_ids
    )
    groups: dict[str, ObservationDisposition] = {}
    for disposition in dispositions:
        if disposition.kind != "bound" or disposition.trial_id is None:
            continue
        previous = groups.get(disposition.trial_id)
        if previous is None:
            groups[disposition.trial_id] = disposition
            continue
        if (
            previous.preset_relpath != disposition.preset_relpath
            or previous.leg != disposition.leg
        ):
            raise IntegratedBenchContractError(
                f"trial {disposition.trial_id} has two decks",
                kind="no_deck",
                reason="trial_conflict",
            )
    requests: list[DeckRequest] = []
    projections: list[Any] = []
    for disposition in groups.values():
        deck = build_deck_request(
            root / str(disposition.preset_relpath),
            leg=str(disposition.leg or "faithful"),
        )
        requests.append(deck)
        projections.append(run_cached_deck(deck, solve))
    return BenchScorePass(
        dispositions=dispositions,
        requests=tuple(requests),
        projections=tuple(projections),
    )
