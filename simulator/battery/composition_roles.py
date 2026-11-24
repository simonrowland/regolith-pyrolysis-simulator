"""Resolve the role of the composition available for an engine call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Generic, TypeVar

from simulator.battery.enums import NoticeKind, Quantity
from simulator.battery.input_provenance import (
    PRINTED_STARTING_BULK,
    composition_with_provenance,
)
from simulator.battery.records import (
    INITIAL_CHARGE_ONLY_PROXY_FLAG,
    INITIAL_CHARGE_ONLY_PROXY_NOTICE,
    Composition,
    Located,
    Notice,
    Observation,
    initial_charge_only_proxy_notice,
    is_initial_charge_only_proxy_notice,
)
from simulator.state_types import State

COMPOSITION_ROLE_FIELD = "composition_role"
DATUM_STATE = "datum_state"
ROLE_DEFAULT_FLAG = "composition_role_defaulted_to_initial_charge_only"
_ROLE_DEFAULT_PREFIX = f"typed-refusal:{ROLE_DEFAULT_FLAG}: "
_ROLES = frozenset({INITIAL_CHARGE_ONLY_PROXY_FLAG, DATUM_STATE})
_Composition = TypeVar("_Composition", Composition, dict[str, float])


class RoleStatus(StrEnum):
    DECLARED = "declared"
    DEFAULTED = "defaulted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RoleResolution(Generic[_Composition]):
    composition: State[_Composition]
    status: RoleStatus
    role: str | None
    flags: tuple[str, ...] = ()
    reason: str | None = None
    original_reason: str | None = None
    notices: tuple[Notice, ...] = ()
    source_label: str | None = None
    locator: object | None = None

    @property
    def callable(self) -> bool:
        return self.composition.is_value

    @property
    def initial_charge_only(self) -> bool:
        return self.role == INITIAL_CHARGE_ONLY_PROXY_FLAG


@dataclass(frozen=True)
class AssayCandidate:
    """One decoded assay. The caller decodes; the resolver chooses."""

    state: State[Any]
    source: str
    label: str | None = None
    locator: object | None = None
    terminal: bool = False


def is_initial_charge_composition(state: State[object] | None) -> bool:
    return (
        state is not None
        and state.is_value
        and isinstance(state.value, Composition)
        and state.value.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG
    )


def is_role_default_reason(reason: object) -> bool:
    return isinstance(reason, str) and reason.startswith(_ROLE_DEFAULT_PREFIX)


def read_composition_role_field(*contexts: object) -> tuple[str | None, str | None]:
    """Printed token and unrecognized-token reason.

    The first context that has the field wins, including an unrecognized
    token. This does not default, stamp, or choose an assay.
    """

    for context in contexts:
        if not isinstance(context, Mapping) or COMPOSITION_ROLE_FIELD not in context:
            continue
        raw = context[COMPOSITION_ROLE_FIELD]
        token = raw.get("value") if isinstance(raw, Mapping) else raw
        token = str(token).strip() if token is not None else ""
        if token in _ROLES:
            return token, None
        return None, (
            f"composition_role {token!r} is not one of "
            f"{sorted(_ROLES)}; composition left unstamped"
        )
    return None, None


def _assay_usable(candidate: AssayCandidate) -> bool:
    state = candidate.state
    if state is None or not state.is_value:
        return False
    value = state.value
    if isinstance(value, Composition):
        return True
    return isinstance(value, Mapping) and len(value) > 0


def _choose_assay(candidates: Sequence[AssayCandidate]) -> AssayCandidate:
    if not candidates:
        return AssayCandidate(
            State.unknown("missing_condition:melt_composition"),
            source="none",
        )
    for index, candidate in enumerate(candidates):
        if candidate.terminal:
            return candidate
        if not _assay_usable(candidate):
            continue
        # A point recipe that is not a Composition must not hide a usable identity.
        if candidate.source == "point" and not isinstance(
            candidate.state.value, Composition
        ):
            later_identity = any(
                other.source == "identity" and _assay_usable(other)
                for other in candidates[index + 1 :]
            )
            if later_identity:
                continue
        return candidate
    return candidates[0]


def _stamp_decided_initial_charge(state: State[Any]) -> State[Any]:
    """Write the charge flag and starting-bulk provenance onto a decided assay."""

    if not state.is_value or not isinstance(state.value, Composition):
        return state
    value = state.value
    if value.proxy_flag != INITIAL_CHARGE_ONLY_PROXY_FLAG:
        value = replace(value, proxy_flag=INITIAL_CHARGE_ONLY_PROXY_FLAG)
    if value.input_provenance != PRINTED_STARTING_BULK:
        value = composition_with_provenance(value, PRINTED_STARTING_BULK)
    if value is state.value:
        return state
    return State.of(value)


def resolve_point_composition(
    row: object,
    parent: object = None,
    candidates: Sequence[AssayCandidate] = (),
    *,
    quantity: Quantity | None = None,
    origin: str = "",
    notices: Sequence[Notice] = (),
) -> RoleResolution[Any]:
    """Choose one assay and bind the printed role to it.

    Nothing in this function reads a flag it just wrote. A ``proxy_flag`` that
    arrived on the chosen candidate is a previous decision stored on the assay.
    """

    if isinstance(row, Observation):
        identity = row.identity
        quantity_state = getattr(identity, "quantity", None)
        token = quantity_state.value if isinstance(quantity_state, State) else None
        quantity = token if isinstance(token, Quantity) else None
        origin = row.observation_id
        notices = row.notices
        if not candidates:
            packed: list[AssayCandidate] = []
            point = (row.point_conditions or {}).get("composition")
            if isinstance(point, Located):
                packed.append(
                    AssayCandidate(point.state, source="point", locator=point.locator)
                )
            identity_composition = getattr(identity, "composition", None)
            if identity_composition is not None:
                packed.append(AssayCandidate(identity_composition, source="identity"))
            candidates = tuple(packed)

    chosen = _choose_assay(candidates)
    composition = chosen.state
    arrived_proxy = is_initial_charge_composition(composition)
    role, original_reason = read_composition_role_field(row, parent)
    role_notices = tuple(
        notice
        for notice in notices
        if (arrived_proxy and is_initial_charge_only_proxy_notice(notice))
        or (
            notice.kind is NoticeKind.SOURCE_DISAGREEMENT
            and is_role_default_reason(notice.reason)
        )
    )
    default_notice = next(
        (notice for notice in role_notices if is_role_default_reason(notice.reason)),
        None,
    )
    defaulted = default_notice is not None or (
        original_reason is not None and composition.is_value
    )
    if defaulted:
        role = INITIAL_CHARGE_ONLY_PROXY_FLAG
        status = RoleStatus.DEFAULTED
        composition = _stamp_decided_initial_charge(composition)
        if default_notice is not None:
            reason = default_notice.reason
            original_reason = reason.partition("original_reason=")[2] or None
        else:
            reason = (
                f"{_ROLE_DEFAULT_PREFIX}default={role}; original_reason={original_reason}"
            )
            if quantity is not None:
                role_notices += (
                    Notice(
                        kind=NoticeKind.SOURCE_DISAGREEMENT,
                        affected_quantities=(quantity,),
                        reason=reason,
                        origin=origin,
                    ),
                )
        flags = (ROLE_DEFAULT_FLAG,)
    elif role == INITIAL_CHARGE_ONLY_PROXY_FLAG or (
        role is None and arrived_proxy and original_reason is None
    ):
        role = INITIAL_CHARGE_ONLY_PROXY_FLAG
        status = RoleStatus.DECLARED
        reason = f"typed-refusal:{role}: {INITIAL_CHARGE_ONLY_PROXY_NOTICE}"
        composition = _stamp_decided_initial_charge(composition)
        flags = (role,) if composition.is_value else ()
    else:
        status = RoleStatus.DECLARED if role is not None else RoleStatus.UNKNOWN
        reason = original_reason or composition.reason
        flags = ()

    if (
        role == INITIAL_CHARGE_ONLY_PROXY_FLAG
        and composition.is_value
        and quantity is not None
        and (
            arrived_proxy
            or (
                status is RoleStatus.DECLARED
                and isinstance(composition.value, Composition)
            )
        )
        and not any(is_initial_charge_only_proxy_notice(n) for n in role_notices)
    ):
        role_notices += (initial_charge_only_proxy_notice(quantity, origin),)
    return RoleResolution(
        composition,
        status,
        role,
        flags,
        reason,
        original_reason,
        role_notices,
        chosen.label,
        chosen.locator,
    )


def resolve_composition_role(
    row: object,
    parent: object = None,
    *,
    composition: State[_Composition] | None = None,
    point_composition: State[_Composition] | None = None,
    quantity: Quantity | None = None,
    origin: str = "",
    notices: Sequence[Notice] = (),
) -> RoleResolution[_Composition]:
    """Pack a point assay and an identity assay, then resolve that point once."""

    packed: list[AssayCandidate] = []
    if point_composition is not None:
        packed.append(AssayCandidate(point_composition, source="point"))
    if composition is not None:
        packed.append(AssayCandidate(composition, source="identity"))
    return resolve_point_composition(
        row,
        parent,
        tuple(packed),
        quantity=quantity,
        origin=origin,
        notices=notices,
    )
