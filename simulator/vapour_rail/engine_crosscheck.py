"""Diagnostic vapour-rail cross-check against the VR-5 VapoRock pool.

This module measures disagreement; it does not calibrate, promote authority, or
apply an acceptance gate.  Every VapoRock call is made through the warm-pool
backend and is rejected before dispatch when the requested temperature lies
outside the externally validated 1350--1950 K envelope.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Final

import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.melt_backend.melt_envelope import (
    MELT_EXTRAPOLATION_ENVELOPE_FIELDS,
    consume_melt_extrapolation_envelope,
    has_melt_extrapolation_envelope,
    melt_extrapolation_diagnostic,
)
from simulator.melt_backend.vaporock import (
    VAPOROCK_T_MAX_K,
    VAPOROCK_T_MIN_K,
    VapoRockBackend,
    _parent_oxide_for_vaporock_gas,
    vaporock_speciation_is_live,
)
from simulator.silent_zero import ZeroBecause
from simulator.state import MOLAR_MASS
from simulator.vapour_rail.calibration import (
    DEFAULT_P_FLOOR_PA,
    open_warm_vaporock_backend,
    require_warm_pool_backend,
    temperature_grid_K,
)
from simulator.vapour_rail.catalog import (
    PressureObservable,
    compile_vapour_rail_catalog,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEEDSTOCK_PATH: Final[Path] = _REPO_ROOT / "data" / "feedstocks.yaml"
DEFAULT_VAPOR_PRESSURE_PATH: Final[Path] = (
    _REPO_ROOT / "data" / "vapor_pressures.yaml"
)
DEFAULT_FEEDSTOCK_ID: Final[str] = "lunar_mare_low_ti"
DEFAULT_FO2_LOG10_BAR: Final[tuple[float, ...]] = (-9.0, -8.0, -7.0)
DEFAULT_PRESSURE_BAR: Final[float] = 1.0e-6
REPORT_SCHEMA_VERSION: Final[int] = 2
_MELT_MODEL_ID: Final[str] = "MELTS-v1.0"


class EngineCrosscheckError(RuntimeError):
    """Raised when the diagnostic cross-check violates a hard contract."""


@dataclass(frozen=True)
class CrosscheckComposition:
    """One oxide composition presented identically to both pressure sources."""

    composition_id: str
    composition_wt_pct: Mapping[str, float]
    composition_mol: Mapping[str, float]


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _installed_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _repository_state() -> dict[str, Any]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=_REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return {"revision": "unavailable", "dirty": None}
    return {"revision": revision, "dirty": dirty}


def load_crosscheck_composition(
    feedstock_id: str = DEFAULT_FEEDSTOCK_ID,
    *,
    feedstock_path: Path = DEFAULT_FEEDSTOCK_PATH,
) -> CrosscheckComposition:
    """Load a canonical feedstock and convert its oxide wt% to an oxide-mol map."""

    payload = yaml.safe_load(feedstock_path.read_text()) or {}
    try:
        raw = payload[feedstock_id]["composition_wt_pct"]
    except (KeyError, TypeError) as exc:
        raise EngineCrosscheckError(
            f"feedstock {feedstock_id!r} has no composition_wt_pct"
        ) from exc
    if not isinstance(raw, Mapping):
        raise EngineCrosscheckError(
            f"feedstock {feedstock_id!r} composition_wt_pct must be a mapping"
        )

    wt_pct: dict[str, float] = {}
    mol: dict[str, float] = {}
    for oxide, value in raw.items():
        weight = float(value)
        if not math.isfinite(weight) or weight < 0.0:
            raise EngineCrosscheckError(
                f"{feedstock_id}.{oxide} weight must be finite and non-negative"
            )
        if weight == 0.0:
            continue
        if oxide not in MOLAR_MASS:
            raise EngineCrosscheckError(
                f"{feedstock_id}.{oxide} has no simulator molar mass"
            )
        wt_pct[str(oxide)] = weight
        mol[str(oxide)] = weight / float(MOLAR_MASS[oxide]) * 1000.0
    if not mol:
        raise EngineCrosscheckError(f"feedstock {feedstock_id!r} is empty")
    return CrosscheckComposition(feedstock_id, wt_pct, mol)


def validate_temperature_grid(temperatures_K: Sequence[float]) -> tuple[float, ...]:
    """Return a unique ordered grid wholly inside the validated VapoRock band."""

    values = tuple(float(value) for value in temperatures_K)
    if not values:
        raise EngineCrosscheckError("temperature grid must not be empty")
    if any(not math.isfinite(value) for value in values):
        raise EngineCrosscheckError("temperature grid values must be finite")
    if any(value < VAPOROCK_T_MIN_K or value > VAPOROCK_T_MAX_K for value in values):
        raise EngineCrosscheckError(
            "VapoRock cross-check temperatures must remain inside the validated "
            f"[{VAPOROCK_T_MIN_K:g}, {VAPOROCK_T_MAX_K:g}] K envelope"
        )
    if len(set(values)) != len(values):
        raise EngineCrosscheckError("temperature grid must not contain duplicates")
    return tuple(sorted(values))


def validate_fo2_grid(fo2_log10_bar: Sequence[float]) -> tuple[float, ...]:
    """Validate an explicit admitted fO2 grid used by both sources."""

    values = tuple(float(value) for value in fo2_log10_bar)
    if len(values) < 2:
        raise EngineCrosscheckError("fO2 slope grid requires at least two points")
    if any(not math.isfinite(value) for value in values):
        raise EngineCrosscheckError("fO2 grid values must be finite")
    if any(10.0**value < 1.0e-9 for value in values):
        raise EngineCrosscheckError(
            "fO2 grid falls below the live rail transport floor (1e-9 bar); "
            "no floor substitution is permitted"
        )
    if len(set(values)) != len(values):
        raise EngineCrosscheckError("fO2 grid must not contain duplicates")
    return tuple(sorted(values))


def load_rail_provider(
    *, vapor_pressure_path: Path = DEFAULT_VAPOR_PRESSURE_PATH
) -> tuple[BuiltinVaporPressureProvider, Mapping[str, Any]]:
    """Load the live builtin pressure provider and its schema-v2 catalog payload."""

    payload = yaml.safe_load(vapor_pressure_path.read_text()) or {}
    if not isinstance(payload, Mapping):
        raise EngineCrosscheckError("vapor pressure catalog must be a mapping")
    return BuiltinVaporPressureProvider(payload), payload


def declared_rail_pressure_species(catalog_payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Executable equilibrium-partial-pressure rows on the compiled rail."""

    catalog = compile_vapour_rail_catalog(
        catalog_payload,
        emit_u0_request_rules=False,
    )
    return tuple(
        sorted(
            species_id
            for species_id, species in catalog.species.items()
            # b-189-exempt: existence probe, no pressure read
            if species.evaluator is not None
            and species.pressure_observable
            is PressureObservable.EQUILIBRIUM_PARTIAL_PRESSURE
        )
    )


