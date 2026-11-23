"""Bind integrated-bench observations to one simulator deck and cache the run.

Car A of b-518. This module does not emit residuals, does not apply a
default, and does not join rows by experiment id. A refusal here is a
disposition. A cached value is a whole-run projection or it is not stored.

The chemistry version for engine ``simulator`` lives in
``simulator.engine_cache``. Bump that version when a cached number
changes. The cache key is the runner's resolved configuration after the
one load, minus the exact metadata paths below. A new prediction field,
including a user-named curve, is in the key without a version bump.
Paths are matched exactly: not by name, prefix, or suffix. ``loaded_files``
is the sha256 of every file byte that load read. The runner file hash
and the preset digest field are provenance and stay out of the cache key.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simulator.condensation import LabGeometryError
from simulator.config import (
    begin_file_byte_record,
    finish_file_byte_record,
    note_loaded_file_bytes,
)
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.engine_cache import (
    EngineCacheValue,
    cached_engine_call,
    record_engine_open,
)
from simulator.process_timestep import resolve_process_timestep
from simulator.runner import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    NOT_APPLICABLE_UNTIL_P0,
    PyrolysisRun,
    RunnerError,
    _load_vacuum_preset_run_spec,
)
from simulator.thermal_budget import FURNACE_POWER_DRIVER_KEY
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

# Measurement tokens. A request that contains one has copied a result
# into the solve input. Provenance names are not matched: a dynamic
# curve may carry a citation, and that citation stays in the key.
FORBIDDEN_REQUEST_KEYS = frozenset({
    "comparison_points",
    "expected_value",
    "expected_values",
    "observation_id",
    "trial_id",
    "uncertainty",
})

# Exact schema paths omitted from the cache key, with the node and its
# subtree. A path not listed here is key material, whatever it is named.
# Over-keying misses; a wrong hit does not. Dynamic curve, profile, and
# surface ids are not listed.
METADATA_EXCLUSION_PATHS: dict[str, str] = {
    "/area_notice": "area-bridge display notice, not a melt input",
    "/preset/paper_id": "paper label",
    "/preset/paper_citation_id": "citation id",
    "/preset/measurement_id": "measurement label",
    "/preset/extraction_status": "extraction status label; the refusal gate reads it before the key",
    "/preset/source_notes": "source notes",
    "/preset/measurement_selectors": "observation selectors, not the solve",
    "/preset/comparison_contract": "comparison policy, sidecar path, and fast-tier policy",
    "/preset/digests": "provenance digests, including schedule_digest",
    "/preset/trajectory_comparison": "comparison grid and figure targets",
    "/preset/lab_geometry/sample/exposed_melt_area_notice": "geometry display notice",
    "/preset/lab_geometry/pump/model": "pump model name, not a solve input",
    "/preset/lab_geometry/pump/model_source_class": "pump model citation class",
    "/preset/lab_geometry/pump/model_citation_id": "pump model citation id",
    "/preset/lab_geometry/pump/model_digest": "pump model citation text",
    "/preset/lab_schedule/melt_temperature_C/0/source_class": "citation class of the initial melt knot",
    "/preset/lab_schedule/melt_temperature_C/0/citation_id": "citation id of the initial melt knot",
    "/preset/lab_schedule/melt_temperature_C/0/extraction_note": "citation note of the initial melt knot",
    "/schedule/melt_temperature_C/0/source_class": "citation class of the initial melt knot on the resolved schedule",
    "/schedule/melt_temperature_C/0/citation_id": "citation id of the initial melt knot on the resolved schedule",
    "/schedule/melt_temperature_C/0/extraction_note": "citation note of the initial melt knot on the resolved schedule",
    "/feedstock/entry/name": "feedstock display name",
    "/feedstock/entry/label": "feedstock display label",
    "/feedstock/entry/source": "feedstock citation source",
    "/feedstock/entry/confidence": "feedstock confidence label",
    "/feedstock/entry/note": "feedstock display note",
    "/feedstock/entry/paper_aliases": "feedstock paper aliases",
    "/feedstock/entry/provenance": "feedstock citation provenance",
    "/feedstock/entry/simulant_vs_real_caveat": "feedstock display caveat",
    "/feedstock/entry/process_notes": "feedstock process notes",
    "/feedstock/entry/composition_basis": "composition citation basis",
    "/feedstock/entry/composition_status": "composition status label",
    "/feedstock/entry/sum_check": "oxide sum cross-check, not an input mass",
    "/feedstock/entry/elemental_composition_wt_pct": "elemental cross-check, not the loadable oxide table",
    "/feedstock/entry/elemental_sum_check": "elemental sum cross-check",
    "/feedstock/entry/elemental_composition_status": "elemental cross-check status",
    "/feedstock/entry/elemental_cross_check_note": "elemental cross-check note",
}


def _json_pointer(path: tuple[Any, ...]) -> str:
    """JSON pointer for a resolved path. ``~`` and ``/`` in a key are escaped."""

    chunks: list[str] = []
    for step in path:
        text = str(step).replace("~", "~0").replace("/", "~1")
        chunks.append("/")
        chunks.append(text)
    return "".join(chunks)


def _canonicalize(value: Any, path: tuple[Any, ...] = ()) -> Any:
    """Sorted keys and the runner's numeric-string parse.

    A listed metadata path is omitted with its subtree. Every other
    field is kept, including a curve whose name looks like a note.
    """

    if isinstance(value, Mapping):
        packed: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            name = str(key)
            child = path + (name,)
            if _json_pointer(child) in METADATA_EXCLUSION_PATHS:
                continue
            packed[name] = _canonicalize(item, child)
        return packed
    if isinstance(value, (list, tuple)):
        return [
            _canonicalize(item, path + (index,))
            for index, item in enumerate(value)
        ]
    if isinstance(value, bool) or value is None:
        return value
    if is_declared_real_scalar(value, allow_numeric_str=True):
        number = float(value)
        if not math.isfinite(number):
            raise IntegratedBenchContractError(
                "resolved configuration contains a non-finite number",
                kind="no_deck",
                reason="non_numeric",
            )
        if number == 0.0:
            return 0.0
        return number
    if isinstance(value, str):
        return value
    raise IntegratedBenchContractError(
        f"resolved configuration cannot pack {type(value).__name__}",
        kind="no_deck",
        reason="non_numeric",
    )


def cache_key_document(resolved: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical key material for one resolved configuration."""

    document = _canonicalize(resolved)
    if not isinstance(document, dict):
        raise IntegratedBenchContractError(
            "resolved configuration is not a mapping",
            kind="no_deck",
            reason="non_numeric",
        )
    return document


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
    resolved_configuration: dict[str, Any]
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


