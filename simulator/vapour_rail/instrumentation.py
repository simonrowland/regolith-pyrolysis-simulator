"""VR-11 instrumentation + VapourBatch flux cutover helpers.

U4 / VR-11 threads exact-key channel answers, validation/verdict status,
refusals, solve groups, activity bounds, source-boundary / anti-cliff
acquisition flags, and the nine-row advisory ceiling table through the
runner / artifact / UI surfaces.

Active evaporation flux consumes a complete VapourBatch: iterate
``requested_species_ids``, branch on pressure/flux unions, and never fall
back to the compatibility live map. Shadow equality is a measured outcome
(proved / mismatch / not-fixed / typed disagreement), never a hardcoded True.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from simulator.vapour_rail.batch import (
    FluxDiagnosticUpperBound,
    FluxEligible,
    FluxRefusal,
    PressureRefusal,
    PressureUpperBound,
    PressureValue,
    VapourAnswer,
    VapourBatch,
    ZeroByPhysics,
)

# Shadow-equal outcome vocabulary (never collapse to a vacuous True).
SHADOW_PROVED = "proved"
SHADOW_MISMATCH = "mismatch"
SHADOW_NOT_FIXED = "not_fixed"
SHADOW_MISSING_BATCH = "missing_batch"
SHADOW_RESOLUTION_ERROR = "resolution_error"
SHADOW_REFUSED_VS_LIVE = "refused_vs_live_disagreement"
SHADOW_MISSING_KEYS = "missing_keys"
SHADOW_NONFINITE_LIVE = "nonfinite_live"
SHADOW_ABSENT_COMPARISON = "absent_comparison"

_SHADOW_EQUAL_TRUE_OUTCOMES = frozenset({SHADOW_PROVED})

# ---------------------------------------------------------------------------
# Nine-row advisory source-vapour ceiling (DESIGN-REV5 §7.1)
# ---------------------------------------------------------------------------

PLUME_SOURCE_SIO_SPECIES = "SiO"
FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL = 0.013617600827

# Each row separates lookup_gas_id, source_parent_id, ceiling_mol, and status.
# ``unvalidated_legacy`` zeros must never be read as measured ZeroByPhysics.
SOURCE_VAPOUR_CEILING_ROWS: tuple[Mapping[str, Any], ...] = (
    MappingProxyType(
        {
            "legacy_key": "SiO",
            "lookup_gas_id": "SiO",
            "source_parent_id": "SiO2",
            "ceiling_mol": FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL,
            "status": "frozen_sio_proxy",
            "evidence": "FROZEN_SIO_SOURCE_VAPOR_CEILING_MOL diagnostic pin",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "Na2O",
            "lookup_gas_id": "Na2O_gas",
            "source_parent_id": "Na2O",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; not measured ZeroByPhysics",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "K2O",
            "lookup_gas_id": "K2O_gas",
            "source_parent_id": "K2O",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; not measured ZeroByPhysics",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "FeO",
            "lookup_gas_id": "FeO_gas",
            "source_parent_id": "FeO",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; not measured ZeroByPhysics",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "MgO",
            "lookup_gas_id": "MgO_gas",
            "source_parent_id": "MgO",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; not measured ZeroByPhysics",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "CaO",
            "lookup_gas_id": "CaO_gas",
            "source_parent_id": "CaO",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; not measured ZeroByPhysics",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "Al2O3",
            "lookup_gas_id": "Al2O3_gas",
            "source_parent_id": "Al2O3",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; not measured ZeroByPhysics",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "TiO2",
            "lookup_gas_id": "TiO2_gas",
            "source_parent_id": "TiO2",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; not measured ZeroByPhysics",
            "advisory_only": True,
        }
    ),
    MappingProxyType(
        {
            "legacy_key": "CrO2",
            "lookup_gas_id": "CrO2",
            "source_parent_id": "Cr2O3",
            "ceiling_mol": 0.0,
            "status": "unvalidated_legacy",
            "evidence": "inherited zero; collision-free gas ID retained",
            "advisory_only": True,
        }
    ),
)

# Compatibility projection of the nine-row table (legacy bare keys).
MAJOR_METAL_OXIDE_SOURCE_VAPOR_CEILINGS_MOL: Mapping[str, float] = MappingProxyType(
    {
        str(row["legacy_key"]): float(row["ceiling_mol"])
        for row in SOURCE_VAPOUR_CEILING_ROWS
    }
)


def source_vapour_ceiling_table() -> list[dict[str, Any]]:
    """Return a mutable JSON-safe copy of the nine advisory ceiling rows."""

    return [dict(row) for row in SOURCE_VAPOUR_CEILING_ROWS]


def source_vapour_ceiling_lookup_keys(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Keys to probe in near-melt species maps for one ceiling row.

    Prefers the canonical gas ID; falls back to the legacy bare key so the
    diagnostic remains non-vacuous both before and after the ``_gas`` rename.
    """

    keys: list[str] = []
    for key_name in ("lookup_gas_id", "legacy_key", "source_parent_id"):
        value = row.get(key_name)
        if isinstance(value, str) and value and value not in keys:
            keys.append(value)
    return tuple(keys)