def _rail_request(
    *,
    composition_mol: Mapping[str, float],
    temperature_K: float,
    fo2_log10_bar: float,
    pressure_bar: float,
    process_phase: str | None = None,
) -> IntentRequest:
    oxygen_bar = 10.0**fo2_log10_bar
    control_inputs: dict[str, Any] = {
        "pO2_bar": oxygen_bar,
        "intrinsic_fO2_log": fo2_log10_bar,
    }
    if process_phase is not None:
        control_inputs["process_phase"] = process_phase
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": dict(composition_mol)},
            species_formula_registry={},
        ),
        temperature_C=temperature_K - 273.15,
        pressure_bar=pressure_bar,
        fO2_log=fo2_log10_bar,
        control_inputs=control_inputs,
    )


def _run_rail_cell(
    provider: Any,
    *,
    composition_mol: Mapping[str, float],
    temperature_K: float,
    fo2_log10_bar: float,
    pressure_bar: float,
    process_phase: str | None = None,
) -> dict[str, Any]:
    try:
        result = provider.dispatch(
            _rail_request(
                composition_mol=composition_mol,
                temperature_K=temperature_K,
                fo2_log10_bar=fo2_log10_bar,
                pressure_bar=pressure_bar,
                process_phase=process_phase,
            )
        )
    except Exception as exc:  # noqa: BLE001 -- recorded diagnostic refusal
        return {
            "status": "refused",
            "reason": f"{type(exc).__name__}: {exc}",
            "pressures_Pa": {},
            "sources": {},
            "activities": {},
        }

    status = str(getattr(result, "status", "unknown") or "unknown")
    diagnostic = dict(getattr(result, "diagnostic", None) or {})
    pressures = dict(diagnostic.get("vapor_pressures_Pa") or {})
    reason = None
    if status not in {"ok", "non_authoritative"}:
        reason = str(diagnostic.get("reason") or status)
        pressures = {}
    return {
        "status": status,
        "reason": reason,
        "pressures_Pa": pressures,
        "sources": dict(diagnostic.get("vapor_pressures_source") or {}),
        "activities": dict(diagnostic.get("activities") or {}),
    }