def _point_lineage(raw_points: Sequence[Any], field: str) -> list[dict[str, str]]:
    """Label each raw knot. The watt curve and the numbers stay with the runner."""

    if isinstance(raw_points, (str, bytes)) or not isinstance(raw_points, Sequence):
        raise IntegratedBenchContractError(
            f"{field} is not a knot list",
            kind="not_a_measurement",
            reason="non_numeric",
        )
    lineage: list[dict[str, str]] = []
    count = len(raw_points)
    for index, raw in enumerate(raw_points):
        if not isinstance(raw, Mapping):
            raise IntegratedBenchContractError(
                f"{field}[{index}] is not a knot",
                kind="not_a_measurement",
                reason="non_numeric",
            )
        lineage.append({
            "input_role": _role(field, raw, index, count),
            "source_class": str(raw.get("source_class") or ""),
        })
    return lineage


def _schedule_lineage(raw_schedule: Mapping[str, Any]) -> dict[str, Any]:
    """Lineage only. The canonical schedule, including power_W, is the runner's.

    ``_resolved_power_schedule`` already reduced the trace. This walk does
    not parse the curve again and does not rebuild an input allowlist.
    """

    melt_lineage = _point_lineage(
        raw_schedule["melt_temperature_C"],
        "melt_temperature_C",
    )
    pressure_lineage = _point_lineage(
        raw_schedule["chamber_pressure_mbar"],
        "chamber_pressure_mbar",
    )
    surface_lineage: dict[str, Any] = {}
    raw_surfaces = raw_schedule.get("surface_temperature_C") or {}
    if isinstance(raw_surfaces, Mapping):
        for surface_id, raw_points in raw_surfaces.items():
            surface_lineage[str(surface_id)] = _point_lineage(
                raw_points,
                f"surface_temperature_C.{surface_id}",
            )
    robinot_schedule = str(raw_schedule.get("id") or "") == "robinot_2026_schedule"
    lineage: dict[str, Any] = {
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
    driver = raw_schedule.get(FURNACE_POWER_DRIVER_KEY)
    if isinstance(driver, Mapping):
        lineage[FURNACE_POWER_DRIVER_KEY] = "measured_driver"
    if isinstance(raw_schedule.get("pO2_cover"), Mapping):
        lineage["pO2_cover"] = "assumption"
    return lineage


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
        raw = path.read_bytes()
        note_loaded_file_bytes("preset", raw)
        document = load_cached_safe_yaml(raw) or {}
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
    vapor_pressures_path: Path | None = None,
) -> DeckRequest:
    """Build the request from the inputs the runner resolves.

    Comparison provenance is not attached, so the fast-tier comparison
    contract is not a numeric input and does not ban the analytical backend.
    The run is frozen before the load, and the key is that same object.
    """

    token = begin_file_byte_record()
    try:
        document = _read_preset_document(preset_path)
        _refuse_uncacheable_preset(document)
        spec = _load_vacuum_preset_run_spec(preset_path, leg)
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
            vapor_pressures_path=vapor_pressures_path,
            process_dt_s=spec.process_dt_s,
        )
        run.freeze_configuration()
        try:
            resolved = run.resolved_configuration(
                spec.document,
                leg=str(leg),
                mitigation=_mitigation_token(spec, leg),
            )
        except LabGeometryError as exc:
            raise IntegratedBenchContractError(
                str(exc),
                kind="missing_capability",
                reason=str(exc.code),
            ) from exc
        except RunnerError as exc:
            if not str(exc).startswith("feedstock "):
                raise
            raise IntegratedBenchContractError(
                str(exc),
                kind="no_deck",
                reason="no_usable_composition",
            ) from exc
        schedule_lineage = _schedule_lineage(run.lab_schedule)
    finally:
        loaded_files = finish_file_byte_record(token)
    resolved["loaded_files"] = loaded_files
    cache_request = cache_key_document(resolved)
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
    lineage: dict[str, Any] = {
        "feedstock": feedstock_lineage,
        "schedule": schedule_lineage,
    }
    if resolved["area_notice"] is not None:
        lineage["exposed_area_notice"] = resolved["area_notice"]
    digest = "sha256:" + hashlib.sha256(preset_path.read_bytes()).hexdigest()
    return DeckRequest(
        cache_request=cache_request,
        resolved_configuration=resolved,
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


def _project_hour_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Copy one hour. A present bad leaf is kept and marked malformed.

    An empty species map is valid. A nonnumeric rate such as ``O2: 'bad'``
    stays in the row; it is not dropped.
    """

    projected: dict[str, Any] = {"hour": float(row["hour"])}
    issue: str | None = None
    for key in _HOUR_NUMERIC:
        if key not in row:
            continue
        value = row.get(key)
        if _finite(value):
            projected[key] = float(value)
        else:
            projected[key] = value
            issue = "malformed_row"
    if "vapor_species_kg_hr" in row:
        species = row.get("vapor_species_kg_hr")
        if isinstance(species, Mapping):
            copied, clean = _copy_species_tree(species)
            projected["vapor_species_kg_hr"] = copied
            if not clean:
                issue = "malformed_row"
        elif species is not None:
            projected["vapor_species_kg_hr"] = species
            issue = "malformed_row"
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
    return projected, issue


def _project_step(row: Any) -> tuple[dict[str, Any], str | None]:
    """Project one step. A bad numeric payload stays a malformed row.

    An index-only row keeps its hour. It is not dropped and it is not
    promoted by discarding the missing payload. A present species rate
    that is not a finite number is kept on the row.
    """

    if (
        isinstance(row, Mapping)
        and row.get("status") == "malformed_row"
        and set(row).issubset({"status", "hour"})
    ):
        kept = {"status": "malformed_row"}
        if "hour" in row:
            kept["hour"] = row["hour"]
        return kept, "malformed_row"
    if not isinstance(row, Mapping) or not _finite(row.get("hour")):
        if isinstance(row, Mapping):
            return (
                {"status": "malformed_row", "hour": row.get("hour")},
                "malformed_row",
            )
        return {"status": "malformed_row"}, "malformed_row"
    projected, issue = _project_hour_row(row)
    if issue is not None:
        projected["status"] = "malformed_row"
        return projected, issue
    if not any(_finite(projected.get(key)) for key in _HOUR_NUMERIC):
        return projected, "index_only_row"
    return projected, None


def _copy_species_tree(
    value: Mapping[str, Any],
    depth: int = 0,
) -> tuple[dict[str, Any], bool]:
    """Copy a species map. An empty map is valid. A bad leaf is kept."""

    projected: dict[str, Any] = {}
    clean = True
    for name, item in value.items():
        if not isinstance(name, str) or " " in name or name in FORBIDDEN_REQUEST_KEYS:
            continue
        if _finite(item):
            projected[name] = float(item)
        elif depth < 2 and isinstance(item, Mapping):
            child, child_clean = _copy_species_tree(item, depth + 1)
            projected[name] = child
            clean = clean and child_clean
        else:
            projected[name] = item
            clean = False
    return dict(sorted(projected.items())), clean


def _project_final_state(state: Any) -> tuple[dict[str, Any], str | None]:
    """Ledger accounts. Valid when one account holds a finite mol.

    Absent or empty is ``missing_final_state``. A present leaf that is
    not a finite mol is ``malformed_row`` and the leaf is kept.
    """

    if not isinstance(state, Mapping) or not state:
        return {}, "missing_final_state"
    projected: dict[str, Any] = {}
    clean = True
    saw_finite = False
    for account, species in state.items():
        if not isinstance(account, str) or account in FORBIDDEN_REQUEST_KEYS:
            clean = False
            continue
        if not isinstance(species, Mapping):
            projected[account] = species
            clean = False
            continue
        mols, mols_clean = _copy_species_tree(species)
        projected[account] = mols
        clean = clean and mols_clean
        if mols_clean and any(_finite(mol) for mol in mols.values()):
            saw_finite = True
    projected = dict(sorted(projected.items()))
    if not clean:
        return projected, "malformed_row"
    if not saw_finite:
        return projected, "missing_final_state"
    return projected, None


def _project_final_report(final: Any) -> tuple[dict[str, Any], str | None]:
    """The runner's final report. Empty maps and the pump token count.

    Absent or empty is ``missing_final_report``. A present bad leaf is
    ``malformed_row`` and the leaf is kept.
    """

    if not isinstance(final, Mapping) or not final:
        return {}, "missing_final_report"
    projected: dict[str, Any] = {}
    clean = True
    for key in _FINAL_FIELDS:
        if key not in final:
            continue
        value = final[key]
        if value == NOT_APPLICABLE_UNTIL_P0:
            projected[key] = NOT_APPLICABLE_UNTIL_P0
            continue
        if isinstance(value, Mapping):
            child, child_clean = _copy_species_tree(value)
            projected[key] = child
            clean = clean and child_clean
            continue
        projected[key] = value
        clean = False
    if not clean:
        return projected, "malformed_row"
    if not projected:
        return projected, "missing_final_report"
    return projected, None


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

    ``ok`` is cacheable only when every requested step is present with a
    finite numeric payload, the runner's ``final_state`` holds a finite
    mol, and the final report is present. An index-only row, a bad
    numeric payload, an empty final, or a missing ledger is ``partial``
    with a typed reason. The same projection certifies a stored hit.
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
        return {
            "status": "missing",
            "per_hour_summary": [],
            "final_state": {},
            "final": {},
        }, False

    rows = document.get("per_hour_summary")
    projected_rows: list[dict[str, Any]] = []
    issues: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            projected, issue = _project_step(row)
            projected_rows.append(projected)
            if issue is not None:
                issues.append(issue)
    elif rows is not None:
        projected_rows.append({"status": "malformed_row"})
        issues.append("malformed_row")

    final_state, state_issue = _project_final_state(document.get("final_state"))
    final, report_issue = _project_final_report(document.get("final"))
    if state_issue == "malformed_row" or report_issue == "malformed_row":
        issues.append("malformed_row")

    status = document.get("status")
    status_text = str(status) if isinstance(status, str) else "missing"
    reason: str | None = None
    if status_text == _SUCCESS_STATUS:
        if "malformed_row" in issues:
            status_text = "partial"
            reason = "malformed_row"
        elif "index_only_row" in issues:
            status_text = "partial"
            reason = "index_only_row"
        else:
            actual = _row_hours(projected_rows)
            if actual != _expected_hours(requested_steps):
                status_text = "partial"
                reason = "incomplete_steps"
            elif state_issue is not None:
                status_text = "partial"
                reason = state_issue
            elif report_issue is not None:
                status_text = "partial"
                reason = report_issue

    hot: dict[str, Any] = {
        "status": status_text,
        "per_hour_summary": projected_rows,
        "final_state": final_state,
        "final": final,
    }
    if reason is not None:
        hot["reason"] = reason
    for key in _RUN_DIAGNOSTICS:
        if key in document:
            hot[key] = _copy_diagnostic(document.get(key))
    cacheable = reason is None and status_text == _SUCCESS_STATUS
    return hot, cacheable


def _validated_ok_document(result: Any, requested_steps: int) -> bool:
    """True when a returned document is a complete ``ok`` run.

    Hits of every status use this check. It is the store projection:
    an index-only row or an empty final is not success.
    """

    if not isinstance(result, Mapping) or result.get("status") != _SUCCESS_STATUS:
        return False
    if result.get("reason"):
        return False
    _hot, cacheable = project_whole_run(result, requested_steps=requested_steps)
    return cacheable


def run_cached_deck(
    deck: DeckRequest,
    solve: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> Any:
    """Solve once per packed request. Only a complete ``ok`` run is stored.

    The default solve is the deck's own ``PyrolysisRun.run``. A test may
    pass a stub. A hit of any status, including a row loaded from
    outside this process, is returned only when the store projection
    still calls it a complete ``ok``. A cold partial is returned and
    not stored.
    """

    def _execute(_request: Mapping[str, Any]) -> Any:
        return deck.run.run()

    solver = solve if solve is not None else _execute
    cold = {
        "preset_digest": deck.preset_digest,
    }
    solved = False

    def _solve() -> EngineCacheValue:
        nonlocal solved
        solved = True
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
    if not solved and not _validated_ok_document(result, deck.requested_steps):
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
