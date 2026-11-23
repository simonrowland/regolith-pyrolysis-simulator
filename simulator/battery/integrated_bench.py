"""Bind integrated-bench observations to one simulator deck and cache the run.

Car A of b-518. This module does not emit residuals, does not apply a
default, and does not join rows by experiment id. A refusal here is a
disposition. A cached value is a whole-run projection or it is not stored.

The chemistry version for engine ``simulator`` lives in
``simulator.engine_cache``. Bump that version when a cached number
changes. The cache key is the execution identity, not a walk of the
resolved configuration:

* git tree ids of ``simulator/``, ``engines/``, and ``data/`` at the captured commit
* sha256 of the deck file bytes, plus the leg selected from that file
* sha256 of ``engines/engines.local.toml`` (or ``absent``) and the
  active external-engine identities checked against their declarations
* the Python version

A dirty tree under those three paths does not read or write the cache.
An explicit feedstock, setpoints, or vapor path is not that tree, so it
does not read or write the cache either. A small resolved summary is
stored as provenance and is not key material.
"""

from __future__ import annotations

import hashlib
import math
import platform
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from simulator.condensation import LabGeometryError
from simulator.engine_cache import (
    EngineCacheValue,
    RuntimeIdentityUnknown,
    active_external_engine_identities,
    cached_engine_call,
    record_engine_open,
)
from simulator.process_timestep import resolve_process_timestep
from simulator.melt_backend import openimcc_bridge
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

# Measurement slots on a prediction document. They are schema locations,
# not names matched anywhere in a curve, profile, surface, or species id.
MEASUREMENT_SLOT_KEYS = (
    "measurement_id",
    "measurement_selectors",
    "comparison_contract",
    "trajectory_comparison",
    "comparison_points",
)

_TREE_PREFIXES = ("simulator", "engines", "data")
DECK_UNCACHED_DIRTY_TREE = "deck_uncached_dirty_tree"
DECK_UNCACHED_PATH_OVERRIDE = "deck_uncached_path_override"
DECK_UNCACHED_ENGINE_IDENTITY = "deck_uncached_engine_identity"


def _readonly(value: Any) -> Any:
    """A mapping the caller cannot mutate, including nested maps."""

    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _readonly(item) for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_readonly(item) for item in value)
    return value