def _run_vaporock_cell(
    backend: VapoRockBackend,
    *,
    composition_mol: Mapping[str, float],
    temperature_K: float,
    fo2_log10_bar: float,
    pressure_bar: float,
) -> dict[str, Any]:
    require_warm_pool_backend(backend)
    if temperature_K < VAPOROCK_T_MIN_K or temperature_K > VAPOROCK_T_MAX_K:
        raise EngineCrosscheckError(
            f"refusing out-of-domain VapoRock call at {temperature_K:g} K"
        )
    computed_envelope = melt_extrapolation_diagnostic(
        float(temperature_K),
        _MELT_MODEL_ID,
    )
    try:
        result = backend.equilibrate(
            temperature_C=temperature_K - 273.15,
            composition_mol=dict(composition_mol),
            fO2_log=fo2_log10_bar,
            pressure_bar=pressure_bar,
        )
    except Exception as exc:  # noqa: BLE001 -- recorded diagnostic refusal
        return {
            **computed_envelope,
            "status": "refused",
            "reason": f"{type(exc).__name__}: {exc}",
            "warnings": [],
            "pressures_Pa": {},
        }
    status = str(getattr(result, "status", "unknown") or "unknown")
    warnings = list(getattr(result, "warnings", None) or [])
    pressures = dict(
        getattr(result, "vaporock_full_speciation_Pa", None)
        or getattr(result, "vapor_pressures_Pa", None)
        or {}
    )
    result_diagnostics = dict(getattr(result, "diagnostics", None) or {})
    envelope_source = (
        result_diagnostics
        if has_melt_extrapolation_envelope(result_diagnostics)
        else computed_envelope
    )
    consume_melt_extrapolation_envelope(
        envelope_source,
        temperature_K=float(temperature_K),
    )
    envelope = {
        field: envelope_source[field]
        for field in MELT_EXTRAPOLATION_ENVELOPE_FIELDS
    }
    reason = None
    # non_authoritative is pressure-authority, not completeness. Hollow
    # producer results carry empty_speciation_cause / not_converged.
    if not vaporock_speciation_is_live(status, result_diagnostics, pressures):
        if status in {"ok", "non_authoritative"}:
            status = "not_converged"
    if status not in {"ok", "non_authoritative"}:
        reason = "; ".join(str(item) for item in warnings) or status
        pressures = {}
    return {
        **envelope,
        "status": status,
        "instrument_status": envelope_source["instrument_status"],
        "reason": reason,
        "warnings": warnings,
        "pressures_Pa": pressures,
        "silent_zero_notes": list(
            result_diagnostics.get("silent_zero_notes") or []
        ),
    }


def _proven_empty_reason(
    species: str,
    silent_zero_notes: Sequence[Mapping[str, Any]] | None,
) -> str:
    token = ZeroBecause.PROVEN_EMPTY_INVENTORY.value
    for note in silent_zero_notes or ():
        if (
            str(note.get("species") or "") == species
            and note.get("zero_because") == token
            and note.get("detail")
        ):
            return str(note["detail"])
    parent = _parent_oxide_for_vaporock_gas(species) or "unknown_parent_oxide"
    return f"parent oxide {parent} absent from feedstock"


def _observation(
    pressures: Mapping[str, Any],
    species: str,
    *,
    cell_status: str,
    cell_reason: str | None,
    p_floor_Pa: float,
    silent_zero_notes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if cell_status not in {"ok", "non_authoritative"}:
        return {
            "kind": "refused",
            "pressure_Pa": None,
            "reason": cell_reason or f"cell_status={cell_status}",
        }
    if species not in pressures:
        return {"kind": "not_answered", "pressure_Pa": None, "reason": None}
    try:
        pressure = float(pressures[species])
    except (TypeError, ValueError):
        pressure = math.nan
    if not math.isfinite(pressure):
        return {
            "kind": "refused",
            "pressure_Pa": None,
            "reason": "non-finite or non-positive pressure",
        }
    if pressure == 0.0:
        return {
            "kind": ZeroBecause.PROVEN_EMPTY_INVENTORY.value,
            "pressure_Pa": 0.0,
            "reason": _proven_empty_reason(species, silent_zero_notes),
        }
    if pressure < 0.0:
        return {
            "kind": "refused",
            "pressure_Pa": None,
            "reason": "non-finite or non-positive pressure",
        }
    if pressure <= p_floor_Pa:
        return {
            "kind": "censored_sub_floor",
            "pressure_Pa": pressure,
            "reason": f"0 < P <= {p_floor_Pa:g} Pa",
        }
    return {"kind": "point", "pressure_Pa": pressure, "reason": None}


def _coverage_label(rail: Mapping[str, Any], vaporock: Mapping[str, Any]) -> str:
    rail_answered = rail["kind"] in {"point", "censored_sub_floor"}
    vaporock_answered = vaporock["kind"] in {"point", "censored_sub_floor"}
    if rail["kind"] == "point" and vaporock["kind"] == "point":
        return "matched_point"
    if rail_answered and vaporock_answered:
        return "matched_censored"
    if rail_answered:
        return "rail_answered_vaporock_not"
    if vaporock_answered:
        return "vaporock_answered_rail_not"
    return "neither_answered"


def _linear_slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    if denominator <= 0.0:
        return None
    return sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(xs, ys, strict=True)
    ) / denominator


