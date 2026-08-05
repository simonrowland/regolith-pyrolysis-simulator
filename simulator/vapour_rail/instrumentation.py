"""VR-11 instrumentation + VapourBatch flux cutover helpers.

U4 / VR-11 threads exact-key channel answers, validation/verdict status,
refusals, solve groups, activity bounds, source-boundary / anti-cliff
acquisition flags, and the nine-row advisory ceiling table through the
runner / artifact / UI surfaces.

Active evaporation flux consumes a complete VapourBatch for channel refusal,
eligibility, and set authority. Before RG-1, values cross one typed effective-
pressure seam from the equilibrium backend. Shadow equality is a measured
outcome (proved / mismatch / not-fixed / typed disagreement), never hardcoded.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any, Iterator

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


def _activity_payload(activity: Any) -> dict[str, Any] | None:
    if activity is None:
        return None
    standard_state = getattr(activity, "standard_state", None)
    standard_payload = None
    if standard_state is not None:
        standard_payload = {
            "convention": standard_state.convention,
            "phase": standard_state.phase,
            "reference_pressure_bar": float(
                standard_state.reference_pressure_bar
            ),
            "reference_temperature_K": standard_state.reference_temperature_K,
            "component_basis": standard_state.component_basis,
        }
    verdict = getattr(activity, "verdict", None)
    refusal_code = getattr(activity, "refusal_code", None)
    bound_direction = getattr(activity, "bound_direction", None)
    return {
        "component_id": activity.component_id,
        "value": activity.value,
        "verdict": getattr(verdict, "value", verdict),
        "bound_direction": getattr(bound_direction, "value", bound_direction),
        "reason": activity.reason,
        "standard_state": standard_payload,
        "phase_assemblage_ref": activity.phase_assemblage_ref,
        "chemical_potential_ref": activity.chemical_potential_ref,
        "state_fingerprint": activity.state_fingerprint,
        "solve_group_id": activity.solve_group_id,
        "provider": activity.provider,
        "authority": bool(activity.authority),
        "report_label": activity.report_label,
        "refusal_code": getattr(refusal_code, "value", refusal_code),
        "detail": activity.detail,
        "evidence_ref": activity.evidence_ref,
        "derivation": dict(activity.derivation),
    }


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
        "source_reaction_activity": _activity_payload(
            answer.source_reaction_activity
        ),
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
EFFECTIVE_PRESSURE_SOURCE_CONTEXT = "effective_pressure_source"

_COMPATIBILITY_PRESSURE_READ_CONTEXT: ContextVar[str | None] = ContextVar(
    "compatibility_pressure_read_context",
    default=None,
)


@contextmanager
def compatibility_pressure_read_context(context: str) -> Iterator[None]:
    """Label compatibility-map reads for the runtime anti-laundering guard."""

    token = _COMPATIBILITY_PRESSURE_READ_CONTEXT.set(str(context))
    try:
        yield
    finally:
        _COMPATIBILITY_PRESSURE_READ_CONTEXT.reset(token)


class CompatibilityPressureMapReadTripwire(Mapping[str, float]):
    """Prove compatibility reads occur only inside the named pre-RG seam."""

    def __init__(self, source: Mapping[str, float]) -> None:
        self._source = dict(source)
        self._reads: list[tuple[str, str]] = []

    def _record(self, operation: str) -> None:
        context = _COMPATIBILITY_PRESSURE_READ_CONTEXT.get() or "unscoped"
        self._reads.append((context, operation))

    def __getitem__(self, key: str) -> float:
        self._record("getitem")
        return self._source[key]

    def __iter__(self) -> Iterator[str]:
        self._record("iter")
        return iter(self._source)

    def __len__(self) -> int:
        self._record("len")
        return len(self._source)

    def read_count(self, context: str) -> int:
        return sum(1 for read_context, _ in self._reads if read_context == context)

    @property
    def reads(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._reads)


class EffectivePressureSource:
    """Typed value handoff consumed only after VapourBatch channel gating."""

    __slots__ = (
        "source_id",
        "species_ids",
        "physical_zero_reason",
        "_pressures_pa",
    )

    def __init__(
        self,
        source_id: str,
        pressures_pa: Mapping[str, float],
        *,
        physical_zero_reason: str | None = None,
    ) -> None:
        pressure_by_species: dict[str, float] = {}
        for species_id, pressure_pa in pressures_pa.items():
            value = float(pressure_pa)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(
                    "effective pressure must be finite and nonnegative: "
                    f"{species_id!r}={pressure_pa!r}"
                )
            pressure_by_species[str(species_id)] = value
        self.source_id = str(source_id)
        self.species_ids = frozenset(pressure_by_species)
        if pressure_by_species and physical_zero_reason is not None:
            raise ValueError(
                "non-empty effective-pressure source cannot prove physical zero"
            )
        self.physical_zero_reason = (
            str(physical_zero_reason) if physical_zero_reason is not None else None
        )
        self._pressures_pa = MappingProxyType(pressure_by_species)

    def pressure_pa(self, species_id: str) -> float | None:
        """Return one effective Pa value without exposing a map to consumers."""

        return self._pressures_pa.get(species_id)

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
    *,
    refuse_nonfinite: bool = True,
) -> frozenset[str]:
    """Canonical finite species set used by pre-RG projections.

    Default refuses non-finite entries rather than silently dropping them
    from the activation set (silent drop → species un-claimed → zero debit).
    """

    return frozenset(
        finite_live_pressure_map(
            live_pressures_Pa,
            refuse_nonfinite=refuse_nonfinite,
        )
    )


def finite_live_pressure_map(
    live_pressures_Pa: Mapping[str, float] | None,
    *,
    read_context: str | None = None,
    refuse_nonfinite: bool = True,
) -> dict[str, float]:
    """Read a finite compatibility projection under an explicit purpose label.

    Non-finite live pressures are not silently dropped from the typed seam:
    dropping would un-claim a species and debit it as zero. When
    ``refuse_nonfinite`` is True (default), any non-finite/unparseable value
    raises. The shadow comparator path uses :func:`_finite_live_map` directly
    and records drops as ``SHADOW_NONFINITE_LIVE``.
    """

    if read_context is None:
        live, dropped = _finite_live_map(live_pressures_Pa)
    else:
        with compatibility_pressure_read_context(read_context):
            live, dropped = _finite_live_map(live_pressures_Pa)
    if refuse_nonfinite and dropped:
        raise ValueError(
            "non-finite live pressures for species "
            f"{dropped}; refusing silent drop (unknown pressure is not "
            "zero pressure / un-claimed species)"
        )
    return live


def _pressures_equal(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_SHADOW_PA_RTOL, abs_tol=_SHADOW_PA_ATOL)


def _channel_flux_gate_state(answer: VapourAnswer) -> str:
    """Return the catalog-owned eligibility/refusal state for one channel."""

    pressure = answer.pressure
    flux = answer.flux
    if isinstance(pressure, PressureRefusal) or isinstance(flux, FluxRefusal):
        return "refusal"
    if isinstance(flux, FluxDiagnosticUpperBound) or isinstance(
        pressure, PressureUpperBound
    ):
        return "upper_bound"
    if isinstance(pressure, ZeroByPhysics):
        return "zero_by_physics"
    if isinstance(pressure, PressureValue) and isinstance(flux, FluxEligible):
        return "eligible"
    return "incomplete_channel"


def _channel_flux_pressure_pa(
    answer: VapourAnswer,
    *,
    effective_pressure_source: EffectivePressureSource,
) -> tuple[float | None, str]:
    """Apply channel authority, then read one value through the typed seam.

    Returns ``(pa_or_None, state)`` where ``pa`` is only set for inventory-
    debiting HKL (PressureValue + FluxEligible). Batch outcomes decide whether
    the pre-RG source may be read. The catalog point answer is a fallback only
    when an eligible channel has no seam value.
    """

    state = _channel_flux_gate_state(answer)
    if state == "zero_by_physics":
        return 0.0, "zero_by_physics"
    if state != "eligible":
        return None, state
    pa = effective_pressure_source.pressure_pa(answer.species_id)
    if pa is not None:
        return pa, "eligible"
    pressure = answer.pressure
    assert isinstance(pressure, PressureValue)
    pa = float(pressure.pa)
    if not math.isfinite(pa) or pa < 0.0:
        return None, "invalid_catalog_pressure"
    return pa, "eligible"


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
    effective_pressure_source: EffectivePressureSource,
    resolution_error: Mapping[str, Any] | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Build flux Pa after batch channel/set gates using one typed value seam.

    Active path: require batch, iterate ``requested_species_ids``, branch on
    catalog pressure/flux unions, enforce the batch-active set, then read values
    from ``effective_pressure_source``. An eligible point answer supplies a
    catalog fallback only when that seam has no value.
    Refusal/upper-bound/zero are typed non-debit states. Before RG-1 the source
    is the equilibrium backend; the batch remains channel/refusal/set authority.
    Extrapolated point estimates are named explicitly for status/degraded
    accounting regardless of which numeric source supplies their flux value.
    Absent batch or resolve error → empty flux map + typed failure report;
    this consumer accepts no anonymous compatibility mapping.
    """

    report: dict[str, Any] = {
        "schema": "vapour_batch_flux_overlay.v1",
        "batch_present": batch is not None,
        "n_flux_pressures": 0,
        "n_extrapolated_flux_species": 0,
        "extrapolated_flux_species": [],
        "batch_channel_states": {},
        "note": (
            "VapourBatch owns channel refusal, eligibility, and the active set; "
            "values cross the typed pre-RG seam when present; eligible point "
            "answers supply the catalog value only as a missing-seam fallback."
        ),
        "effective_pressure_source": effective_pressure_source.source_id,
        "effective_pressure_zero_reason": (
            effective_pressure_source.physical_zero_reason
        ),
    }

    if resolution_error:
        report["resolution_error"] = dict(resolution_error)
        report["selection_source"] = "typed_failure_resolution_error"
        return {}, report

    if batch is None:
        report["selection_source"] = "typed_failure_missing_batch"
        return {}, report

    batch_active_species_ids = batch.flux_active_species_ids
    source_species_ids = effective_pressure_source.species_ids
    missing_source_species_ids = batch_active_species_ids - source_species_ids
    out_of_range_active_species_ids = frozenset(
        species_id
        for species_id in batch_active_species_ids
        if (
            (answer := batch.channels_by_species.get(species_id)) is not None
            and bool(dict(answer.extra).get("out_of_range", False))
        )
    )
    catalog_continuation_fallback_species_ids = (
        missing_source_species_ids & out_of_range_active_species_ids
    )
    missing_source_species = sorted(missing_source_species_ids)
    unresolved_missing_source_species = sorted(
        missing_source_species_ids - catalog_continuation_fallback_species_ids
    )
    extra_source_species = sorted(source_species_ids - batch_active_species_ids)
    report["missing_effective_pressure_species"] = missing_source_species
    report["catalog_continuation_flux_species"] = sorted(
        catalog_continuation_fallback_species_ids
    )
    report["effective_pressure_species_not_batch_active"] = extra_source_species
    # Only OOD point answers may bridge a genuine seam absence with their
    # catalog continuation. An in-domain seam gap remains a typed failure:
    # accepting its catalog point would be an implicit RG-1 value cutover.
    if unresolved_missing_source_species:
        report["selection_source"] = (
            "typed_failure_effective_pressure_species_set_mismatch"
        )
        return {}, report
    # Source extras that the batch marks as genuine upper bounds are expected
    # and must not empty the whole flux map.
    if extra_source_species:
        report["demoted_effective_pressure_species"] = list(extra_source_species)

    report["selection_source"] = effective_pressure_source.source_id
    flux_pressures: dict[str, float] = {}
    batch_pa_by_species: dict[str, float] = {}
    selected_runtime_pa_by_species: dict[str, float] = {}
    selected_pressure_source_by_species: dict[str, str] = {}
    extrapolated_flux_species: list[str] = []
    channel_states: dict[str, str] = {}
    missing_channel_keys: list[str] = []

    for species_id in sorted(batch.requested_species_ids):
        answer = batch.channels_by_species.get(species_id)
        if answer is None:
            missing_channel_keys.append(species_id)
            channel_states[species_id] = "missing_channel"
            continue
        catalog_pressure = answer.pressure
        if isinstance(catalog_pressure, PressureValue):
            catalog_pa = float(catalog_pressure.pa)
            if math.isfinite(catalog_pa):
                batch_pa_by_species[species_id] = catalog_pa
        gate_state = _channel_flux_gate_state(answer)
        if (
            gate_state in {"eligible", "zero_by_physics"}
            and species_id not in batch_active_species_ids
        ):
            channel_states[species_id] = "dormant_by_epoch"
            continue
        selected_pa, state = _channel_flux_pressure_pa(
            answer,
            effective_pressure_source=effective_pressure_source,
        )
        channel_states[species_id] = state
        if state == "eligible" and selected_pa is not None:
            selected_runtime_pa_by_species[species_id] = float(selected_pa)
            flux_pressures[species_id] = float(selected_pa)
            if bool(dict(answer.extra).get("out_of_range", False)):
                extrapolated_flux_species.append(species_id)
            if species_id not in catalog_continuation_fallback_species_ids:
                selected_pressure_source_by_species[species_id] = (
                    effective_pressure_source.source_id
                )
            else:
                selected_pressure_source_by_species[species_id] = (
                    "vapour_batch_catalog_continuation"
                )
        elif state == "zero_by_physics":
            selected_runtime_pa_by_species[species_id] = 0.0
            flux_pressures[species_id] = 0.0
            selected_pressure_source_by_species[species_id] = "zero_by_physics"
        # refusal / upper_bound / nonfinite / dormant eligible: no debit

    report["batch_channel_states"] = channel_states
    report["batch_pa_by_species"] = batch_pa_by_species
    report["selected_runtime_pa_by_species"] = selected_runtime_pa_by_species
    report["selected_pressure_source_by_species"] = (
        selected_pressure_source_by_species
    )
    report["missing_batch_keys"] = missing_channel_keys
    report["n_flux_pressures"] = len(flux_pressures)
    report["extrapolated_flux_species"] = extrapolated_flux_species
    report["n_extrapolated_flux_species"] = len(extrapolated_flux_species)

    return flux_pressures, report


