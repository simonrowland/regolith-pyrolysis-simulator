"""Pure fouling-lifecycle projection over terminal wall-deposit exports.

Phase A is an overlay. It reads completed run exports and never writes a
ledger, seeds a simulator, or changes per-run output.

Merge semantics are explicit because Phase A and the deferred seeded simulator
path differ. In Phase A, each constituent run starts with empty authoritative
wall state, so the trace terminal export is already that campaign's net deposit
(deposit minus any remobilized material already accounted by the run). The
projection state is updated as:

    cumulative_projection(N) = cumulative_projection(N - 1) + per_run_net(N)

If a future seeded path exports terminal wall state that already includes the
carried wall inventory, callers must set ``export_includes_carried=True``; then
``per_run_net(N)`` is ``terminal_export(N) - carried_projection(N - 1)`` and
the post-merge cumulative snapshot is still the projection state, not the raw
trace object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


GROUNDING_UNGROUNDED = "ungrounded_threshold"
GROUNDING_PROVISIONAL = "PROVISIONAL"
THICKNESS_PROXY_LIMITER = "thickness_proxy_vs_placeholder"
EPSILON_KG = 1.0e-12


class FoulingProjectionError(ValueError):
    """Raised when a projection input is malformed or physically impossible."""


NestedDeposit = Mapping[str, Mapping[str, float]]
Limiter = Callable[["FoulingTerminalSnapshot", int], "LimiterEvaluation"]


@dataclass(frozen=True)
class FoulingTerminalSnapshot:
    """Immutable terminal wall-deposit export for one completed run."""

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
        """Harvest the existing read-only trace deposit and sticking authority."""

        return cls(
            wall_deposit_by_segment_species_kg=_coerce_nested_deposit(
                getattr(trace, "wall_deposit_by_segment_species_kg", {})
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


@dataclass(frozen=True)
class LifecycleProjection:
    service_life_campaigns: float | None
    worst_segment_campaigns_provisional: float | None
    cascade_knee_provisional: int | None
    grounding_status: str
    service_life_authoritative: bool
    limiter_fired: str | None
    end_condition_stack: tuple[str, ...] = (THICKNESS_PROXY_LIMITER,)

    def to_dict(self) -> dict[str, Any]:
        return {
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


@dataclass(frozen=True)
class CampaignsToResinterTotal:
    value: float | str
    authoritative_for_resinter: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "authoritative_for_resinter": self.authoritative_for_resinter,
        }


def merge_run_snapshot(
    carried_projection: FoulingTerminalSnapshot | None,
    run_export: FoulingTerminalSnapshot,
    *,
    export_includes_carried: bool = False,
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
    return (
        FoulingTerminalSnapshot(
            wall_deposit_by_segment_species_kg=cumulative,
            wall_deposit_sticking_authority=run_export.wall_deposit_sticking_authority,
            grounding_status=run_export.grounding_status,
            threshold_params=run_export.threshold_params,
            c4b_binding_substrate_state=run_export.c4b_binding_substrate_state,
        ),
        _freeze_nested_deposit(per_run_net),
    )


def merge_snapshot_sequence(
    run_exports: Sequence[FoulingTerminalSnapshot],
    *,
    export_includes_carried: bool = False,
) -> FoulingMergeResult:
    carried: FoulingTerminalSnapshot | None = None
    trajectory: list[FoulingTerminalSnapshot] = []
    per_run_net: list[NestedDeposit] = []
    for run_export in run_exports:
        carried, net = merge_run_snapshot(
            carried,
            run_export,
            export_includes_carried=export_includes_carried,
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
) -> tuple[Limiter, ...]:
    return (
        thickness_proxy_vs_placeholder(
            thickness_limit_m=thickness_limit_m,
            rho_deposit_kg_m3=rho_deposit_kg_m3,
            segment_area_m2=segment_area_m2,
        ),
    )


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
) -> LifecycleProjection:
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
        return LifecycleProjection(
            service_life_campaigns=None,
            worst_segment_campaigns_provisional=None,
            cascade_knee_provisional=None,
            grounding_status=GROUNDING_UNGROUNDED,
            service_life_authoritative=False,
            limiter_fired=None,
        )
    if thickness_limit_m is None or rho_deposit_kg_m3 is None:
        return LifecycleProjection(
            service_life_campaigns=None,
            worst_segment_campaigns_provisional=None,
            cascade_knee_provisional=None,
            grounding_status=GROUNDING_UNGROUNDED,
            service_life_authoritative=False,
            limiter_fired=None,
        )

    stack = phase_a_end_condition_stack(
        thickness_limit_m=thickness_limit_m,
        rho_deposit_kg_m3=rho_deposit_kg_m3,
        segment_area_m2=segment_area_m2,
    )
    fired: LimiterEvaluation | None = None
    for campaign_index, snapshot in enumerate(trajectory, start=1):
        for limiter in stack:
            evaluation = limiter(snapshot, campaign_index)
            if evaluation.fired:
                fired = evaluation
                break
        if fired is not None:
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
) -> CampaignsToResinterTotal:
    total_wall_load_kg = sum(
        float(kg)
        for species_kg in wall_deposit_by_segment_species_kg.values()
        for kg in species_kg.values()
        if float(kg) > EPSILON_KG
    )
    if total_wall_load_kg <= 0.0:
        value: float | str = "infinite"
    elif resinter_threshold_kg is None:
        value = f"resinter_threshold_kg / {total_wall_load_kg:.12g}"
    else:
        value = float(resinter_threshold_kg) / total_wall_load_kg
    return CampaignsToResinterTotal(
        value=value,
        authoritative_for_resinter=bool(authoritative_for_resinter),
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
    if abs(kg) <= EPSILON_KG:
        return
    species_kg = nested.setdefault(segment, {})
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
                result.get(segment, {}).pop(species, None)
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
                result.get(segment, {}).pop(species, None)
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
            str(species): float(kg)
            for species, kg in sorted(species_kg.items())
            if abs(float(kg)) > EPSILON_KG
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
