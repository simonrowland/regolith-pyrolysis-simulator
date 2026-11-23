"""Resolve the role of the composition available for an engine call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Generic, TypeVar

from simulator.battery.enums import NoticeKind, Quantity
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

    @property
    def callable(self) -> bool:
        return self.composition.is_value

    @property
    def initial_charge_only(self) -> bool:
        return self.role == INITIAL_CHARGE_ONLY_PROXY_FLAG


def is_initial_charge_composition(state: State[object] | None) -> bool:
    return (
        state is not None
        and state.is_value
        and isinstance(state.value, Composition)
        and state.value.proxy_flag == INITIAL_CHARGE_ONLY_PROXY_FLAG
    )


def is_role_default_reason(reason: object) -> bool:
    return isinstance(reason, str) and reason.startswith(_ROLE_DEFAULT_PREFIX)


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
    """Bind the first role declaration to the selected physical composition.

    An absent declaration leaves the composition's existing interpretation in
    place. An unrecognized declaration takes the stated initial-charge
    default when a usable composition exists. Typed rows carry that decision
    through the existing composition proxy and notices.
    """

    if isinstance(row, Observation):
        identity = row.identity
        quantity_state = getattr(identity, "quantity", None)
        token = quantity_state.value if isinstance(quantity_state, State) else None
        quantity = token if isinstance(token, Quantity) else None
        origin = row.observation_id
        notices = row.notices
        if composition is None:
            composition = getattr(identity, "composition", None)
        point = (row.point_conditions or {}).get("composition")
        if isinstance(point, Located):
            point_composition = point.state
    # Choose the assay first. A discarded identity proxy must not decide the
    # role of the composition that is actually used.
    if point_composition is not None and (
        isinstance(point_composition.value, Composition) or composition is None
    ):
        composition = point_composition
    if composition is None:
        composition = State.unknown("missing_condition:melt_composition")
    has_proxy = is_initial_charge_composition(composition)
    role = None
    original_reason = None
    for context in (row, parent):
        if not isinstance(context, Mapping) or COMPOSITION_ROLE_FIELD not in context:
            continue
        raw = context[COMPOSITION_ROLE_FIELD]
        token = raw.get("value") if isinstance(raw, Mapping) else raw
        token = str(token).strip() if token is not None else ""
        if token in _ROLES:
            role = token
        else:
            original_reason = (
                f"composition_role {token!r} is not one of "
                f"{sorted(_ROLES)}; composition left unstamped"
            )
        break

    role_notices = tuple(
        notice
        for notice in notices
        if (has_proxy and is_initial_charge_only_proxy_notice(notice))
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
        if (
            composition.is_value
            and isinstance(composition.value, Composition)
            and composition.value.proxy_flag != role
        ):
            composition = State.of(replace(composition.value, proxy_flag=role))
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
        role is None and has_proxy and original_reason is None
    ):
        role = INITIAL_CHARGE_ONLY_PROXY_FLAG
        status = RoleStatus.DECLARED
        reason = f"typed-refusal:{role}: {INITIAL_CHARGE_ONLY_PROXY_NOTICE}"
        flags = (role,) if composition.is_value else ()
    else:
        status = RoleStatus.DECLARED if role is not None else RoleStatus.UNKNOWN
        reason = original_reason or composition.reason
        flags = ()

    if (
        role == INITIAL_CHARGE_ONLY_PROXY_FLAG
        and composition.is_value
        and quantity is not None
        and has_proxy
        and not any(is_initial_charge_only_proxy_notice(n) for n in role_notices)
    ):
        role_notices += (initial_charge_only_proxy_notice(quantity, origin),)
    return RoleResolution(composition, status, role, flags, reason, original_reason, role_notices)