def compare_live_shadow_to_batch_flux(
    *,
    batch: VapourBatch | None,
    live_pressures_Pa: Mapping[str, float] | None,
    batch_flux_pressures_Pa: Mapping[str, float],
    resolution_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare an independently computed legacy map with batch-driven flux.

    This shadow surface accepts an independently captured compatibility map.
    It never selects or returns flux-driving pressures; the distinct typed seam
    supplies pre-RG values only after batch gating.
    """

    if live_pressures_Pa is None:
        shadow_outcome = (
            SHADOW_RESOLUTION_ERROR
            if resolution_error
            else SHADOW_ABSENT_COMPARISON
        )
        return {
            "shadow_equal": False,
            "shadow_outcome": shadow_outcome,
            "detail": (
                dict(resolution_error)
                if resolution_error
                else "independent compatibility comparison evidence is absent"
            ),
            "mismatched_species": [],
            "missing_in_batch_path": [],
            "missing_in_legacy_path": [],
            "n_live_pressures": 0,
            "live_only_bridge_species": [],
            "batch_refused_live_species": [],
            "batch_flux_active_not_in_live": (
                sorted(batch.flux_active_species_ids) if batch is not None else []
            ),
            "live_flux_active_not_in_batch": [],
            "dropped_nonfinite_live_species": [],
            "catalog_pa_shadow_equal": False,
            "catalog_pa_shadow_outcome": shadow_outcome,
        }

    live, dropped_nonfinite = _finite_live_map(live_pressures_Pa)
    refused_live: list[str] = []
    missing_channel_keys: list[str] = []
    catalog_pa_by_species: dict[str, float] = {}
    # Genuine catalog upper bounds demote seam claims. Live may still carry
    # those species as legacy pressures; they are not batch flux-active and
    # must not fail the activation-set shadow proof.
    demoted_upper_bound: set[str] = set()

    if batch is not None:
        for species_id in sorted(batch.requested_species_ids):
            answer = batch.channels_by_species.get(species_id)
            if answer is None:
                missing_channel_keys.append(species_id)
                continue
            if answer.is_refused and live.get(species_id, 0.0) > 0.0:
                refused_live.append(species_id)
            if _channel_flux_gate_state(answer) == "upper_bound":
                demoted_upper_bound.add(species_id)
            if (
                species_id in batch.flux_active_species_ids
                and isinstance(answer.pressure, PressureValue)
            ):
                catalog_pa = float(answer.pressure.pa)
                if math.isfinite(catalog_pa):
                    catalog_pa_by_species[species_id] = catalog_pa

    legacy_active_ids = (
        tuple(sid for sid in live if sid not in demoted_upper_bound)
        if batch is not None
        else None
    )
    comparison = compare_legacy_vs_batch_flux_paths(
        legacy_pressures_Pa={
            sid: pa for sid, pa in live.items() if sid not in demoted_upper_bound
        },
        batch_flux_pressures_Pa=batch_flux_pressures_Pa,
        legacy_flux_active_species_ids=legacy_active_ids,
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
            "demoted_upper_bound_species": sorted(demoted_upper_bound),
            "live_only_bridge_species": sorted(
                set(live) - set(batch_flux_pressures_Pa) - demoted_upper_bound
            ),
            "batch_refused_live_species": refused_live,
            "batch_flux_active_not_in_live": sorted(
                set(batch.flux_active_species_ids) - set(live)
            )
            if batch is not None
            else [],
            "live_flux_active_not_in_batch": sorted(
                (set(live) - demoted_upper_bound) - set(batch.flux_active_species_ids)
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
# Source guard: compatibility maps never reach a flux consumer as mappings
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

# Any controls.get of the compatibility key in a flux consumer kernel is banned;
# the kernel reads only the already batch-gated effective-pressure key.
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
    return hits


def assert_no_flux_consumer_iterates_compatibility_maps(
    sources: Mapping[str, str],
) -> None:
    """Hard-fail when a compatibility mapping bypasses the typed value seam."""

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
            "outside the "
            "typed effective-pressure seam "
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