# ---------------------------------------------------------------------------
# VapourBatch serialization (artifact / UI / runner)
# ---------------------------------------------------------------------------


def _pressure_payload(pressure: Any) -> dict[str, Any]:
    if isinstance(pressure, PressureValue):
        return {"kind": "value", "pa": float(pressure.pa)}
    if isinstance(pressure, PressureUpperBound):
        return {
            "kind": "upper_bound",
            "pa": float(pressure.pa),
            "evidence_ref": str(pressure.evidence_ref),
        }
    if isinstance(pressure, ZeroByPhysics):
        return {
            "kind": "zero_by_physics",
            "evidence_ref": str(pressure.evidence_ref),
        }
    if isinstance(pressure, PressureRefusal):
        return {
            "kind": "refusal",
            "code": str(pressure.code),
            "detail": str(pressure.detail),
        }
    return {"kind": "unknown", "repr": repr(pressure)}


def _flux_payload(flux: Any) -> dict[str, Any]:
    if isinstance(flux, FluxEligible):
        return {
            "kind": "eligible",
            "alpha_ref": str(flux.alpha_ref),
            "reaction_id": flux.reaction_id,
        }
    if isinstance(flux, FluxDiagnosticUpperBound):
        return {
            "kind": "diagnostic_upper_bound",
            "alpha_ref": str(flux.alpha_ref),
            "reaction_id": flux.reaction_id,
        }
    if isinstance(flux, FluxRefusal):
        return {
            "kind": "refusal",
            "code": str(flux.code),
            "detail": str(flux.detail),
        }
    return {"kind": "unknown", "repr": repr(flux)}


def serialize_vapour_answer(answer: VapourAnswer) -> dict[str, Any]:
    """JSON-safe channel answer for runner/artifact/UI."""

    extra = dict(answer.extra) if isinstance(answer.extra, Mapping) else {}
    return {
        "species_id": answer.species_id,
        "pressure": _pressure_payload(answer.pressure),
        "selected_runtime_pressure": _pressure_payload(
            answer.selected_runtime_pressure
        ),
        "flux": _flux_payload(answer.flux),
        "source_label": answer.source_label,
        "formula_id": answer.formula_id,
        "source_account": answer.source_account,
        "solve_group_id": answer.solve_group_id,
        "state_fingerprint": answer.state_fingerprint,
        "validation_status": answer.validation_status,
        "validation_anchor_refs": list(answer.validation_anchor_refs),
        "verdict_status": answer.verdict_status,
        "certification_ceiling": answer.certification_ceiling,
        "refusal_code": answer.refusal_code,
        "is_refused": bool(answer.is_refused),
        # A channel answer has no epoch context; this is only union eligibility.
        "is_union_flux_eligible": bool(answer.is_flux_active),
        "extra": extra,
        # Anti-cliff / acquisition flags ride on extra when the evaluator
        # recorded them; promote common keys for UI consumers.
        "out_of_range": bool(extra.get("out_of_range", False)),
        "acquisition_flag": extra.get("acquisition_flag"),
        "activity_bound": extra.get("activity_bound"),
        "source_boundary": extra.get("source_boundary"),
    }