def deck_cache_request(
    *,
    tree_ids: Mapping[str, str],
    preset_sha256: str,
    leg: str,
    engines_local_toml_sha256: str,
    external_engine_identities: Mapping[str, str],
    python_version: str,
) -> Mapping[str, Any]:
    """The lookup identity. Each argument is one part of the key."""

    missing = [name for name in _TREE_PREFIXES if name not in tree_ids]
    if missing:
        raise IntegratedBenchContractError(
            "deck cache identity is missing tree ids: " + ", ".join(missing),
            kind="no_deck",
            reason="non_numeric",
        )
    return _readonly({
        "trees": {name: str(tree_ids[name]) for name in _TREE_PREFIXES},
        "preset_sha256": str(preset_sha256),
        "leg": str(leg),
        "external_engines": {
            "engines_local_toml_sha256": str(engines_local_toml_sha256),
            "engine_identities": {
                str(name): str(external_engine_identities[name])
                for name in sorted(external_engine_identities)
            },
        },
        "python_version": str(python_version),
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
    """Frozen deck identity, plus lineage that does not enter the cache key.

    ``cache_request`` is the read-only lookup identity ``cached_engine_call``
    packs. ``lineage`` names the role of each boundary so a later score can
    flag it after lookup. ``run`` is the runner object the solve calls.
    ``requested_steps`` is the step count ``drive_session`` executes.
    ``source_commit`` supplies all tree ids and cleanliness checks.
    ``cache_bypass`` is a typed notice when this deck must not touch the cache.
    """

    cache_request: Mapping[str, Any]
    resolved_configuration: dict[str, Any]
    lineage: dict[str, Any]
    preset_digest: str
    source_commit: str
    run: PyrolysisRun
    requested_steps: int
    cache_bypass: str | None = None


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


def assert_measurement_slots_outside_prediction(document: Mapping[str, Any]) -> None:
    """Refuse a prediction that carries a measurement slot at its root.

    Slots are the fixed schema locations below. A dynamic id named
    ``uncertainty`` or ``target`` is not one of them.
    """

    found = [name for name in MEASUREMENT_SLOT_KEYS if name in document]
    if found:
        raise IntegratedBenchContractError(
            "prediction document contains measurement slots: " + ", ".join(found),
            kind="not_a_measurement",
            reason="measurement_in_prediction",
        )


def _git_text(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git failed"
        raise IntegratedBenchContractError(
            detail,
            kind="no_deck",
            reason=DECK_UNCACHED_DIRTY_TREE,
        )
    return completed.stdout.strip()


def _working_tree_clean_against(commit: str) -> bool:
    """Check tracked and untracked cache inputs against the captured commit."""

    try:
        return not any((
            _git_text(["diff", "--name-only", commit, "--", *_TREE_PREFIXES]),
            _git_text(["diff", "--cached", "--name-only", commit, "--", *_TREE_PREFIXES]),
            _git_text([
                "ls-files", "--others", "--exclude-standard", "--", *_TREE_PREFIXES
            ]),
        ))
    except IntegratedBenchContractError:
        return False


def _tree_ids(commit: str) -> dict[str, str]:
    return {
        name: _git_text(["rev-parse", f"{commit}:{name}"])
        for name in _TREE_PREFIXES
    }


def _engines_local_toml_sha256() -> str:
    path = _repo_root() / "engines" / "engines.local.toml"
    if not path.is_file():
        return "absent"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_preset_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except (OSError, UnicodeDecodeError) as exc:
        raise IntegratedBenchContractError(
            f"preset {path} is unreadable",
            kind="no_deck",
            reason="preset_unreadable",
        ) from exc


def _read_preset_document(path: Path, raw: bytes) -> Mapping[str, Any]:
    try:
        document = load_cached_safe_yaml(raw) or {}
    except UnicodeDecodeError as exc:
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
    setpoints_path: Path | None = None,
    vapor_pressures_path: Path | None = None,
) -> DeckRequest:
    """Build the frozen run and the read-only cache identity.

    Comparison provenance is not attached, so the fast-tier comparison
    contract is not a numeric input and does not ban the analytical backend.
    The run is frozen before the load. The lookup key is the tree, deck,
    engine, and Python identity captured with that freeze.
    """

    source_commit = _git_text(["rev-parse", "HEAD"])
    preset_bytes = _read_preset_bytes(preset_path)
    document = _read_preset_document(preset_path, preset_bytes)
    _refuse_uncacheable_preset(document)
    spec = _load_vacuum_preset_run_spec(
        preset_path,
        leg,
        raw_bytes=preset_bytes,
    )
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
        setpoints_path=setpoints_path,
        vapor_pressures_path=vapor_pressures_path,
        process_dt_s=spec.process_dt_s,
    )
    run.freeze_configuration()
    build_tree_clean = _working_tree_clean_against(source_commit)
    bypass: str | None = (
        None if build_tree_clean else DECK_UNCACHED_DIRTY_TREE
    )
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
    imported_commit = openimcc_bridge.imported_openimcc_commit()
    if imported_commit is None:
        engine_identities = {"openimcc": "unavailable"}
        if bypass is None:
            bypass = DECK_UNCACHED_ENGINE_IDENTITY
    else:
        engine_identities = active_external_engine_identities(
            SIMULATOR_ENGINE,
            active_identities={"openimcc": f"commit:{imported_commit}"},
        )
        if isinstance(engine_identities, RuntimeIdentityUnknown):
            engine_identities = {engine_identities.engine_id: "unavailable"}
            if bypass is None:
                bypass = DECK_UNCACHED_ENGINE_IDENTITY
    cache_request = deck_cache_request(
        tree_ids=_tree_ids(source_commit),
        preset_sha256=hashlib.sha256(preset_bytes).hexdigest(),
        leg=str(leg),
        engines_local_toml_sha256=_engines_local_toml_sha256(),
        external_engine_identities=engine_identities,
        python_version=platform.python_version(),
    )
    if bypass is None and any(
        path is not None
        for path in (
            run.feedstocks_path,
            run.setpoints_path,
            run.vapor_pressures_path,
        )
    ):
        bypass = DECK_UNCACHED_PATH_OVERRIDE
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
    digest = "sha256:" + hashlib.sha256(preset_bytes).hexdigest()
    return DeckRequest(
        cache_request=cache_request,
        resolved_configuration=resolved,
        lineage=lineage,
        preset_digest=digest,
        source_commit=source_commit,
        run=run,
        requested_steps=int(run.hours),
        cache_bypass=bypass,
    )


def _copy_diagnostic(value: Any) -> Any:
    """Keep a runner diagnostic. Names are not measurement tests."""

    if isinstance(value, Mapping):
        return {
            str(key): _copy_diagnostic(item)
            for key, item in value.items()
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
        else:
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


def _species_key_ok(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and name.strip() == name
        and " " not in name
    )


def _keep_evidence(value: Any) -> Any:
    """Copy a rejected leaf so the partial still shows what was wrong."""

    if isinstance(value, Mapping):
        return {
            (key if isinstance(key, str) else str(key)): _keep_evidence(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_keep_evidence(item) for item in value]
    return value


def _copy_species_tree(
    value: Mapping[str, Any],
    depth: int = 0,
    *,
    nested_ok: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Copy a species map. An empty map is valid. A bad leaf is kept.

    A flat rate field rejects a nested map. ``deposit_by_surface`` is a
    map of surface to species map: a number at the surface is malformed
    and is kept. ``depth`` is accepted so a caller can pass it through.
    """

    del depth
    projected: dict[str, Any] = {}
    clean = True
    for name, item in value.items():
        key = name if isinstance(name, str) else str(name)
        if not _species_key_ok(name):
            clean = False
        if nested_ok:
            if isinstance(item, Mapping):
                child, child_clean = _copy_species_tree(item, nested_ok=False)
                projected[key] = child
                clean = clean and child_clean
            else:
                projected[key] = float(item) if _finite(item) else item
                clean = False
            continue
        if _finite(item):
            projected[key] = float(item)
        elif isinstance(item, Mapping):
            projected[key] = _keep_evidence(item)
            clean = False
        else:
            projected[key] = item
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
        key = account if isinstance(account, str) else str(account)
        if not isinstance(account, str):
            projected[key] = _keep_evidence(species)
            clean = False
            continue
        if not isinstance(species, Mapping):
            projected[account] = species
            clean = False
            continue
        mols, mols_clean = _copy_species_tree(species)
        projected[key] = mols
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
        if (
            key == "pump_outlet_by_species_kg"
            and value == NOT_APPLICABLE_UNTIL_P0
        ):
            projected[key] = NOT_APPLICABLE_UNTIL_P0
            continue
        if value == NOT_APPLICABLE_UNTIL_P0 or value is None:
            projected[key] = value
            clean = False
            continue
        if isinstance(value, Mapping):
            child, child_clean = _copy_species_tree(
                value,
                nested_ok=(key == "deposit_by_surface_species_kg"),
            )
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
    try:
        assert_measurement_slots_outside_prediction(document)
    except IntegratedBenchContractError as exc:
        measurement_issue = exc.reason
    else:
        measurement_issue = None
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
    incoming = document.get("reason")
    incoming_text = (
        None
        if incoming in (None, "")
        else incoming if isinstance(incoming, str) else str(incoming)
    )
    if status_text == _SUCCESS_STATUS:
        if measurement_issue is not None:
            status_text = "partial"
            reason = measurement_issue
        elif "malformed_row" in issues:
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
        if status_text == _SUCCESS_STATUS and incoming_text is not None:
            status_text = "partial"
            reason = incoming_text
    elif incoming_text is not None:
        reason = incoming_text

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
    assert_measurement_slots_outside_prediction(hot)
    cacheable = reason is None and status_text == _SUCCESS_STATUS
    return hot, cacheable


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

    def _run_uncached(reason: str) -> Any:
        document = solver(deck.cache_request)
        hot, _cacheable = project_whole_run(
            document,
            requested_steps=deck.requested_steps,
        )
        hot["notice"] = reason
        return hot

    if deck.cache_bypass:
        return _run_uncached(deck.cache_bypass)
    session = deck.resolved_configuration.get("session")
    mitigation = session.get("mitigation") if isinstance(session, Mapping) else None
    cold = {
        "preset_digest": deck.preset_digest,
        "provenance": {
            "feedstock_id": deck.run.feedstock_id,
            "leg": deck.cache_request["leg"],
            "requested_steps": deck.requested_steps,
            "mitigation": mitigation,
        },
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
        if not _working_tree_clean_against(deck.source_commit):
            hot["notice"] = DECK_UNCACHED_DIRTY_TREE
            cacheable = False
        return EngineCacheValue(hot=hot, cold=dict(cold), cacheable=cacheable)

    record_engine_open(SIMULATOR_ENGINE)
    if not _working_tree_clean_against(deck.source_commit):
        return _run_uncached(DECK_UNCACHED_DIRTY_TREE)
    result = cached_engine_call(SIMULATOR_ENGINE, deck.cache_request, _solve)
    if not solved:
        hot, cacheable = project_whole_run(
            result,
            requested_steps=deck.requested_steps,
        )
        if not cacheable:
            raise IntegratedBenchContractError(
                "a cache hit returned a document that is not a complete ok run",
                kind="no_deck",
                reason="cache_hit_not_ok",
            )
        return hot
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
