"""Pure fouling-lifecycle projection over terminal wall-deposit exports.

Phase A is an overlay. It reads completed run exports and never writes a
ledger, seeds a simulator, or changes per-run output.

Phase A campaigns are independent runs. Each constituent run starts with an
empty wall state, so its terminal export is already that run's net deposit.
The projection layer accumulates those exports:

    cumulative(N) = cumulative(N - 1) + terminal_export(N)

Remobilization has already been folded into each per-run terminal net by the
simulator. ``export_includes_carried=True`` remains an optional non-default
path for a future warm-state exporter where a terminal export includes prior
carried wall state and the per-run net must be ``export - carried``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from simulator.diagnostics import (
    _status_bearing_alpha_record,
    wall_deposit_sticking_authority_status,
)
from simulator.scalar_boundary import is_declared_real_scalar
from simulator.wall_deposit_availability import (
    WALL_SATURATION_PRESSURE_REFUSAL_EVENTS,
    wall_deposit_figure_availability,
    wall_deposit_refusal_events,
)
from simulator.vapour_rail.instrumentation import (
    merge_public_notice,
    merge_vapour_carrier_lineage,
    public_carrier_notices,
    vapour_carrier_authority_severity,
    vapour_carrier_authority_status,
)


GROUNDING_UNGROUNDED = "ungrounded_threshold"
GROUNDING_PROVISIONAL = "PROVISIONAL"
THICKNESS_PROXY_LIMITER = "thickness_proxy_vs_placeholder"
INTERFACE_FIRST_MELT_LIMITER = "interface_first_melt"
EPSILON_KG = 1.0e-12


class FoulingProjectionError(ValueError):
    """Raised when a projection input is malformed or physically impossible."""


NestedDeposit = Mapping[str, Mapping[str, float]]
Limiter = Callable[["FoulingTerminalSnapshot", int], "LimiterEvaluation"]


@dataclass(frozen=True)
class FoulingTerminalSnapshot:
    """Immutable terminal wall-deposit export for one completed run.

    ``c4b_binding_substrate_state`` is an optional deferred seam for the
    condensation export. Phase A does not harvest live condensation-model state;
    callers may pass an already-exported read-only payload when one exists.
    """

    wall_deposit_by_segment_species_kg: NestedDeposit
    wall_deposit_sticking_authority: Mapping[str, Any] | None = None
    grounding_status: str = GROUNDING_UNGROUNDED
    threshold_params: Mapping[str, Any] | None = None
    c4b_binding_substrate_state: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "wall_deposit_by_segment_species_kg",
            _freeze_nested_deposit(self.wall_deposit_by_segment_species_kg),
        )
        object.__setattr__(
            self,
            "wall_deposit_sticking_authority",
            None
            if self.wall_deposit_sticking_authority is None
            else _freeze_value(self.wall_deposit_sticking_authority),
        )
        object.__setattr__(
            self,
            "threshold_params",
            None if self.threshold_params is None else _freeze_value(self.threshold_params),
        )
        object.__setattr__(
            self,
            "c4b_binding_substrate_state",
            None
            if self.c4b_binding_substrate_state is None
            else _freeze_value(self.c4b_binding_substrate_state),
        )

    @classmethod
    def from_trace(
        cls,
        trace: Any,
        *,
        grounding_status: str = GROUNDING_UNGROUNDED,
        threshold_params: Mapping[str, Any] | None = None,
        c4b_binding_substrate_state: Mapping[str, Any] | None = None,
    ) -> "FoulingTerminalSnapshot":
        """Harvest existing read-only trace deposit and sticking authority."""

        if not hasattr(trace, "wall_deposit_by_segment_species_kg"):
            raise FoulingProjectionError(
                "trace missing wall_deposit_by_segment_species_kg export"
            )

        return cls(
            wall_deposit_by_segment_species_kg=_coerce_nested_deposit(
                getattr(trace, "wall_deposit_by_segment_species_kg")
            ),
            wall_deposit_sticking_authority=getattr(
                trace,
                "wall_deposit_sticking_authority",
                None,
            ),
            grounding_status=str(grounding_status),
            threshold_params=threshold_params,
            c4b_binding_substrate_state=c4b_binding_substrate_state,
        )

    def deposit_plain(self) -> dict[str, dict[str, float]]:
        return _plain_nested_deposit(self.wall_deposit_by_segment_species_kg)


@dataclass(frozen=True)
class FoulingMergeResult:
    trajectory: tuple[FoulingTerminalSnapshot, ...]
    per_run_net_deposit_by_segment_species_kg: tuple[NestedDeposit, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "trajectory", tuple(self.trajectory))
        object.__setattr__(
            self,
            "per_run_net_deposit_by_segment_species_kg",
            tuple(
                _freeze_nested_deposit(item)
                for item in self.per_run_net_deposit_by_segment_species_kg
            ),
        )


@dataclass(frozen=True)
class LimiterEvaluation:
    name: str
    fired: bool
    segment: str | None = None
    value: float | None = None
    limit: float | None = None
    campaign_index: int | None = None
    refusal: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LifecycleProjection:
    service_life_campaigns: float | None
    worst_segment_campaigns_provisional: float | None
    cascade_knee_provisional: int | None
    grounding_status: str
    service_life_authoritative: bool
    limiter_fired: str | None
    end_condition_stack: tuple[str, ...] = (THICKNESS_PROXY_LIMITER,)
    interface_first_melt_diagnostic: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "service_life_campaigns": self.service_life_campaigns,
            "worst_segment_campaigns_provisional": (
                self.worst_segment_campaigns_provisional
            ),
            "cascade_knee_provisional": self.cascade_knee_provisional,
            "grounding_status": self.grounding_status,
            "service_life_authoritative": self.service_life_authoritative,
            "limiter_fired": self.limiter_fired,
            "end_condition_stack": list(self.end_condition_stack),
        }
        if self.interface_first_melt_diagnostic is not None:
            result["interface_first_melt"] = dict(
                self.interface_first_melt_diagnostic
            )
        return result


@dataclass(frozen=True)
class CampaignsToResinterTotal:
    value: float | str | None
    authoritative_for_resinter: bool
    notices: tuple[Mapping[str, Any], ...] = ()
    refusal: Mapping[str, Any] | None = None
    measured_empty_deposit: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "notices", tuple(_freeze_value(item) for item in self.notices))
        if self.refusal is not None:
            refusal = _freeze_value(self.refusal)
            reason = refusal.get("reason") if isinstance(refusal, Mapping) else None
            authority = refusal.get("authority") if isinstance(refusal, Mapping) else None
            count = refusal.get("count") if isinstance(refusal, Mapping) else None
            if (
                not isinstance(reason, str)
                or not reason.strip()
                or not isinstance(authority, Mapping)
                or not authority
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                raise FoulingProjectionError(
                    "resinter refusal requires a reason, authority, and non-negative count"
                )
            object.__setattr__(self, "refusal", refusal)
        if (self.value is None) != (self.refusal is not None):
            raise FoulingProjectionError(
                "unavailable campaigns_to_resinter requires a refusal, and a refusal requires None"
            )
        if self.value == "infinite" and not self.measured_empty_deposit:
            raise FoulingProjectionError(
                "infinite campaigns_to_resinter requires a measured empty deposit"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "value": self.value,
            "authoritative_for_resinter": self.authoritative_for_resinter,
            "notices": [_plain_authority_mapping(item) for item in self.notices],
        }
        if self.refusal is not None:
            payload["refusal"] = _plain_authority_mapping(self.refusal)
        return payload


def merge_run_snapshot(
    carried_projection: FoulingTerminalSnapshot | None,
    run_export: FoulingTerminalSnapshot,
    *,
    export_includes_carried: bool = False,
    campaign_index: int | None = None,
) -> tuple[FoulingTerminalSnapshot, NestedDeposit]:
    """Return ``(post_merge_snapshot, per_run_net_deposit)``."""

    carried = (
        {}
        if carried_projection is None
        else carried_projection.wall_deposit_by_segment_species_kg
    )
    exported = run_export.wall_deposit_by_segment_species_kg
    per_run_net = (
        _subtract_deposits(exported, carried)
        if export_includes_carried
        else _plain_nested_deposit(exported)
    )
    cumulative = _add_deposits(carried, per_run_net)
    merged_authority = _merged_wall_deposit_sticking_authority(
        carried_projection.wall_deposit_sticking_authority
        if carried_projection is not None
        else None,
        run_export.wall_deposit_sticking_authority,
        campaign_index=campaign_index,
    )
    authority = wall_deposit_sticking_authority_status(
        cumulative,
        merged_authority,
    )
    refusal_events = merged_authority.get(
        WALL_SATURATION_PRESSURE_REFUSAL_EVENTS
    )
    if refusal_events:
        authority[WALL_SATURATION_PRESSURE_REFUSAL_EVENTS] = refusal_events
    return (
        FoulingTerminalSnapshot(
            wall_deposit_by_segment_species_kg=cumulative,
            wall_deposit_sticking_authority=authority,
            grounding_status=run_export.grounding_status,
            threshold_params=run_export.threshold_params,
            c4b_binding_substrate_state=run_export.c4b_binding_substrate_state,
        ),
        _freeze_nested_deposit(per_run_net),
    )


def _distinct_public_notices(
    carriers: Sequence[Any],
) -> dict[str, list[dict[str, Any]]]:
    """Every distinct notice across carriers, in encounter order.

    Filtering and exact-duplicate collapse belong to ``merge_public_notice``.
    The sticking-authority record then stores that list through the authority
    copier, which turns tuples into lists. The public helper keeps tuples.
    """

    collected: dict[str, list[dict[str, Any]]] = {}
    for carrier in carriers:
        if not isinstance(carrier, Mapping):
            continue
        for key, items in public_carrier_notices(carrier).items():
            for item in items:
                merge_public_notice(collected, key, item)
    return {
        key: [_plain_authority_mapping(item) for item in items]
        for key, items in collected.items()
    }


def _merged_wall_deposit_sticking_authority(
    carried_authority: Mapping[str, Any] | None,
    run_authority: Mapping[str, Any] | None,
    *,
    campaign_index: int | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    refusal_events = wall_deposit_refusal_events(carried_authority)
    refusal_events.extend(
        wall_deposit_refusal_events(
            run_authority,
            campaign=campaign_index,
        )
    )
    for authority in (carried_authority, run_authority):
        if not isinstance(authority, Mapping):
            continue

        raw_alpha = authority.get("alpha_s_provenance_by_species")
        if isinstance(raw_alpha, Mapping):
            alpha_by_species = merged.setdefault(
                "alpha_s_provenance_by_species",
                {},
            )
            if isinstance(alpha_by_species, dict):
                for species, by_segment in raw_alpha.items():
                    if not isinstance(by_segment, Mapping):
                        continue
                    current_by_segment = alpha_by_species.setdefault(
                        str(species),
                        {},
                    )
                    if not isinstance(current_by_segment, dict):
                        continue
                    for segment, raw_record in by_segment.items():
                        if not isinstance(raw_record, Mapping):
                            continue
                        record = _plain_authority_mapping(raw_record)
                        current = current_by_segment.get(str(segment))
                        if (
                            not isinstance(current, Mapping)
                            or (
                                not _status_bearing_alpha_record(current)
                                and _status_bearing_alpha_record(record)
                            )
                        ):
                            current_by_segment[str(segment)] = record

        for key in (
            "wall_saturation_pressure_refusals_by_species",
            "wall_saturation_pressure_extrapolations_by_species",
            "evaporation_transport_notices_by_species",
        ):
            raw_records = authority.get(key)
            if isinstance(raw_records, Mapping):
                by_species = merged.setdefault(key, {})
                if isinstance(by_species, dict):
                    for species, by_segment in raw_records.items():
                        if not isinstance(by_segment, Mapping):
                            continue
                        current = by_species.setdefault(str(species), {})
                        if isinstance(current, dict):
                            current.update(_plain_authority_mapping(by_segment))

        raw_carriers = authority.get("vapour_carrier_authority_by_species")
        if isinstance(raw_carriers, Mapping):
            carriers_by_species = merged.setdefault(
                "vapour_carrier_authority_by_species",
                {},
            )
            if isinstance(carriers_by_species, dict):
                for species, raw_record in raw_carriers.items():
                    if not isinstance(raw_record, Mapping):
                        continue
                    species_key = str(species)
                    record = _plain_authority_mapping(raw_record)
                    current = carriers_by_species.get(species_key)
                    current_status = vapour_carrier_authority_status(
                        current,
                        expected_species_id=species_key,
                    )
                    record_status = vapour_carrier_authority_status(
                        record,
                        expected_species_id=species_key,
                    )
                    if (
                        not isinstance(current, Mapping)
                        or vapour_carrier_authority_severity(record_status)
                        > vapour_carrier_authority_severity(current_status)
                    ):
                        carriers_by_species[species_key] = record
                    # One list on the singular key. Distinct disclosures
                    # are kept; exact duplicates are not.
                    collected = _distinct_public_notices((current, record))
                    if collected:
                        extra = carriers_by_species[species_key].setdefault(
                            "extra", {}
                        )
                        if isinstance(extra, dict):
                            for key, items in collected.items():
                                extra[key] = items
        raw_lineage = authority.get(
            "vapour_carrier_lineage_by_deposited_species"
        )
        if isinstance(raw_lineage, Mapping):
            lineage = merged.setdefault(
                "vapour_carrier_lineage_by_deposited_species",
                {},
            )
            if isinstance(lineage, dict):
                for product_species, carrier_species in raw_lineage.items():
                    product_key = str(product_species)
                    lineage[product_key] = merge_vapour_carrier_lineage(
                        lineage.get(product_key),
                        carrier_species,
                    )
    if refusal_events:
        merged[WALL_SATURATION_PRESSURE_REFUSAL_EVENTS] = refusal_events
    return merged


def _plain_authority_mapping(value: Mapping[Any, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, Mapping):
            result[str(key)] = _plain_authority_mapping(item)
        elif isinstance(item, (list, tuple)):
            result[str(key)] = [
                _plain_authority_mapping(element)
                if isinstance(element, Mapping)
                else element
                for element in item
            ]
        else:
            result[str(key)] = item
    return result


def merge_snapshot_sequence(
    run_exports: Sequence[FoulingTerminalSnapshot],
    *,
    export_includes_carried: bool = False,
) -> FoulingMergeResult:
    carried: FoulingTerminalSnapshot | None = None
    trajectory: list[FoulingTerminalSnapshot] = []
    per_run_net: list[NestedDeposit] = []
    for campaign_index, run_export in enumerate(run_exports, start=1):
        carried, net = merge_run_snapshot(
            carried,
            run_export,
            export_includes_carried=export_includes_carried,
            campaign_index=campaign_index,
        )
        trajectory.append(carried)
        per_run_net.append(net)
    return FoulingMergeResult(
        trajectory=tuple(trajectory),
        per_run_net_deposit_by_segment_species_kg=tuple(per_run_net),
    )


def phase_a_end_condition_stack(
    *,
    thickness_limit_m: float | None,
    rho_deposit_kg_m3: float | Mapping[str, float] | None,
    segment_area_m2: Mapping[str, float],
    interface_first_melt: Limiter | None = None,
) -> tuple[Limiter, ...]:
    stack: list[Limiter] = []
    if interface_first_melt is not None:
        stack.append(interface_first_melt)
    stack.append(
        thickness_proxy_vs_placeholder(
            thickness_limit_m=thickness_limit_m,
            rho_deposit_kg_m3=rho_deposit_kg_m3,
            segment_area_m2=segment_area_m2,
        )
    )
    return tuple(stack)


def interface_first_melt_limiter(
    evaluate: Limiter,
) -> Limiter:
    """Adapt a pure liner-corrosion evaluation to the lifecycle limiter stack."""

    def normalized(
        snapshot: FoulingTerminalSnapshot,
        campaign_index: int,
    ) -> LimiterEvaluation:
        try:
            result = evaluate(snapshot, campaign_index)
        except Exception as exc:  # noqa: BLE001 - diagnostic cannot gate lifecycle
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
                refusal={
                    "reason": str(
                        getattr(
                            exc,
                            "reason",
                            "interface_first_melt_evaluation_failed",
                        )
                    ),
                    "detail": str(exc),
                },
            )
        if not isinstance(result, LimiterEvaluation):
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
                refusal={
                    "reason": "interface_first_melt_result_invalid",
                    "detail": "evaluator must return LimiterEvaluation",
                },
            )
        return LimiterEvaluation(
            name=INTERFACE_FIRST_MELT_LIMITER,
            fired=bool(result.fired),
            segment=result.segment,
            value=result.value,
            limit=result.limit,
            campaign_index=(
                result.campaign_index
                if result.campaign_index is not None
                else campaign_index
            ),
            refusal=result.refusal,
        )

    return normalized


def _first_liner_state_refusal(raw_state: Mapping[str, Any]) -> dict[str, Any] | None:
    refusal = raw_state.get("refusal")
    if isinstance(refusal, Mapping) and refusal.get("reason"):
        return dict(refusal)
    refusals = raw_state.get("refusals", ())
    if isinstance(refusals, Sequence) and not isinstance(refusals, (str, bytes)):
        for item in refusals:
            if isinstance(item, Mapping) and item.get("reason"):
                return dict(item)
    return None


def _interface_first_melt_from_liner_states(
    liner_states_by_campaign: Sequence[Mapping[str, Any] | None],
) -> Limiter:
    """Fire on the first d-419 onset in each campaign's carried liner state."""

    first_state_index = next(
        (
            index
            for index, liner_state in enumerate(liner_states_by_campaign)
            if isinstance(liner_state, Mapping)
        ),
        None,
    )

    def evaluate(
        _snapshot: FoulingTerminalSnapshot,
        campaign_index: int,
    ) -> LimiterEvaluation:
        position = campaign_index - 1
        if first_state_index is None:
            refusal = {
                "reason": "liner_state_missing",
                "detail": "no campaign supplied liner-state-v1 metadata",
            }
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
                refusal=refusal,
            )
        if position < first_state_index:
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
            )
        if position >= len(liner_states_by_campaign) or not isinstance(
            liner_states_by_campaign[position], Mapping
        ):
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
                refusal={
                    "reason": "liner_state_missing_for_campaign",
                    "detail": f"campaign {campaign_index} has no liner-state-v1 metadata",
                },
            )
        liner_state = liner_states_by_campaign[position]
        segments = liner_state.get("segments")
        if not isinstance(segments, Mapping):
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
                refusal={
                    "reason": "liner_state_segments_missing",
                    "detail": "liner-state-v1 has no segment records",
                },
            )
        from simulator.liner_corrosion import deposit_oxide_equivalent_mass_by_oxide

        cumulative_by_segment = _snapshot.deposit_plain()
        fired_segments: list[str] = []
        refusals: list[dict[str, Any]] = []
        evaluated_segments: list[str] = []
        no_onset_segments: list[str] = []
        for raw_name, raw_state in segments.items():
            segment = str(raw_name)
            if not isinstance(raw_state, Mapping):
                continue
            if raw_state.get("status") == "refused":
                refusals.append(
                    _first_liner_state_refusal(raw_state)
                    or {"reason": "liner_state_refused", "detail": segment}
                )
                continue
            try:
                expected_oxide_mass = deposit_oxide_equivalent_mass_by_oxide(
                    cumulative_by_segment.get(segment, {})
                )
            except Exception as exc:  # noqa: BLE001 - diagnostic cannot gate lifecycle
                refusals.append(
                    {
                        "reason": str(
                            getattr(exc, "reason", "liner_cumulative_deposit_invalid")
                        ),
                        "detail": str(exc),
                    }
                )
                continue
            recorded_oxide_mass = raw_state.get(
                "cumulative_deposit_oxide_equivalent_kg_by_oxide"
            )
            if not isinstance(recorded_oxide_mass, Mapping):
                refusals.append(
                    {
                        "reason": "liner_cumulative_oxide_deposit_missing",
                        "detail": f"{segment} has no cumulative oxide-equivalent vector",
                    }
                )
                continue
            if any(not isinstance(oxide, str) for oxide in recorded_oxide_mass):
                refusals.append(
                    {
                        "reason": "liner_cumulative_oxide_deposit_invalid",
                        "detail": f"{segment} oxide vector keys must be strings",
                    }
                )
                continue
            all_oxides = set(expected_oxide_mass) | set(recorded_oxide_mass)
            mismatch = False
            for oxide in all_oxides:
                raw_value = recorded_oxide_mass.get(oxide, 0.0)
                if not is_declared_real_scalar(raw_value):
                    mismatch = True
                    break
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    mismatch = True
                    break
                if not math.isfinite(value) or not math.isclose(
                    value,
                    expected_oxide_mass.get(oxide, 0.0),
                    rel_tol=1.0e-9,
                    abs_tol=1.0e-12,
                ):
                    mismatch = True
                    break
            if mismatch:
                refusals.append(
                    {
                        "reason": "liner_cumulative_oxide_deposit_mismatch",
                        "detail": f"{segment} vector does not match cumulative wall deposit",
                    }
                )
                continue
            onset = raw_state.get("onset")
            if not isinstance(onset, bool):
                refusals.append(
                    {
                        "reason": "liner_accumulated_onset_missing",
                        "detail": f"{segment} has no d-419 onset evaluation",
                    }
                )
                continue
            evaluated_segments.append(segment)
            if onset:
                fired_segments.append(segment)
            else:
                no_onset_segments.append(segment)
        if fired_segments:
            segment = sorted(fired_segments)[0]
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=True,
                segment=segment,
                campaign_index=campaign_index,
            )
        if refusals and (evaluated_segments or len(refusals) > 1):
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
                refusal={
                    "reason": "liner_segment_coverage_incomplete",
                    "detail": "one or more liner segments could not be evaluated",
                    "no_onset_segments": sorted(no_onset_segments),
                    "refusals": refusals,
                },
            )
        if not evaluated_segments:
            refusal = refusals[0] if refusals else {
                "reason": "liner_accumulated_onset_missing",
                "detail": "no segment has a verified cumulative d-419 onset result",
            }
            return LimiterEvaluation(
                name=INTERFACE_FIRST_MELT_LIMITER,
                fired=False,
                campaign_index=campaign_index,
                refusal=refusal,
            )
        return LimiterEvaluation(
            name=INTERFACE_FIRST_MELT_LIMITER,
            fired=False,
            campaign_index=campaign_index,
        )

    return interface_first_melt_limiter(evaluate)