def divergence_label(max_abs_delta_dex: float | None) -> str:
    """Descriptive magnitude band only; never an acceptance verdict."""

    if max_abs_delta_dex is None:
        return "no_matched_points"
    if max_abs_delta_dex >= 2.0:
        return "wild_ge_2_dex"
    if max_abs_delta_dex >= 1.0:
        return "large_1_to_2_dex"
    if max_abs_delta_dex >= 0.5:
        return "material_0_5_to_1_dex"
    return "under_0_5_dex"


def _summarize_species(
    species: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    reference_fo2_log10_bar: float,
) -> dict[str, Any]:
    matched = [row for row in rows if row["coverage"] == "matched_point"]
    deltas = [float(row["delta_log10_rail_minus_vaporock_dex"]) for row in matched]
    max_row = max(
        matched,
        key=lambda row: abs(float(row["delta_log10_rail_minus_vaporock_dex"])),
        default=None,
    )

    slopes: list[dict[str, Any]] = []
    by_temperature: dict[float, list[Mapping[str, Any]]] = defaultdict(list)
    for row in matched:
        by_temperature[float(row["temperature_K"])].append(row)
    for temperature_K, temperature_rows in sorted(by_temperature.items()):
        if len(temperature_rows) < 2:
            continue
        ordered = sorted(temperature_rows, key=lambda row: row["fo2_log10_bar"])
        xs = [float(row["fo2_log10_bar"]) for row in ordered]
        rail_logs = [math.log10(float(row["rail"]["pressure_Pa"])) for row in ordered]
        vaporock_logs = [
            math.log10(float(row["vaporock"]["pressure_Pa"])) for row in ordered
        ]
        rail_slope = _linear_slope(xs, rail_logs)
        vaporock_slope = _linear_slope(xs, vaporock_logs)
        slopes.append(
            {
                "temperature_K": temperature_K,
                "n_points": len(ordered),
                "rail_dlog10P_dlog10fO2": rail_slope,
                "vaporock_dlog10P_dlog10fO2": vaporock_slope,
                "slope_difference_rail_minus_vaporock": (
                    rail_slope - vaporock_slope
                    if rail_slope is not None and vaporock_slope is not None
                    else None
                ),
            }
        )

    reference_rows = [
        row
        for row in matched
        if math.isclose(
            float(row["fo2_log10_bar"]),
            reference_fo2_log10_bar,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
    ]
    t_slope = _linear_slope(
        [float(row["temperature_K"]) for row in reference_rows],
        [float(row["delta_log10_rail_minus_vaporock_dex"]) for row in reference_rows],
    )
    rail_slopes = [
        float(item["rail_dlog10P_dlog10fO2"])
        for item in slopes
        if item["rail_dlog10P_dlog10fO2"] is not None
    ]
    vaporock_slopes = [
        float(item["vaporock_dlog10P_dlog10fO2"])
        for item in slopes
        if item["vaporock_dlog10P_dlog10fO2"] is not None
    ]
    slope_differences = [
        float(item["slope_difference_rail_minus_vaporock"])
        for item in slopes
        if item["slope_difference_rail_minus_vaporock"] is not None
    ]
    max_abs = max((abs(value) for value in deltas), default=None)
    return {
        "species": species,
        "matched_point_count": len(matched),
        "rail_answered_vaporock_not_count": sum(
            row["coverage"] == "rail_answered_vaporock_not" for row in rows
        ),
        "vaporock_answered_rail_not_count": sum(
            row["coverage"] == "vaporock_answered_rail_not" for row in rows
        ),
        "neither_answered_count": sum(
            row["coverage"] == "neither_answered" for row in rows
        ),
        "censored_match_count": sum(
            row["coverage"] == "matched_censored" for row in rows
        ),
        "median_delta_dex": median(deltas) if deltas else None,
        "median_abs_delta_dex": median(abs(value) for value in deltas) if deltas else None,
        "min_delta_dex": min(deltas) if deltas else None,
        "max_delta_dex": max(deltas) if deltas else None,
        "max_abs_delta_dex": max_abs,
        "divergence_label": divergence_label(max_abs),
        "max_abs_delta_cell": (
            {
                "temperature_K": max_row["temperature_K"],
                "fo2_log10_bar": max_row["fo2_log10_bar"],
                "delta_dex": max_row["delta_log10_rail_minus_vaporock_dex"],
                "rail_pressure_Pa": max_row["rail"]["pressure_Pa"],
                "vaporock_pressure_Pa": max_row["vaporock"]["pressure_Pa"],
            }
            if max_row is not None
            else None
        ),
        "temperature_dependence": {
            "reference_fo2_log10_bar": reference_fo2_log10_bar,
            "n_points": len(reference_rows),
            "delta_slope_dex_per_100K": t_slope * 100.0 if t_slope is not None else None,
            "min_delta_dex": min(
                (
                    float(row["delta_log10_rail_minus_vaporock_dex"])
                    for row in reference_rows
                ),
                default=None,
            ),
            "max_delta_dex": max(
                (
                    float(row["delta_log10_rail_minus_vaporock_dex"])
                    for row in reference_rows
                ),
                default=None,
            ),
        },
        "fo2_dependence": {
            "median_rail_slope": median(rail_slopes) if rail_slopes else None,
            "median_vaporock_slope": (
                median(vaporock_slopes) if vaporock_slopes else None
            ),
            "median_slope_difference": (
                median(slope_differences) if slope_differences else None
            ),
            "min_slope_difference": min(slope_differences) if slope_differences else None,
            "max_slope_difference": max(slope_differences) if slope_differences else None,
            "per_temperature": slopes,
        },
    }


def build_crosscheck_report(
    *,
    composition: CrosscheckComposition,
    temperatures_K: Sequence[float],
    fo2_log10_bar: Sequence[float],
    raw_cells: Sequence[Mapping[str, Any]],
    rail_declared_species: Sequence[str],
    vaporock_declared_species: Sequence[str],
    p_floor_Pa: float,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build deterministic detailed rows and per-species divergence summaries."""

    shared_declared = set(rail_declared_species) & set(vaporock_declared_species)
    answered_species: set[str] = set()
    for cell in raw_cells:
        answered_species.update(cell["rail"]["pressures_Pa"])
        answered_species.update(cell["vaporock"]["pressures_Pa"])
    species_scope = sorted(shared_declared | answered_species)

    rows: list[dict[str, Any]] = []
    for cell in sorted(
        raw_cells,
        key=lambda item: (item["temperature_K"], item["fo2_log10_bar"]),
    ):
        for species in species_scope:
            rail = _observation(
                cell["rail"]["pressures_Pa"],
                species,
                cell_status=cell["rail"]["status"],
                cell_reason=cell["rail"].get("reason"),
                p_floor_Pa=p_floor_Pa,
            )
            vaporock = _observation(
                cell["vaporock"]["pressures_Pa"],
                species,
                cell_status=cell["vaporock"]["status"],
                cell_reason=cell["vaporock"].get("reason"),
                p_floor_Pa=p_floor_Pa,
                silent_zero_notes=cell["vaporock"].get("silent_zero_notes"),
            )
            coverage = _coverage_label(rail, vaporock)
            if coverage == "neither_answered" and species not in shared_declared:
                continue
            delta = None
            if coverage == "matched_point":
                delta = math.log10(
                    float(rail["pressure_Pa"]) / float(vaporock["pressure_Pa"])
                )
            rows.append(
                {
                    "species": species,
                    "temperature_K": float(cell["temperature_K"]),
                    "fo2_log10_bar": float(cell["fo2_log10_bar"]),
                    "rail": rail,
                    "vaporock": vaporock,
                    "rail_source": cell["rail"].get("sources", {}).get(species),
                    "rail_activity": cell["rail"].get("activities", {}).get(species),
                    "coverage": coverage,
                    "delta_log10_rail_minus_vaporock_dex": delta,
                }
            )

    rows_by_species: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_species[str(row["species"])].append(row)
    reference_fo2 = sorted(float(value) for value in fo2_log10_bar)[
        len(fo2_log10_bar) // 2
    ]
    species_summaries = [
        _summarize_species(
            species,
            species_rows,
            reference_fo2_log10_bar=reference_fo2,
        )
        for species, species_rows in sorted(rows_by_species.items())
    ]
    compared = [
        item["species"]
        for item in species_summaries
        if item["matched_point_count"] > 0
    ]
    asymmetries = [
        {
            "species": item["species"],
            "rail_answered_vaporock_not_count": item[
                "rail_answered_vaporock_not_count"
            ],
            "vaporock_answered_rail_not_count": item[
                "vaporock_answered_rail_not_count"
            ],
            "neither_answered_count": item["neither_answered_count"],
        }
        for item in species_summaries
        if item["rail_answered_vaporock_not_count"]
        or item["vaporock_answered_rail_not_count"]
    ]
    wild = [
        item["species"]
        for item in species_summaries
        if item["divergence_label"] == "wild_ge_2_dex"
    ]
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    cell_runs: list[dict[str, Any]] = []
    for cell in sorted(
        raw_cells,
        key=lambda item: (item["temperature_K"], item["fo2_log10_bar"]),
    ):
        computed_envelope = melt_extrapolation_diagnostic(
            float(cell["temperature_K"]),
            _MELT_MODEL_ID,
        )
        vaporock_cell = cell["vaporock"]
        envelope_source = (
            vaporock_cell
            if has_melt_extrapolation_envelope(vaporock_cell)
            else computed_envelope
        )
        consume_melt_extrapolation_envelope(
            envelope_source,
            temperature_K=float(cell["temperature_K"]),
        )
        envelope = {
            field: envelope_source[field]
            for field in MELT_EXTRAPOLATION_ENVELOPE_FIELDS
        }
        cell_runs.append({
            "temperature_K": float(cell["temperature_K"]),
            "fo2_log10_bar": float(cell["fo2_log10_bar"]),
            "rail_status": cell["rail"]["status"],
            "rail_reason": cell["rail"].get("reason"),
            "rail_pressure_count": len(cell["rail"]["pressures_Pa"]),
            "vaporock_status": cell["vaporock"]["status"],
            "vaporock_reason": cell["vaporock"].get("reason"),
            "vaporock_warnings": list(cell["vaporock"].get("warnings") or []),
            "vaporock_pressure_count": len(cell["vaporock"]["pressures_Pa"]),
            "vaporock_instrument_status": envelope_source[
                "instrument_status"
            ],
            "vaporock_h2_melt_envelope": envelope,
        })
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "kind": "vapour_rail_engine_crosscheck",
        "generated_at": timestamp,
        "authority": "diagnostic_only",
        "certifies": False,
        "calibrates": False,
        "verdict": None,
        "posture": (
            "Measured divergence is signal, not a pass/fail gate. Signed delta is "
            "log10(P_rail/P_VapoRock); no coefficient is adjusted by this harness."
        ),
        "domain": {
            "temperature_K": [min(temperatures_K), max(temperatures_K)],
            "temperatures_K": list(temperatures_K),
            "fo2_log10_bar": list(fo2_log10_bar),
            "pressure_bar": DEFAULT_PRESSURE_BAR,
            "p_floor_Pa": p_floor_Pa,
            "vaporock_external_gate": [VAPOROCK_T_MIN_K, VAPOROCK_T_MAX_K],
            "fo2_grid_note": (
                "Fixed admitted grid used on both sources; IW-1 can fall below the "
                "live rail 1e-9 bar floor at low T and is not silently clamped."
            ),
        },
        "composition": {
            "composition_id": composition.composition_id,
            "composition_wt_pct": dict(composition.composition_wt_pct),
            "composition_mol": dict(composition.composition_mol),
            "composition_digest_sha256": _canonical_digest(
                dict(composition.composition_mol)
            ),
        },
        "engines": {
            "vaporock": {
                "role": "external_vapour_pressure_comparator",
                "route": "VR-5 warm pool only",
                "queried": True,
                "package_version": _installed_version("vaporock"),
                "liquid_state_evidence": (
                    "unverified_not_asserted; VapoRock internal melt solve used, "
                    "but no external liquid_fraction value was fabricated"
                ),
            },
            "alphamelts": {
                "role": "VapoRock condensed-melt solver and activity context",
                "queried_as_independent_pressure_comparator": False,
                "thermoengine_package_version": _installed_version("thermoengine"),
                "coverage_finding": (
                    "No independent gas-pressure API; direct helper also risks an "
                    "unpooled VapoRock call, so no separate call is fabricated."
                ),
            },
            "magemin": {
                "role": "phase-assemblage/liquid-fraction context",
                "queried_as_pressure_comparator": False,
                "coverage_finding": (
                    "No vapour-pressure API; matched MAGEMin plus ThermoEngine "
                    "activity-evidence conversion is not runtime-wired."
                ),
            },
        },
        "coverage": {
            "rail_declared_pressure_species": sorted(set(rail_declared_species)),
            "vaporock_declared_species": sorted(set(vaporock_declared_species)),
            "shared_declared_species": sorted(shared_declared),
            "species_compared": compared,
            "asymmetries": asymmetries,
        },
        "wild_divergence_species": wild,
        "species_summaries": species_summaries,
        "cell_runs": cell_runs,
        "rows": rows,
    }


def run_engine_crosscheck(
    *,
    composition: CrosscheckComposition | None = None,
    temperatures_K: Sequence[float] | None = None,
    fo2_log10_bar: Sequence[float] = DEFAULT_FO2_LOG10_BAR,
    pressure_bar: float = DEFAULT_PRESSURE_BAR,
    p_floor_Pa: float = DEFAULT_P_FLOOR_PA,
    warm_pool_size: int = 1,
    rail_provider: Any | None = None,
    catalog_payload: Mapping[str, Any] | None = None,
    rail_declared_species: Sequence[str] | None = None,
    vaporock_backend: VapoRockBackend | None = None,
    vaporock_declared_species: Sequence[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Run the matched-cell campaign and return a measured-divergence report."""

    temperatures = validate_temperature_grid(
        temperatures_K if temperatures_K is not None else temperature_grid_K()
    )
    fo2_values = validate_fo2_grid(fo2_log10_bar)
    if not math.isfinite(pressure_bar) or pressure_bar <= 0.0:
        raise EngineCrosscheckError("pressure_bar must be finite and positive")
    if not math.isfinite(p_floor_Pa) or p_floor_Pa <= 0.0:
        raise EngineCrosscheckError("p_floor_Pa must be finite and positive")
    composition = composition or load_crosscheck_composition()

    if rail_provider is None:
        rail_provider, loaded_payload = load_rail_provider()
        catalog_payload = catalog_payload or loaded_payload
    if rail_declared_species is None:
        if catalog_payload is None:
            raise EngineCrosscheckError(
                "catalog_payload or rail_declared_species is required with an "
                "injected rail provider"
            )
        rail_declared_species = declared_rail_pressure_species(catalog_payload)

    owns_backend = vaporock_backend is None
    if vaporock_backend is None:
        vaporock_backend = open_warm_vaporock_backend(
            warm_pool_size=warm_pool_size,
        )
    else:
        require_warm_pool_backend(vaporock_backend)
    if vaporock_declared_species is None:
        vaporock_declared_species = tuple(vaporock_backend.get_vapor_species())

    raw_cells: list[dict[str, Any]] = []
    try:
        for temperature_K in temperatures:
            for fo2_log in fo2_values:
                rail = _run_rail_cell(
                    rail_provider,
                    composition_mol=composition.composition_mol,
                    temperature_K=temperature_K,
                    fo2_log10_bar=fo2_log,
                    pressure_bar=pressure_bar,
                )
                vaporock = _run_vaporock_cell(
                    vaporock_backend,
                    composition_mol=composition.composition_mol,
                    temperature_K=temperature_K,
                    fo2_log10_bar=fo2_log,
                    pressure_bar=pressure_bar,
                )
                raw_cells.append(
                    {
                        "temperature_K": temperature_K,
                        "fo2_log10_bar": fo2_log,
                        "rail": rail,
                        "vaporock": vaporock,
                    }
                )
    finally:
        if owns_backend:
            vaporock_backend.close()

    report = build_crosscheck_report(
        composition=composition,
        temperatures_K=temperatures,
        fo2_log10_bar=fo2_values,
        raw_cells=raw_cells,
        rail_declared_species=rail_declared_species,
        vaporock_declared_species=vaporock_declared_species,
        p_floor_Pa=p_floor_Pa,
        generated_at=generated_at,
    )
    report["domain"]["pressure_bar"] = pressure_bar
    report["provenance"] = {
        "repository": _repository_state(),
        "rail_catalog_digest_sha256": (
            _canonical_digest(catalog_payload) if catalog_payload is not None else None
        ),
        "composition_digest_sha256": report["composition"][
            "composition_digest_sha256"
        ],
        "warm_pool_size_requested": warm_pool_size,
        "vaporock_call_count": len(raw_cells),
        "rail_call_count": len(raw_cells),
    }
    return report


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.{digits}f}"


def render_crosscheck_markdown(report: Mapping[str, Any]) -> str:
    """Render the human-readable companion to the detailed JSON report."""

    domain = report["domain"]
    composition = report["composition"]
    lines = [
        "# Vapour rail / engine measured-divergence report",
        "",
        f"- generated: `{report['generated_at']}`",
        (
            f"- authority: `{report['authority']}`; "
            f"certifies: `{str(report['certifies']).lower()}`; "
            f"calibrates: `{str(report['calibrates']).lower()}`"
        ),
        "- verdict: none — this report measures divergence and cannot pass or fail the model",
        "- signed delta: `log10(P_rail/P_VapoRock)`",
        "",
        "## Matched inputs",
        "",
        (
            f"- composition: `{composition['composition_id']}` "
            "(same oxide-mol map on both sides)"
        ),
        (
            f"- temperature: `{domain['temperature_K'][0]:g}–"
            f"{domain['temperature_K'][1]:g} K` "
            f"({len(domain['temperatures_K'])} points)"
        ),
        (
            "- log10 fO2/bar: `"
            + ", ".join(f"{value:g}" for value in domain["fo2_log10_bar"])
            + "`"
        ),
        (
            "- VapoRock route: `VR-5 warm pool`; external gate: `"
            f"{domain['vaporock_external_gate'][0]:g}–"
            f"{domain['vaporock_external_gate'][1]:g} K`"
        ),
        (
            "- liquid state: `unverified_not_asserted`; VapoRock used its "
            "internal melt solve and the harness did not fabricate "
            "`liquid_fraction=1`"
        ),
        f"- fO2 note: {domain['fo2_grid_note']}",
        "",
        "## Engine applicability",
        "",
        "| Engine | Role in this report | Pressure comparator? | Coverage finding |",
        "|---|---|---:|---|",
        (
            "| VapoRock | External vapour-pressure baseline | yes | Warm-pool "
            "calls only; full non-authoritative speciation retained |"
        ),
        (
            "| alphaMELTS | Condensed-melt/activity context used by the VapoRock "
            "family | no separate call | No independent gas-pressure API; a "
            "direct helper risks bypassing the warm pool |"
        ),
        (
            "| MAGEMin | Phase assemblage / liquid-fraction context | no | No "
            "vapour API; matched activity evidence conversion is not "
            "runtime-wired |"
        ),
        "",
        "## Per-species divergence",
        "",
        (
            "Magnitude labels are descriptive only. `wild_ge_2_dex` means at "
            "least one matched cell differs by 100× or more."
        ),
        "",
        (
            "| Species | Matched | Rail-only | VR-only | median Δ dex | min..max "
            "Δ dex | max |Δ| | T trend dex/100 K | rail fO2 slope | VR fO2 "
            "slope | Δ slope | Finding |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["species_summaries"]:
        t_dep = item["temperature_dependence"]
        f_dep = item["fo2_dependence"]
        extent = (
            f"{_fmt(item['min_delta_dex'])}..{_fmt(item['max_delta_dex'])}"
            if item["matched_point_count"]
            else "—"
        )
        lines.append(
            "| {species} | {matched} | {rail_only} | {vr_only} | {median} | "
            "{extent} | {max_abs} | {t_slope} | {rail_slope} | {vr_slope} | "
            "{slope_delta} | `{label}` |".format(
                species=item["species"],
                matched=item["matched_point_count"],
                rail_only=item["rail_answered_vaporock_not_count"],
                vr_only=item["vaporock_answered_rail_not_count"],
                median=_fmt(item["median_delta_dex"]),
                extent=extent,
                max_abs=(
                    f"{float(item['max_abs_delta_dex']):.3f}"
                    if item["max_abs_delta_dex"] is not None
                    else "—"
                ),
                t_slope=_fmt(t_dep["delta_slope_dex_per_100K"]),
                rail_slope=_fmt(f_dep["median_rail_slope"]),
                vr_slope=_fmt(f_dep["median_vaporock_slope"]),
                slope_delta=_fmt(f_dep["median_slope_difference"]),
                label=item["divergence_label"],
            )
        )

    lines.extend(["", "## Coverage asymmetries", ""])
    asymmetries = report["coverage"]["asymmetries"]
    if asymmetries:
        lines.extend(
            [
                "| Species | Rail answered / VR did not | VR answered / rail did not |",
                "|---|---:|---:|",
            ]
        )
        for item in asymmetries:
            lines.append(
                f"| {item['species']} | "
                f"{item['rail_answered_vaporock_not_count']} | "
                f"{item['vaporock_answered_rail_not_count']} |"
            )
    else:
        lines.append("No unilateral answered cells were observed.")

    lines.extend(["", "## Wild-divergence findings", ""])
    wild_items = [
        item
        for item in report["species_summaries"]
        if item["divergence_label"] == "wild_ge_2_dex"
    ]
    if wild_items:
        for item in wild_items:
            cell = item["max_abs_delta_cell"]
            lines.append(
                f"- **{item['species']}**: max |Δ| `{item['max_abs_delta_dex']:.3f} dex` "
                f"at `{cell['temperature_K']:g} K`, "
                f"`log10 fO2/bar={cell['fo2_log10_bar']:g}` "
                f"(rail `{cell['rail_pressure_Pa']:.6e} Pa`, "
                f"VapoRock `{cell['vaporock_pressure_Pa']:.6e} Pa`)."
            )
    else:
        lines.append("No matched species reached the descriptive 2 dex wild band.")

    lines.extend(["", "## Per-temperature fO2 slopes", ""])
    lines.extend(
        [
            "| Species | T K | n | rail slope | VapoRock slope | rail-minus-VapoRock |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["species_summaries"]:
        for slope in item["fo2_dependence"]["per_temperature"]:
            lines.append(
                f"| {item['species']} | {slope['temperature_K']:g} | "
                f"{slope['n_points']} | "
                f"{_fmt(slope['rail_dlog10P_dlog10fO2'])} | "
                f"{_fmt(slope['vaporock_dlog10P_dlog10fO2'])} | "
                f"{_fmt(slope['slope_difference_rail_minus_vaporock'])} |"
            )
    lines.extend(
        [
            "",
            "## Detailed rows",
            "",
            (
                "The companion JSON contains every matched-pressure row, "
                "censored value, refusal, and unilateral coverage result. No "
                "result is clipped or used to change a coefficient."
            ),
            "",
        ]
    )
    return "\n".join(lines)
