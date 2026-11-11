"""Exact-key vapour batch surface consumed by later flux cutover (VR-11).

DESIGN-REV5 §1.2: the invariant is

    channels_by_species.keys() == requested_species_ids

A missing requested key hard-fails; it can never mean physical zero.
Callers never construct or narrow ``requested_species_ids`` — the catalog
request builder is the sole constructor of that frozen set.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from simulator.vapour_rail.activity import SourceReactionActivity


class IncompleteVapourBatchError(KeyError):
    """Raised when a requested channel is absent from the exact-key batch."""


class VapourRequestConstructionError(ValueError):
    """Raised when a caller tries to narrow or invent a request set."""


FLUX_ACTIVATION_EPOCH_PRE_RG = "pre_rg_legacy_live"
FLUX_ACTIVATION_EPOCH_RG_MANIFEST = "rg_manifest_union"
_FLUX_ACTIVATION_EPOCHS = frozenset(
    {FLUX_ACTIVATION_EPOCH_PRE_RG, FLUX_ACTIVATION_EPOCH_RG_MANIFEST}
)


@dataclass(frozen=True)
class FluxActivationContext:
    """Epoch authority for the flux-active subset of an exact-key batch.

    Requested channels remain comprehensive. Before RG-1, the typed effective-
    pressure seam supplies only its finite species set; catalog refusal closure
    proves those channels eligible and the batch freezes the active set. Pressure
    values never enter this context.
    """

    epoch: str
    effective_pressure_species_ids: frozenset[str] = frozenset()
    # Flagged alpha=1 admission. None with the flag set means every species
    # (the provider's missing-allowlist meaning). A set admits only those ids.
    # Pressure values still never enter this context.
    allow_unmeasured_alpha_fallback: bool = False
    unmeasured_alpha_fallback_species: frozenset[str] | None = None

    def __post_init__(self) -> None:
        epoch = str(self.epoch)
        if epoch not in _FLUX_ACTIVATION_EPOCHS:
            raise ValueError(f"unknown flux activation epoch: {epoch!r}")
        if not isinstance(self.allow_unmeasured_alpha_fallback, bool):
            raise TypeError("allow_unmeasured_alpha_fallback must be bool")
        effective_species = frozenset(
            str(species_id) for species_id in self.effective_pressure_species_ids
        )
        if epoch == FLUX_ACTIVATION_EPOCH_RG_MANIFEST and effective_species:
            raise ValueError(
                "rg_manifest_union activation may not carry pre-RG effective "
                "pressure species"
            )
        raw_allowlist = self.unmeasured_alpha_fallback_species
        if raw_allowlist is not None:
            raw_allowlist = frozenset(str(species_id) for species_id in raw_allowlist)
        object.__setattr__(self, "epoch", epoch)
        object.__setattr__(
            self,
            "effective_pressure_species_ids",
            effective_species,
        )
        object.__setattr__(
            self,
            "unmeasured_alpha_fallback_species",
            raw_allowlist,
        )


# ---------------------------------------------------------------------------
# Pressure tagged union
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PressureValue:
    """Point equilibrium / saturation pressure (Pa)."""

    pa: float


@dataclass(frozen=True)
class PressureUpperBound:
    """Non-mutating screening upper bound (Pa)."""

    pa: float
    evidence_ref: str


@dataclass(frozen=True)
class ZeroByPhysics:
    """Physically justified zero pressure; requires evidence."""

    evidence_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_ref, str) or not self.evidence_ref.strip():
            raise ValueError("ZeroByPhysics requires non-empty evidence_ref")


@dataclass(frozen=True)
class PressureRefusal:
    """Typed pressure refusal; never coerces to zero."""

    code: str
    detail: str


PressureOutcome = (
    PressureValue | PressureUpperBound | ZeroByPhysics | PressureRefusal
)


# ---------------------------------------------------------------------------
# Flux tagged union
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FluxEligible:
    """HKL-eligible flux path with alpha and optional reaction."""

    alpha_ref: str
    reaction_id: str | None = None


@dataclass(frozen=True)
class FluxDiagnosticUpperBound:
    """Screening-only flux bound; may not debit inventory."""

    alpha_ref: str
    reaction_id: str | None = None


@dataclass(frozen=True)
class FluxRefusal:
    """Typed flux refusal on the same channel record as pressure."""

    code: str
    detail: str


FluxOutcome = FluxEligible | FluxDiagnosticUpperBound | FluxRefusal


# Verdict / certification ceilings (owner-ratified O1; VR-2)
VERDICT_STATUS_BEARING_NON_AUTHORITATIVE = "status_bearing_non_authoritative"
VERDICT_AUTHORITATIVE = "authoritative"
CERTIFICATION_CEILING_NEVER = "never"


@dataclass(frozen=True)
class VapourAnswer:
    """One exact-key channel answer for a requested gas species.

    ``selected_runtime_pressure`` is the selected value consumed by the flux
    path after this answer's pressure and flux outcomes authorize a debit.
    """

    species_id: str
    pressure: PressureOutcome
    selected_runtime_pressure: PressureOutcome
    flux: FluxOutcome
    source_label: str
    formula_id: str
    source_account: str
    solve_group_id: str
    state_fingerprint: str
    validation_status: str
    validation_anchor_refs: tuple[str, ...] = ()
    verdict_status: str = VERDICT_STATUS_BEARING_NON_AUTHORITATIVE
    certification_ceiling: str = CERTIFICATION_CEILING_NEVER
    refusal_code: str | None = None
    extra: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    source_reaction_activity: SourceReactionActivity | None = None
    source_reaction_activity_shadow: SourceReactionActivity | None = None
    source_reaction_activity_shadow_evaluation: Mapping[str, Any] | None = None

    @property
    def is_refused(self) -> bool:
        return isinstance(self.pressure, PressureRefusal) or isinstance(
            self.flux, FluxRefusal
        )

    @property
    def is_flux_active(self) -> bool:
        return isinstance(self.flux, FluxEligible) and isinstance(
            self.pressure, PressureValue
        )


def recount_published_refusal_metadata(
    metadata: Mapping[str, Any],
    channels: Mapping[str, Any],
) -> dict[str, Any]:
    """Return ``metadata`` with ``n_refused`` counted from ``channels``.

    INVARIANT: ``n_refused`` is the number of channels whose ``is_refused``
    is true, on the batch and on every publisher that copies this metadata.
    Request resolution snapshots the count. A later rewrite that turns a
    refusal into an authorized physical zero must publish this recount, or
    the runner reports the corrected serialization count beside the old
    one. Other keys are request facts and are copied unchanged.
    ``n_flux_active``, ``n_solve_bundles``, and ``n_flux_dormant_by_epoch``
    are not functions of ``is_refused`` alone. The caller updates those
    only when it also changes those sets.
    """

    recounted = dict(metadata)
    recounted["n_refused"] = sum(
        1 for answer in channels.values() if answer.is_refused
    )
    return recounted


def _freeze_channels(
    channels: Mapping[str, VapourAnswer],
) -> MappingProxyType[str, VapourAnswer]:
    return MappingProxyType(dict(channels))


@dataclass(frozen=True)
class VapourBatch:
    """Exact-key mapping from requested gas IDs to channel answers.

    Construction validates ``channels_by_species.keys() == requested_species_ids``.
    The only production constructor is
    :meth:`simulator.vapour_rail.catalog.VapourRailCatalog.resolve_batch`.
    """

    requested_species_ids: frozenset[str]
    channels_by_species: Mapping[str, VapourAnswer]
    solve_bundle_ids: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    flux_active_species_ids: frozenset[str] = frozenset()
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    # t-568 Phase 1 diagnostic surface. Existing serializers intentionally omit
    # it so runner/head goldens stay bit-identical.
    melt_activity_shadow: Any | None = None

    def __post_init__(self) -> None:
        requested = frozenset(self.requested_species_ids)
        channels = _freeze_channels(self.channels_by_species)
        object.__setattr__(self, "requested_species_ids", requested)
        object.__setattr__(self, "channels_by_species", channels)
        object.__setattr__(
            self,
            "solve_bundle_ids",
            MappingProxyType(
                {
                    str(bundle_id): frozenset(members)
                    for bundle_id, members in dict(self.solve_bundle_ids).items()
                }
            ),
        )
        object.__setattr__(
            self, "flux_active_species_ids", frozenset(self.flux_active_species_ids)
        )
        object.__setattr__(
            self, "metadata", MappingProxyType(dict(self.metadata))
        )

        channel_keys = frozenset(channels)
        if channel_keys != requested:
            missing = sorted(requested - channel_keys)
            extra = sorted(channel_keys - requested)
            raise IncompleteVapourBatchError(
                "channels_by_species.keys() must equal requested_species_ids; "
                f"missing={missing}, extra={extra}"
            )
        for species_id, answer in channels.items():
            if answer.species_id != species_id:
                raise IncompleteVapourBatchError(
                    f"channel key {species_id!r} does not match "
                    f"answer.species_id {answer.species_id!r}"
                )

    def channel(self, species_id: str) -> VapourAnswer:
        """Return the exact-key answer; missing keys hard-fail."""

        try:
            return self.channels_by_species[species_id]
        except KeyError as exc:
            raise IncompleteVapourBatchError(
                f"requested vapour channel {species_id!r} missing from batch"
            ) from exc

    def __contains__(self, species_id: object) -> bool:
        return (
            isinstance(species_id, str)
            and species_id in self.requested_species_ids
        )

    def __iter__(self) -> Iterable[str]:
        return iter(sorted(self.requested_species_ids))

    def __len__(self) -> int:
        return len(self.requested_species_ids)