def interface_first_melt_from_liner_state(
    liner_state: Mapping[str, Any],
) -> Limiter:
    """Preserve the single-state adapter while delegating to sequence logic."""

    return _interface_first_melt_from_liner_states((liner_state,))


def _interface_first_melt_diagnostic(
    trajectory: Sequence[FoulingTerminalSnapshot],
    limiter: Limiter | None,
) -> dict[str, Any] | None:
    if limiter is None:
        return None
    last: LimiterEvaluation | None = None
    for campaign_index, snapshot in enumerate(trajectory, start=1):
        last = limiter(snapshot, campaign_index)
        if last.fired or last.refusal is not None:
            break
    if last is None:
        return {
            "name": INTERFACE_FIRST_MELT_LIMITER,
            "fired": False,
            "campaign_index": None,
            "diagnostic_only": True,
        }
    return {
        "name": INTERFACE_FIRST_MELT_LIMITER,
        "fired": last.fired,
        "segment": last.segment,
        "value": last.value,
        "limit": last.limit,
        "campaign_index": last.campaign_index,
        "diagnostic_only": True,
        "refusal": (
            dict(last.refusal) if last.refusal is not None else None
        ),
    }


def thickness_proxy_vs_placeholder(
    *,
    thickness_limit_m: float | None,
    rho_deposit_kg_m3: float | Mapping[str, float] | None,
    segment_area_m2: Mapping[str, float],
) -> Limiter:
    def evaluate(
        snapshot: FoulingTerminalSnapshot,
        campaign_index: int,
    ) -> LimiterEvaluation:
        if thickness_limit_m is None or rho_deposit_kg_m3 is None:
            return LimiterEvaluation(name=THICKNESS_PROXY_LIMITER, fired=False)
        thickness_by_segment = thickness_proxy_by_segment_m(
            snapshot,
            segment_area_m2=segment_area_m2,
            rho_deposit_kg_m3=rho_deposit_kg_m3,
        )
        if not thickness_by_segment:
            return LimiterEvaluation(
                name=THICKNESS_PROXY_LIMITER,
                fired=False,
                limit=float(thickness_limit_m),
                campaign_index=campaign_index,
            )
        segment, value = max(thickness_by_segment.items(), key=lambda item: item[1])
        fired = value >= float(thickness_limit_m)
        return LimiterEvaluation(
            name=THICKNESS_PROXY_LIMITER,
            fired=fired,
            segment=segment,
            value=value,
            limit=float(thickness_limit_m),
            campaign_index=campaign_index,
        )

    return evaluate


