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
import inspect
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simulator.engine_cache import (
    EngineCacheValue,
    cached_engine_call,
    record_engine_open,
)
from simulator.lab_geometry import parse_lab_geometry
from simulator.lab_schedule import normalize_lab_schedule
from simulator.runner import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    _load_vacuum_preset_run_spec,
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

_PROCESS_DT_CALL = re.compile(
    r"apply_process_chamber_pressure\(\s*self\s*,\s*dt_s\s*=\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*\)"
)
_SUCCESS_STATUS = "ok"
_HOUR_FIELDS = ("hour", "O2_source_side_potential_kg_cumulative")
_FINAL_FIELDS = (
    "pump_outlet_by_species_kg",
    "wall_deposit_by_species_kg",
    "deposit_by_surface_species_kg",
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
    the role of each boundary (printed, conditional covariate, or assumption)
    so a later score can flag it after lookup. The same numbers stay one
    cache entry.
    """

    cache_request: dict[str, Any]
    lineage: dict[str, Any]
    preset_digest: str


@dataclass(frozen=True)
class BenchResidual:
    """A residual shape for the headline gate. This car does not construct one."""

    used_default: bool
    flag: str | None
    value: float


@dataclass(frozen=True)
class BenchScorePass:
    """One scoring pass over explicit decks. Residuals stay empty in this car."""

    residuals: tuple[BenchResidual, ...]
    defaults_applied: tuple[Any, ...]
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


def process_timestep_s() -> float:
    """Return the timestep the chamber-pressure step actually receives.

    The literal lives on ``PyrolysisSimulator._apply_teststand_chamber_pressure``.
    ``teststand._HOUR_S`` is an O2-counter conversion, not this step.
    """

    from simulator.core import PyrolysisSimulator

    source = inspect.getsource(
        PyrolysisSimulator._apply_teststand_chamber_pressure
    )
    matches = _PROCESS_DT_CALL.findall(source)
    if len(matches) != 1:
        raise IntegratedBenchContractError(
            "process timestep call site is not one literal",
            kind="missing_capability",
            reason="process_timestep_unresolved",
        )
    return float(matches[0])


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
    lineage = {
        "duration_h": "conditional_covariate",
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
    # A null exposed area is omitted. This car does not fill it.
    if _finite(sample.get("exposed_melt_area_m2")):
        projected_sample["exposed_melt_area_m2"] = _num(
            sample["exposed_melt_area_m2"], "sample.exposed_melt_area_m2"
        )
        sample_lineage["exposed_melt_area_m2"] = "printed_input"
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


def _oxide_vector(feedstocks: Mapping[str, Any], feedstock_id: str) -> dict[str, float]:
    try:
        entry = feedstocks[feedstock_id]
    except KeyError as exc:
        raise IntegratedBenchContractError(
            f"feedstock {feedstock_id!r} is not in the session bundle",
            kind="no_deck",
            reason="no_usable_composition",
        ) from exc
    raw = entry.get("composition_wt_pct") if isinstance(entry, Mapping) else None
    if not isinstance(raw, Mapping) or not raw:
        raise IntegratedBenchContractError(
            f"feedstock {feedstock_id!r} has no usable oxide vector",
            kind="no_deck",
            reason="no_usable_composition",
        )
    oxides: dict[str, float] = {}
    for name, value in raw.items():
        oxides[str(name)] = _num(value, f"composition_wt_pct.{name}")
    return dict(sorted(oxides.items()))


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
    )
    config = run._session_config()
    schedule, schedule_lineage = _project_schedule(
        spec.lab_schedule, spec.lab_geometry
    )
    geometry, geometry_lineage = _project_geometry(spec.lab_geometry)
    oxides = _oxide_vector(config.feedstocks, spec.feedstock_id)
    additives = {
        str(name): _num(amount, f"additives_kg.{name}")
        for name, amount in sorted(dict(config.additives_kg).items())
    }
    cache_request = {
        "feedstock": {
            "id": spec.feedstock_id,
            "composition_wt_pct": oxides,
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
            "effective_dt_s": process_timestep_s(),
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
    lineage = {
        "feedstock": "printed_input",
        "schedule": schedule_lineage,
        "geometry": geometry_lineage,
    }
    digest = "sha256:" + hashlib.sha256(preset_path.read_bytes()).hexdigest()
    return DeckRequest(
        cache_request=cache_request,
        lineage=lineage,
        preset_digest=digest,
    )


def _token_notices(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    notices = []
    for item in value:
        if isinstance(item, str) and item and " " not in item and len(item) <= 64:
            if item not in FORBIDDEN_REQUEST_KEYS:
                notices.append(item)
    return notices


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


def project_whole_run(document: Mapping[str, Any] | Any) -> tuple[dict[str, Any], bool]:
    """Project one finished run. A scalar or a failed run is not cacheable."""

    if not isinstance(document, Mapping):
        return {"status": "missing", "per_hour_summary": [], "final": {}}, False
    rows = document.get("per_hour_summary")
    final = document.get("final")
    projected_rows: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping) or not _finite(row.get("hour")):
                continue
            projected: dict[str, Any] = {
                "hour": float(row["hour"]),
            }
            for key in _HOUR_FIELDS[1:]:
                if _finite(row.get(key)):
                    projected[key] = float(row[key])
            species = row.get("vapor_species_kg_hr")
            if isinstance(species, Mapping):
                projected["vapor_species_kg_hr"] = _project_numeric_tree(species)
            projected_rows.append(projected)
    projected_final: dict[str, Any] = {}
    if isinstance(final, Mapping):
        for key in _FINAL_FIELDS:
            value = final.get(key)
            if isinstance(value, Mapping):
                projected_final[key] = _project_numeric_tree(value)
    status = document.get("status")
    hot: dict[str, Any] = {
        "status": str(status) if isinstance(status, str) else "missing",
        "per_hour_summary": projected_rows,
        "final": projected_final,
    }
    notices = _token_notices(document.get("notices"))
    if notices:
        hot["notices"] = notices
    cacheable = (
        hot["status"] == _SUCCESS_STATUS
        and bool(projected_rows)
        and isinstance(final, Mapping)
    )
    return hot, cacheable


def run_cached_deck(
    deck: DeckRequest,
    solve: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> Any:
    """Solve once per packed request. Partial and failed runs are not stored."""

    cold = {
        "preset_digest": deck.preset_digest,
    }

    def _solve() -> EngineCacheValue:
        document = solve(deck.cache_request)
        hot, cacheable = project_whole_run(document)
        identity = record_engine_open(SIMULATOR_ENGINE)
        if isinstance(identity, str):
            cold["runtime_identity"] = identity
        return EngineCacheValue(hot=hot, cold=dict(cold), cacheable=cacheable)

    record_engine_open(SIMULATOR_ENGINE)
    return cached_engine_call(SIMULATOR_ENGINE, deck.cache_request, _solve)


def clean_headline(
    residuals: Sequence[BenchResidual],
) -> tuple[BenchResidual, ...]:
    """Drop every defaulted residual from the clean headline.

    A default with no flag is a contract error. Removing the flag fails
    the caller. This car's pass has no residuals and no defaults.
    """

    clean: list[BenchResidual] = []
    for residual in residuals:
        if residual.used_default:
            if not residual.flag:
                raise IntegratedBenchContractError(
                    "a residual that used a default has no d-460 flag",
                    kind="unflagged_default",
                    reason="missing_flag",
                )
            continue
        clean.append(residual)
    return tuple(clean)


def score_bound_decks(
    observation_ids: Sequence[str],
    *,
    solve: Callable[[Mapping[str, Any]], Mapping[str, Any]],
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
    result = BenchScorePass(
        residuals=(),
        defaults_applied=(),
        dispositions=dispositions,
        requests=tuple(requests),
        projections=tuple(projections),
    )
    if result.defaults_applied or result.residuals:
        raise IntegratedBenchContractError(
            "integrated-bench car A emitted a residual or a default",
            kind="unflagged_default",
            reason="scorecard_delta",
        )
    return result