def serialize_vapour_batch(batch: VapourBatch | None) -> dict[str, Any] | None:
    """Serialize an exact-key batch for instrumentation surfaces."""

    if batch is None:
        return None
    channels: dict[str, dict[str, Any]] = {}
    for species_id, answer in sorted(batch.channels_by_species.items()):
        channel = serialize_vapour_answer(answer)
        union_eligible = bool(channel["is_union_flux_eligible"])
        effective_active = species_id in batch.flux_active_species_ids
        # A channel may be answerable yet dormant under the current epoch.
        # Batch serialization must expose one unambiguous activation truth.
        channel["is_flux_active"] = effective_active
        channel["is_flux_dormant_by_epoch"] = union_eligible and not effective_active
        channels[species_id] = channel
    refusals = {
        species_id: channel
        for species_id, channel in channels.items()
        if channel.get("is_refused")
    }
    return {
        "schema": "vapour_batch.v1",
        "n_requested": len(batch.requested_species_ids),
        "n_flux_active": len(batch.flux_active_species_ids),
        "n_refused": len(refusals),
        "requested_species_ids": sorted(batch.requested_species_ids),
        "flux_active_species_ids": sorted(batch.flux_active_species_ids),
        "solve_bundle_ids": {
            bundle_id: sorted(members)
            for bundle_id, members in sorted(batch.solve_bundle_ids.items())
        },
        "channels_by_species": channels,
        "refusals_by_species": refusals,
        "metadata": dict(batch.metadata),
    }


# ---------------------------------------------------------------------------
# VapourBatch flux cutover: batch is authority; live is shadow projection
# ---------------------------------------------------------------------------

CONTROL_FLUX_PRESSURES_KEY = "vapour_batch_flux_pressures_Pa"
CONTROL_BATCH_REPORT_KEY = "vapour_batch_report"
CONTROL_SHADOW_EQUAL_KEY = "vapour_batch_flux_shadow_equal"
CONTROL_SHADOW_OUTCOME_KEY = "vapour_batch_flux_shadow_outcome"

# Absolute + relative tolerance for per-species pressure comparison.
_SHADOW_PA_ATOL = 1.0e-12
_SHADOW_PA_RTOL = 1.0e-9


def _finite_live_map(
    live_pressures_Pa: Mapping[str, float] | None,
) -> tuple[dict[str, float], list[str]]:
    """Split finite live pressures from non-finite keys (never silent drop)."""

    live: dict[str, float] = {}
    dropped: list[str] = []
    for species, pressure in dict(live_pressures_Pa or {}).items():
        try:
            value = float(pressure)
        except (TypeError, ValueError):
            dropped.append(str(species))
            continue
        if not math.isfinite(value):
            dropped.append(str(species))
            continue
        live[str(species)] = value
    return live, sorted(dropped)


def finite_live_pressure_species_ids(
    live_pressures_Pa: Mapping[str, float] | None,
) -> frozenset[str]:
    """Canonical finite species set used by legacy activation and shadow."""

    live, _ = _finite_live_map(live_pressures_Pa)
    return frozenset(live)


def finite_live_pressure_map(
    live_pressures_Pa: Mapping[str, float] | None,
) -> dict[str, float]:
    """Finite legacy projection used to seed pre-RG runtime selections."""

    live, _ = _finite_live_map(live_pressures_Pa)
    return live


def _pressures_equal(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_SHADOW_PA_RTOL, abs_tol=_SHADOW_PA_ATOL)


def _channel_flux_pressure_pa(answer: VapourAnswer) -> tuple[float | None, str]:
    """Branch on pressure/flux unions → debiting Pa or typed non-debit state.

    Returns ``(pa_or_None, state)`` where ``pa`` is only set for inventory-
    debiting HKL (PressureValue + FluxEligible). Upper bounds and refusals
    never debit; ZeroByPhysics is an explicit zero.
    """

    pressure = answer.selected_runtime_pressure
    flux = answer.flux
    if isinstance(pressure, PressureRefusal) or isinstance(flux, FluxRefusal):
        return None, "refusal"
    if isinstance(flux, FluxDiagnosticUpperBound) or isinstance(
        pressure, PressureUpperBound
    ):
        return None, "upper_bound"
    if isinstance(pressure, ZeroByPhysics):
        return 0.0, "zero_by_physics"
    if isinstance(pressure, PressureValue) and isinstance(flux, FluxEligible):
        pa = float(pressure.pa)
        if not math.isfinite(pa):
            return None, "nonfinite_batch_pressure"
        return pa, "eligible"
    return None, "incomplete_channel"