def project_lifecycle(
    trajectory: Sequence[FoulingTerminalSnapshot],
    *,
    segment_area_m2: Mapping[str, float],
    rho_deposit_kg_m3: float | Mapping[str, float] | None,
    thickness_limit_m: float | None,
    resinter_threshold_kg: float | None = None,
    interface_first_melt: Limiter | None = None,
) -> LifecycleProjection:
    interface_limiter = (
        interface_first_melt_limiter(interface_first_melt)
        if interface_first_melt is not None
        else None
    )
    if thickness_limit_m is not None:
        thickness_limit_m = _positive_finite(
            thickness_limit_m,
            "thickness_limit_m",
        )
    if resinter_threshold_kg is not None:
        resinter_threshold_kg = _positive_finite(
            resinter_threshold_kg,
            "resinter_threshold_kg",
        )
    threshold_params = {
        "resinter_threshold_kg": resinter_threshold_kg,
        "thickness_limit_m": thickness_limit_m,
        "rho_deposit_kg_m3": rho_deposit_kg_m3,
        "segment_area_m2": dict(segment_area_m2),
    }
    if all(
        value is None
        for value in (
            threshold_params["resinter_threshold_kg"],
            threshold_params["thickness_limit_m"],
            threshold_params["rho_deposit_kg_m3"],
        )
    ):
        interface_diagnostic = _interface_first_melt_diagnostic(
            trajectory, interface_limiter
        )
        return LifecycleProjection(
            service_life_campaigns=None,
            worst_segment_campaigns_provisional=None,
            cascade_knee_provisional=None,
            grounding_status=GROUNDING_UNGROUNDED,
            service_life_authoritative=False,
            limiter_fired=None,
            end_condition_stack=(
                (INTERFACE_FIRST_MELT_LIMITER, THICKNESS_PROXY_LIMITER)
                if interface_limiter is not None
                else (THICKNESS_PROXY_LIMITER,)
            ),
            interface_first_melt_diagnostic=interface_diagnostic,
        )
    if thickness_limit_m is None or rho_deposit_kg_m3 is None:
        interface_diagnostic = _interface_first_melt_diagnostic(
            trajectory, interface_limiter
        )
        return LifecycleProjection(
            service_life_campaigns=None,
            worst_segment_campaigns_provisional=None,
            cascade_knee_provisional=None,
            grounding_status=GROUNDING_UNGROUNDED,
            service_life_authoritative=False,
            limiter_fired=None,
            end_condition_stack=(
                (INTERFACE_FIRST_MELT_LIMITER, THICKNESS_PROXY_LIMITER)
                if interface_limiter is not None
                else (THICKNESS_PROXY_LIMITER,)
            ),
            interface_first_melt_diagnostic=interface_diagnostic,
        )

    stack = phase_a_end_condition_stack(
        thickness_limit_m=thickness_limit_m,
        rho_deposit_kg_m3=rho_deposit_kg_m3,
        segment_area_m2=segment_area_m2,
        interface_first_melt=interface_limiter,
    )
    fired: LimiterEvaluation | None = None
    interface_evaluation: LimiterEvaluation | None = None
    for campaign_index, snapshot in enumerate(trajectory, start=1):
        for limiter in stack:
            if fired is not None and limiter is not interface_limiter:
                continue
            if (
                limiter is interface_limiter
                and interface_evaluation is not None
                and (
                    interface_evaluation.fired
                    or interface_evaluation.refusal is not None
                )
            ):
                continue
            evaluation = limiter(snapshot, campaign_index)
            if evaluation.name == INTERFACE_FIRST_MELT_LIMITER:
                if evaluation.refusal is not None:
                    interface_evaluation = evaluation
                elif evaluation.fired and (
                    interface_evaluation is None or not interface_evaluation.fired
                ):
                    interface_evaluation = evaluation
                elif interface_evaluation is None:
                    interface_evaluation = evaluation
                continue
            if evaluation.fired and fired is None:
                fired = evaluation
        if fired is not None and (
            interface_limiter is None
            or (
                interface_evaluation is not None
                and (interface_evaluation.fired or interface_evaluation.refusal is not None)
            )
        ):
            break

    worst_campaigns = _worst_segment_campaigns_from_trajectory(
        trajectory,
        segment_area_m2=segment_area_m2,
        rho_deposit_kg_m3=rho_deposit_kg_m3,
        thickness_limit_m=float(thickness_limit_m),
    )
    service_life = float(fired.campaign_index) if fired else worst_campaigns
    return LifecycleProjection(
        service_life_campaigns=service_life,
        worst_segment_campaigns_provisional=worst_campaigns,
        cascade_knee_provisional=cascade_knee_provisional(
            trajectory,
            segment_area_m2=segment_area_m2,
            rho_deposit_kg_m3=rho_deposit_kg_m3,
        ),
        grounding_status=GROUNDING_PROVISIONAL,
        service_life_authoritative=False,
        limiter_fired=THICKNESS_PROXY_LIMITER if service_life is not None else None,
        end_condition_stack=(
            (INTERFACE_FIRST_MELT_LIMITER, THICKNESS_PROXY_LIMITER)
            if interface_limiter is not None
            else (THICKNESS_PROXY_LIMITER,)
        ),
        interface_first_melt_diagnostic=(
            {
                "name": INTERFACE_FIRST_MELT_LIMITER,
                "fired": interface_evaluation.fired,
                "segment": interface_evaluation.segment,
                "value": interface_evaluation.value,
                "limit": interface_evaluation.limit,
                "campaign_index": interface_evaluation.campaign_index,
                "diagnostic_only": True,
                "refusal": (
                    dict(interface_evaluation.refusal)
                    if interface_evaluation.refusal is not None
                    else None
                ),
            }
            if interface_evaluation is not None
            else None
        ),
    )