def compare_legacy_vs_batch_flux_paths(
    *,
    legacy_pressures_Pa: Mapping[str, float],
    batch_flux_pressures_Pa: Mapping[str, float],
    legacy_flux_active_species_ids: Sequence[str] | None = None,
    batch_flux_active_species_ids: Sequence[str] | None = None,
    refused_live_species: Sequence[str] = (),
    missing_batch_keys: Sequence[str] = (),
    dropped_nonfinite_live_species: Sequence[str] = (),
    batch_present: bool = True,
    resolution_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute an explicit proved/mismatch/not-fixed shadow outcome.

    Null hypothesis this refutes when stubbed: any missing evidence still
    reports ``shadow_equal=True``.
    """

    if resolution_error:
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_RESOLUTION_ERROR,
            "detail": dict(resolution_error),
            "mismatched_species": [],
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
        }
    if not batch_present:
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_MISSING_BATCH,
            "detail": "complete VapourBatch required; no legacy flux fallback",
            "mismatched_species": [],
            "missing_in_batch_path": sorted(legacy_pressures_Pa),
            "missing_in_legacy_path": [],
        }
    if dropped_nonfinite_live_species:
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_NONFINITE_LIVE,
            "detail": {
                "dropped_nonfinite_live_species": list(
                    dropped_nonfinite_live_species
                )
            },
            "mismatched_species": [],
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
        }
    if refused_live_species:
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_REFUSED_VS_LIVE,
            "detail": {
                "batch_refused_live_species": list(refused_live_species),
            },
            "mismatched_species": [],
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
        }
    if missing_batch_keys:
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_MISSING_KEYS,
            "detail": {"missing_batch_keys": list(missing_batch_keys)},
            "mismatched_species": [],
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
        }

    if (
        legacy_flux_active_species_ids is None
        or batch_flux_active_species_ids is None
    ):
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_MISSING_KEYS,
            "detail": "both flux-active species sets are required for shadow proof",
            "mismatched_species": [],
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
        }
    legacy_active = frozenset(str(sid) for sid in legacy_flux_active_species_ids)
    batch_active = frozenset(str(sid) for sid in batch_flux_active_species_ids)
    missing_active_in_batch = sorted(legacy_active - batch_active)
    missing_active_in_legacy = sorted(batch_active - legacy_active)
    if missing_active_in_batch or missing_active_in_legacy:
        # DESIGN-REV5 G2: equal Pa on the live intersection cannot prove
        # shadow parity when the flux-active species sets differ.
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_MISSING_KEYS,
            "detail": "flux-active species set differs between legacy and batch",
            "mismatched_species": [],
            "missing_in_batch_path": missing_active_in_batch,
            "missing_in_legacy_path": missing_active_in_legacy,
        }

    legacy = {str(k): float(v) for k, v in dict(legacy_pressures_Pa).items()}
    batch_path = {
        str(k): float(v) for k, v in dict(batch_flux_pressures_Pa).items()
    }
    missing_in_batch = sorted(set(legacy) - set(batch_path))
    missing_in_legacy = sorted(set(batch_path) - set(legacy))
    mismatched: list[dict[str, Any]] = []
    for species in sorted(set(legacy) & set(batch_path)):
        if not _pressures_equal(legacy[species], batch_path[species]):
            mismatched.append(
                {
                    "species": species,
                    "legacy_Pa": legacy[species],
                    "batch_path_Pa": batch_path[species],
                }
            )

    if missing_in_batch or missing_in_legacy:
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_MISSING_KEYS,
            "detail": "species multiset differs between legacy and batch paths",
            "mismatched_species": mismatched,
            "missing_in_batch_path": missing_in_batch,
            "missing_in_legacy_path": missing_in_legacy,
        }
    if mismatched:
        return {
            "shadow_equal": False,
            "shadow_outcome": SHADOW_MISMATCH,
            "detail": "per-species pressure disagreement",
            "mismatched_species": mismatched,
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
        }
    if not legacy and not batch_path:
        # Empty-vs-empty is a valid identity, but only when a batch was present
        # and produced no debiting channels (not an absent comparison).
        return {
            "shadow_equal": True,
            "shadow_outcome": SHADOW_PROVED,
            "detail": "both paths empty",
            "mismatched_species": [],
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
        }
    return {
        "shadow_equal": True,
        "shadow_outcome": SHADOW_PROVED,
        "detail": "legacy and batch flux paths agree per species",
        "mismatched_species": [],
        "missing_in_batch_path": [],
        "missing_in_legacy_path": [],
    }


def flux_pressures_from_batch(
    batch: VapourBatch | None,
    *,
    resolution_error: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Build the flux-driving pressure map only from a complete VapourBatch.

    Active path: require batch, iterate ``requested_species_ids``, branch on
    selected-runtime-pressure/flux unions. Refusal/upper-bound/zero are typed
    non-debit states.
    Absent batch or resolve error → empty flux map + typed failure report;
    this consumer has no compatibility-map input or fallback.
    """

    report: dict[str, Any] = {
        "schema": "vapour_batch_flux_overlay.v1",
        "batch_present": batch is not None,
        "n_flux_pressures": 0,
        "batch_channel_states": {},
        "note": (
            "Active flux consumes each VapourAnswer selected runtime pressure. "
            "Compatibility pressure maps are absent from this consumer."
        ),
    }

    if resolution_error:
        report["resolution_error"] = dict(resolution_error)
        report["selection_source"] = "typed_failure_resolution_error"
        return {}, report

    if batch is None:
        report["selection_source"] = "typed_failure_missing_batch"
        return {}, report

    report["selection_source"] = "vapour_answer_selected_runtime_pressure"
    flux_pressures: dict[str, float] = {}
    batch_pa_by_species: dict[str, float] = {}
    selected_runtime_pa_by_species: dict[str, float] = {}
    channel_states: dict[str, str] = {}
    missing_channel_keys: list[str] = []

    for species_id in sorted(batch.requested_species_ids):
        answer = batch.channels_by_species.get(species_id)
        if answer is None:
            missing_channel_keys.append(species_id)
            channel_states[species_id] = "missing_channel"
            continue
        selected_pa, state = _channel_flux_pressure_pa(answer)
        channel_states[species_id] = state
        catalog_pressure = answer.pressure
        if isinstance(catalog_pressure, PressureValue):
            catalog_pa = float(catalog_pressure.pa)
            if math.isfinite(catalog_pa):
                batch_pa_by_species[species_id] = catalog_pa
        if state == "eligible" and selected_pa is not None:
            selected_runtime_pa_by_species[species_id] = float(selected_pa)
            if species_id not in batch.flux_active_species_ids:
                channel_states[species_id] = "dormant_by_epoch"
                continue
            flux_pressures[species_id] = float(selected_pa)
        elif state == "zero_by_physics":
            selected_runtime_pa_by_species[species_id] = 0.0
            if species_id not in batch.flux_active_species_ids:
                channel_states[species_id] = "dormant_by_epoch"
                continue
            flux_pressures[species_id] = 0.0
        # refusal / upper_bound / nonfinite / dormant eligible: no debit

    report["batch_channel_states"] = channel_states
    report["batch_pa_by_species"] = batch_pa_by_species
    report["selected_runtime_pa_by_species"] = selected_runtime_pa_by_species
    report["missing_batch_keys"] = missing_channel_keys
    report["n_flux_pressures"] = len(flux_pressures)

    return flux_pressures, report


def compare_live_shadow_to_batch_flux(
    *,
    batch: VapourBatch | None,
    live_pressures_Pa: Mapping[str, float],
    batch_flux_pressures_Pa: Mapping[str, float],
    resolution_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare an independently computed legacy map with batch-driven flux.

    This is the only VR-11 surface that accepts the compatibility pressure map.
    It never selects or returns flux-driving pressures.
    """

    live, dropped_nonfinite = _finite_live_map(live_pressures_Pa)
    refused_live: list[str] = []
    missing_channel_keys: list[str] = []
    catalog_pa_by_species: dict[str, float] = {}

    if batch is not None:
        for species_id in sorted(batch.requested_species_ids):
            answer = batch.channels_by_species.get(species_id)
            if answer is None:
                missing_channel_keys.append(species_id)
                continue
            if answer.is_refused and live.get(species_id, 0.0) > 0.0:
                refused_live.append(species_id)
            if (
                species_id in batch.flux_active_species_ids
                and isinstance(answer.pressure, PressureValue)
            ):
                catalog_pa = float(answer.pressure.pa)
                if math.isfinite(catalog_pa):
                    catalog_pa_by_species[species_id] = catalog_pa

    comparison = compare_legacy_vs_batch_flux_paths(
        legacy_pressures_Pa=live,
        batch_flux_pressures_Pa=batch_flux_pressures_Pa,
        legacy_flux_active_species_ids=tuple(live) if batch is not None else None,
        batch_flux_active_species_ids=(
            tuple(batch.flux_active_species_ids) if batch is not None else None
        ),
        refused_live_species=refused_live,
        missing_batch_keys=missing_channel_keys,
        dropped_nonfinite_live_species=dropped_nonfinite,
        batch_present=batch is not None,
        resolution_error=resolution_error,
    )
    comparison.update(
        {
            "n_live_pressures": len(live),
            "live_only_bridge_species": sorted(
                set(live) - set(batch_flux_pressures_Pa)
            ),
            "batch_refused_live_species": refused_live,
            "batch_flux_active_not_in_live": sorted(
                set(batch.flux_active_species_ids) - set(live)
            )
            if batch is not None
            else [],
            "live_flux_active_not_in_batch": sorted(
                set(live) - set(batch.flux_active_species_ids)
            )
            if batch is not None
            else sorted(live),
            "dropped_nonfinite_live_species": dropped_nonfinite,
        }
    )

    if batch is not None and not resolution_error:
        catalog_vs_live = compare_legacy_vs_batch_flux_paths(
            legacy_pressures_Pa=live,
            batch_flux_pressures_Pa=catalog_pa_by_species,
            legacy_flux_active_species_ids=tuple(live),
            batch_flux_active_species_ids=tuple(batch.flux_active_species_ids),
            batch_present=True,
        )
        comparison["catalog_pa_shadow_equal"] = catalog_vs_live["shadow_equal"]
        comparison["catalog_pa_shadow_outcome"] = catalog_vs_live[
            "shadow_outcome"
        ]
    return comparison


# ---------------------------------------------------------------------------
# Source guard: no flux consumer iterates compatibility pressure maps
# ---------------------------------------------------------------------------

# Direct one-line banned forms (regex tripwire).
_FLUX_MAP_ITERATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # for ... in [dict(]controls.get("vapor_pressures_Pa")...
    re.compile(
        r"for\s+\w+(?:\s*,\s*\w+)?\s+in\s+"
        r"\(?\s*(?:dict\()?\s*controls\.get\(\s*[\"']vapor_pressures_Pa[\"']"
    ),
    # Parenthesized: for s, p in (controls.get("vapor_pressures_Pa") or {}).items()
    re.compile(
        r"for\s+\w+(?:\s*,\s*\w+)?\s+in\s+"
        r"\(?\s*controls\.get\(\s*[\"']vapor_pressures_Pa[\"']"
    ),
    # Iterating compatibility YAML family groups for flux species selection.
    re.compile(
        r"for\s+\w+(?:\s*,\s*\w+)?\s+in\s+"
        r"(?:self\.)?vapor_pressures\.get\(\s*[\"'](?:metals|oxide_vapors)"
    ),
    re.compile(
        r"for\s+\w+(?:\s*,\s*\w+)?\s+in\s+"
        r"(?:self\.)?vapor_pressures\[[\"'](?:metals|oxide_vapors)"
    ),
)

# Any controls.get of the compatibility key in a flux consumer kernel is banned
# (the batch-consumer key is the sole flux pressure input).
_LEGACY_CONTROLS_GET_PATTERN = re.compile(
    r"controls\.get\(\s*[\"']vapor_pressures_Pa[\"']"
)

FLUX_CONSUMER_RELPATHS: tuple[str, ...] = (
    "engines/builtin/evaporation_flux.py",
    "simulator/evaporation.py",
    "simulator/vapour_rail/instrumentation.py",
    "simulator/vapour_rail/request.py",
)

# Kernel path: any legacy-key read is a fail.
_KERNEL_FLUX_CONSUMER_RELPATHS: frozenset[str] = frozenset(
    {"engines/builtin/evaporation_flux.py"}
)


def _alias_then_iterate_hits(source_text: str, *, path: str) -> list[str]:
    """AST: catch ``vp = controls.get("vapor_pressures_Pa"); for x in vp``."""

    hits: list[str] = []
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return hits

    legacy_aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        # dict(controls.get("vapor_pressures_Pa") or {})
        # controls.get("vapor_pressures_Pa") or {}
        get_call = _extract_controls_get_vapor_pressures(value)
        if get_call is not None:
            legacy_aliases.add(target.id)

    if not legacy_aliases:
        return hits

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        iter_names = {
            n.id for n in ast.walk(node.iter) if isinstance(n, ast.Name)
        }
        banned = sorted(iter_names & legacy_aliases)
        if banned:
            hits.append(
                f"{path}:{getattr(node, 'lineno', '?')}: "
                f"alias-then-iterate compatibility map via {banned}"
            )
    return hits


def _extract_controls_get_vapor_pressures(node: ast.AST) -> ast.Call | None:
    """Return the controls.get('vapor_pressures_Pa') call if present in expr."""

    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Name)
            and func.value.id == "controls"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "vapor_pressures_Pa"
        ):
            return node
        # dict(controls.get(...))
        if (
            isinstance(func, ast.Name)
            and func.id == "dict"
            and node.args
        ):
            return _extract_controls_get_vapor_pressures(node.args[0])
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            found = _extract_controls_get_vapor_pressures(value)
            if found is not None:
                return found
    if isinstance(node, ast.BinOp):
        return _extract_controls_get_vapor_pressures(
            node.left
        ) or _extract_controls_get_vapor_pressures(node.right)
    if isinstance(node, ast.UnaryOp):
        return _extract_controls_get_vapor_pressures(node.operand)
    if isinstance(node, ast.IfExp):
        return (
            _extract_controls_get_vapor_pressures(node.body)
            or _extract_controls_get_vapor_pressures(node.orelse)
            or _extract_controls_get_vapor_pressures(node.test)
        )
    if isinstance(node, ast.Attribute) and node.attr in {"items", "keys", "values"}:
        return _extract_controls_get_vapor_pressures(node.value)
    if isinstance(node, ast.Subscript):
        return _extract_controls_get_vapor_pressures(node.value)
    return None


def _batch_flux_consumer_live_argument_hits(
    source_text: str,
    *,
    path: str,
) -> list[str]:
    """Reject compatibility-pressure parameters on batch flux consumers."""

    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("flux_pressures_from_batch"):
            continue
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        live_arguments = [
            argument.arg
            for argument in arguments
            if "live" in argument.arg.lower() and "pressure" in argument.arg.lower()
        ]
        if live_arguments:
            hits.append(
                f"{path}:{node.lineno}: batch flux consumer accepts "
                f"compatibility pressure argument(s) {live_arguments}"
            )
    return hits


def _selected_runtime_pressure_live_source_hits(
    source_text: str,
    *,
    path: str,
) -> list[str]:
    """Reject live-pressure aliases copied into selected batch answers."""

    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    def identifiers(node: ast.AST) -> set[str]:
        names = {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name)
        }
        names.update(
            child.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Attribute)
        )
        return names

    tainted_aliases: set[str] = set()
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            value_names = identifiers(node.value)
            live_pressure_source = any(
                "live" in name.lower() and "pressure" in name.lower()
                for name in value_names
            ) or bool(value_names & tainted_aliases)
            if not live_pressure_source:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in tainted_aliases:
                    tainted_aliases.add(target.id)
                    changed = True

    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg not in {
                "selected_runtime_pressure",
                "selected_runtime_pressures_Pa",
            }:
                continue
            value_names = identifiers(keyword.value)
            direct_live_source = any(
                "live" in name.lower() and "pressure" in name.lower()
                for name in value_names
            )
            if direct_live_source or value_names & tainted_aliases:
                hits.append(
                    f"{path}:{node.lineno}: selected runtime pressure "
                    "depends on a live compatibility-pressure source"
                )
    return hits


def flux_consumer_compatibility_map_iterations(
    source_text: str,
    *,
    path: str = "",
) -> list[str]:
    """Return human-readable hits of banned flux-map iteration patterns."""

    hits: list[str] = []
    for lineno, line in enumerate(source_text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern in _FLUX_MAP_ITERATION_PATTERNS:
            if pattern.search(line):
                hits.append(f"{path}:{lineno}: {stripped}")
                break
        else:
            # Kernel consumers may not read the legacy key at all.
            if (
                path in _KERNEL_FLUX_CONSUMER_RELPATHS
                or path.endswith("evaporation_flux.py")
            ) and _LEGACY_CONTROLS_GET_PATTERN.search(line):
                hits.append(
                    f"{path}:{lineno}: legacy controls.get(vapor_pressures_Pa) "
                    f"in flux kernel: {stripped}"
                )
    hits.extend(_alias_then_iterate_hits(source_text, path=path))
    hits.extend(_batch_flux_consumer_live_argument_hits(source_text, path=path))
    hits.extend(_selected_runtime_pressure_live_source_hits(source_text, path=path))
    return hits


def assert_no_flux_consumer_iterates_compatibility_maps(
    sources: Mapping[str, str],
) -> None:
    """Hard-fail when a flux consumer iterates a compatibility pressure map."""

    all_hits: list[str] = []
    supplied_production_paths = set(sources) & set(FLUX_CONSUMER_RELPATHS)
    if supplied_production_paths and supplied_production_paths != set(
        FLUX_CONSUMER_RELPATHS
    ):
        missing = sorted(set(FLUX_CONSUMER_RELPATHS) - set(sources))
        all_hits.append(
            "source guard production scan is incomplete; missing " + repr(missing)
        )
    for path, text in sources.items():
        all_hits.extend(
            flux_consumer_compatibility_map_iterations(text, path=path)
        )
    if all_hits:
        joined = "\n".join(all_hits)
        raise AssertionError(
            "flux consumers must not iterate compatibility pressure maps "
            f"(VR-11 / DESIGN-REV5 §7.4):\n{joined}"
        )


def shadow_equal_is_proved(overlay: Mapping[str, Any] | None) -> bool | None:
    """Return True/False/None from an overlay; never default missing to True."""

    if not isinstance(overlay, Mapping) or not overlay:
        return None
    if "shadow_equal" in overlay:
        return bool(overlay["shadow_equal"])
    outcome = overlay.get("shadow_outcome")
    if outcome in _SHADOW_EQUAL_TRUE_OUTCOMES:
        return True
    if outcome is None:
        return None
    return False


def condensation_refusals_payload(
    refusals: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize condensation_refusals_by_species for consumers."""

    if not isinstance(refusals, Mapping) or not refusals:
        return {
            "schema": "condensation_refusals.v1",
            "n_species": 0,
            "by_species": {},
            "has_refusals": False,
        }
    by_species = {
        str(species): dict(record) if isinstance(record, Mapping) else {
            "status": "refused",
            "reason": "untyped",
            "raw": record,
        }
        for species, record in sorted(refusals.items())
    }
    return {
        "schema": "condensation_refusals.v1",
        "n_species": len(by_species),
        "by_species": by_species,
        "has_refusals": bool(by_species),
    }


# Operator T_cond override audit (setpoints.yaml) — VR-11 §7.2.
# Al and Ti are present as operator-routing estimates; no additional
# Al/Ti/trace overrides may be added without independent engineering policy.
AUDITED_OPERATOR_T_COND_SPECIES: frozenset[str] = frozenset(
    {"Fe", "SiO", "CrO2", "Mg", "Na", "K", "Ca", "Mn", "Cr", "Al", "Ti"}
)

SETPOINTS_T_COND_AUDIT: Mapping[str, Any] = MappingProxyType(
    {
        "schema": "setpoints_t_cond_audit.v1",
        "operator_override_species": sorted(AUDITED_OPERATOR_T_COND_SPECIES),
        "al_ti_policy": (
            "Al and Ti overrides are estimated/operator-routing only; "
            "no additional Al/Ti/trace T_cond overrides without independent "
            "engineering policy approval (DESIGN-REV5 §7.2)."
        ),
        "reaction_fixed_windows": (
            "Reaction-fixed condensation windows remain non-operator settings."
        ),
    }
)