def thickness_proxy_by_segment_m(
    snapshot: FoulingTerminalSnapshot,
    *,
    segment_area_m2: Mapping[str, float],
    rho_deposit_kg_m3: float | Mapping[str, float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for segment, species_kg in snapshot.wall_deposit_by_segment_species_kg.items():
        area = _positive_finite(
            segment_area_m2.get(segment),
            f"segment_area_m2[{segment!r}]",
        )
        total = 0.0
        for species, kg in species_kg.items():
            rho = _rho_for_species(rho_deposit_kg_m3, species)
            total += float(kg) / (rho * area)
        if abs(total) > 0.0:
            result[segment] = total
    return result


def cascade_knee_provisional(
    trajectory: Sequence[FoulingTerminalSnapshot],
    *,
    segment_area_m2: Mapping[str, float],
    rho_deposit_kg_m3: float | Mapping[str, float] | None,
) -> int | None:
    if rho_deposit_kg_m3 is None or len(trajectory) < 3:
        return None
    maxima = [
        max(
            thickness_proxy_by_segment_m(
                snapshot,
                segment_area_m2=segment_area_m2,
                rho_deposit_kg_m3=rho_deposit_kg_m3,
            ).values(),
            default=0.0,
        )
        for snapshot in trajectory
    ]
    slopes = [maxima[index] - maxima[index - 1] for index in range(1, len(maxima))]
    for index in range(1, len(slopes)):
        if slopes[index] > slopes[index - 1] + 1.0e-15:
            return index + 2
    return None


def campaigns_to_resinter_total(
    wall_deposit_by_segment_species_kg: NestedDeposit,
    *,
    resinter_threshold_kg: float | None,
    authoritative_for_resinter: bool,
    authority: Mapping[str, Any] | None = None,
    measured: bool = True,
) -> CampaignsToResinterTotal:
    if resinter_threshold_kg is not None:
        resinter_threshold_kg = _positive_finite(
            resinter_threshold_kg,
            "resinter_threshold_kg",
        )
    total_wall_load_kg = sum(
        float(kg)
        for species_kg in wall_deposit_by_segment_species_kg.values()
        for kg in species_kg.values()
        if float(kg) > EPSILON_KG
    )
    availability = wall_deposit_figure_availability(
        has_positive_deposit=total_wall_load_kg > 0.0,
        authority=authority,
        measured=measured,
    )
    if availability.status == "refused":
        value: float | str | None = None
    elif total_wall_load_kg <= 0.0:
        value: float | str = "infinite"
    elif resinter_threshold_kg is None:
        value = f"resinter_threshold_kg / {total_wall_load_kg:.12g}"
    else:
        value = float(resinter_threshold_kg) / total_wall_load_kg
    return CampaignsToResinterTotal(
        value=value,
        authoritative_for_resinter=(
            bool(authoritative_for_resinter)
            and availability.status != "refused"
        ),
        refusal=availability.refusal,
        measured_empty_deposit=availability.status == "measured_empty",
    )


def _worst_segment_campaigns_from_trajectory(
    trajectory: Sequence[FoulingTerminalSnapshot],
    *,
    segment_area_m2: Mapping[str, float],
    rho_deposit_kg_m3: float | Mapping[str, float],
    thickness_limit_m: float,
) -> float | None:
    if not trajectory:
        return None
    last_index = float(len(trajectory))
    by_segment = thickness_proxy_by_segment_m(
        trajectory[-1],
        segment_area_m2=segment_area_m2,
        rho_deposit_kg_m3=rho_deposit_kg_m3,
    )
    candidates = []
    for thickness_m in by_segment.values():
        if thickness_m > 0.0:
            candidates.append(thickness_limit_m / (thickness_m / last_index))
    return min(candidates) if candidates else None


def _coerce_nested_deposit(raw: Any) -> dict[str, dict[str, float]]:
    if not isinstance(raw, Mapping):
        raise FoulingProjectionError("wall deposit export must be a mapping")
    nested: dict[str, dict[str, float]] = {}
    for key, value in raw.items():
        if isinstance(key, tuple) and len(key) == 2:
            segment, species = str(key[0]), str(key[1])
            _assign_deposit(nested, segment, species, value)
            continue
        if isinstance(value, Mapping):
            segment = str(key)
            for species, kg in value.items():
                _assign_deposit(nested, segment, str(species), kg)
            continue
        raise FoulingProjectionError(
            "wall deposit export must use (segment, species) keys or nested mapping"
        )
    return {
        segment: dict(sorted(species_kg.items()))
        for segment, species_kg in sorted(nested.items())
    }


def _assign_deposit(
    nested: dict[str, dict[str, float]],
    segment: str,
    species: str,
    raw_kg: Any,
) -> None:
    kg = _finite_float(raw_kg, f"wall_deposit[{segment!r}][{species!r}]")
    species_kg = nested.setdefault(segment, {})
    if abs(kg) <= EPSILON_KG:
        species_kg.setdefault(species, 0.0)
        return
    species_kg[species] = species_kg.get(species, 0.0) + kg


def _add_deposits(left: NestedDeposit, right: NestedDeposit) -> dict[str, dict[str, float]]:
    result = _plain_nested_deposit(left)
    for segment, species_kg in right.items():
        for species, kg in species_kg.items():
            merged = result.setdefault(segment, {}).get(species, 0.0) + float(kg)
            if merged < -EPSILON_KG:
                raise FoulingProjectionError(
                    f"negative projected wall inventory for {segment}/{species}"
                )
            if abs(merged) <= EPSILON_KG:
                result.setdefault(segment, {})[species] = 0.0
            else:
                result.setdefault(segment, {})[species] = merged
        if not result.get(segment):
            result.pop(segment, None)
    return {
        segment: dict(sorted(species_kg.items()))
        for segment, species_kg in sorted(result.items())
    }


def _subtract_deposits(
    left: NestedDeposit,
    right: NestedDeposit,
) -> dict[str, dict[str, float]]:
    result = _plain_nested_deposit(left)
    for segment, species_kg in right.items():
        for species, kg in species_kg.items():
            net = result.setdefault(segment, {}).get(species, 0.0) - float(kg)
            if abs(net) <= EPSILON_KG:
                result.setdefault(segment, {})[species] = 0.0
            else:
                result.setdefault(segment, {})[species] = net
        if not result.get(segment):
            result.pop(segment, None)
    return {
        segment: dict(sorted(species_kg.items()))
        for segment, species_kg in sorted(result.items())
    }


def _freeze_nested_deposit(raw: Any) -> NestedDeposit:
    nested = _coerce_nested_deposit(raw)
    return MappingProxyType({
        segment: MappingProxyType(dict(species_kg))
        for segment, species_kg in nested.items()
    })


def _plain_nested_deposit(raw: NestedDeposit) -> dict[str, dict[str, float]]:
    return {
        str(segment): {
            str(species): (
                0.0 if abs(float(kg)) <= EPSILON_KG else float(kg)
            )
            for species, kg in sorted(species_kg.items())
        }
        for segment, species_kg in sorted(raw.items())
        if species_kg
    }


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            copy.deepcopy(key): _freeze_value(item)
            for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return copy.deepcopy(value)


def _finite_float(value: Any, field: str) -> float:
    try:
        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise TypeError
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise FoulingProjectionError(f"{field} must be numeric") from exc
    if not math.isfinite(amount):
        raise FoulingProjectionError(f"{field} must be finite")
    return amount


def _positive_finite(value: Any, field: str) -> float:
    amount = _finite_float(value, field)
    if amount <= 0.0:
        raise FoulingProjectionError(f"{field} must be > 0")
    return amount


def _rho_for_species(
    rho_deposit_kg_m3: float | Mapping[str, float],
    species: str,
) -> float:
    if isinstance(rho_deposit_kg_m3, Mapping):
        return _positive_finite(
            rho_deposit_kg_m3.get(species),
            f"rho_deposit_kg_m3[{species!r}]",
        )
    return _positive_finite(rho_deposit_kg_m3, "rho_deposit_kg_m3")
