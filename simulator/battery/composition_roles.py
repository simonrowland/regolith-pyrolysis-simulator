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
    Derivation,
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
_INVALID_RUN_COMPOSITION_PREFIXES = (
    "malformed declared amount",
    "unsupported component",
    "mass-percent composition",
    "partial_composition:",
    "partial analysis contains unsupported component",
    "explicit printed point composition map is unusable",
    "printed point composition is unusable",
)
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
    inference: Derivation | None = None

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
    inference: Derivation | None = None
    # False when this assay is a flat run: the parent's role describes the
    # parent, not these columns. An explicit role on the row still applies.
    inherit_parent_role: bool = True
    declared_map: bool = False
    requires_declared_role: bool = False
    incomplete_initial_charge: bool = False


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


def invalid_run_composition(
    state: State[Any] | None, *, declared_map: bool = True
) -> bool:
    """Whether a stored composition is a printed but unusable run assay."""

    return _reason_is_invalid_run_composition(
        None if state is None or not state.is_unknown else state.reason,
        declared_map=declared_map,
    )


def _reason_is_invalid_run_composition(
    reason: str | None, *, declared_map: bool
) -> bool:
    folded = (reason or "").casefold()
    if not folded:
        return False
    if folded.startswith("malformed printed component amount"):
        return declared_map
    return any(
        folded.startswith(prefix) for prefix in _INVALID_RUN_COMPOSITION_PREFIXES
    )


def _choose_assay(
    candidates: Sequence[AssayCandidate],
    *,
    declared_role: str | None = None,
    role_reason: str | None = None,
) -> AssayCandidate:
    admitted: list[AssayCandidate] = []
    for candidate in candidates:
        if candidate.requires_declared_role and declared_role is None:
            if role_reason is not None:
                return replace(candidate, state=State.unknown(role_reason))
            continue
        admitted.append(candidate)
    candidates = tuple(admitted)
    if not candidates:
        return AssayCandidate(
            State.unknown("missing_condition:melt_composition"),
            source="none",
        )
    # Admission is one resolver rule. Migration supplies every candidate and
    # its decoded source; it does not stop the cascade when one candidate wins.
    if declared_role == INITIAL_CHARGE_ONLY_PROXY_FLAG and any(
        candidate.source == "point"
        and candidate.inherit_parent_role
        and candidate.incomplete_initial_charge
        for candidate in candidates
    ):
        prior_charge = next(
            (
                candidate
                for candidate in candidates
                if candidate.source == "identity"
                and is_initial_charge_composition(candidate.state)
            ),
            None,
        )
        if prior_charge is not None:
            return prior_charge
    for candidate in candidates:
        state = candidate.state
        if state.is_unknown and candidate.source in {
            "point",
            "declared",
            "identity",
        }:
            invalid = _reason_is_invalid_run_composition(
                state.reason,
                declared_map=(
                    candidate.declared_map or candidate.source == "declared"
                ),
            )
            if invalid:
                return candidate
    flat_run = next(
        (candidate for candidate in candidates if candidate.label == "flat-run"),
        None,
    )
    if flat_run is not None:
        return flat_run
    for candidate in candidates:
        if candidate.terminal:
            return candidate
    for index, candidate in enumerate(candidates):
        state = candidate.state
        if candidate.source == "parent" and state.is_unknown and not any(
            _assay_usable(previous) for previous in candidates[:index]
        ):
            return candidate
    for index, candidate in enumerate(candidates):
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


def choose_stored_run(candidates: Sequence[AssayCandidate]) -> AssayCandidate:
    """Assay a stored observation's engine call uses.

    An unknown point outranks a charge identity, so a refused run map is not
    scored as the printed charge. Otherwise a usable identity outranks a
    usable point. The stored state is returned unchanged.
    """

    point = next((candidate for candidate in candidates if candidate.source == "point"), None)
    identity = next(
        (candidate for candidate in candidates if candidate.source == "identity"),
        None,
    )

    def usable(candidate: AssayCandidate | None) -> bool:
        return (
            candidate is not None
            and candidate.state is not None
            and candidate.state.is_value
        )

    if (
        identity is not None
        and is_initial_charge_composition(identity.state)
        and point is not None
        and point.state is not None
        and point.state.is_unknown
    ):
        return point
    if usable(identity):
        return identity
    if usable(point):
        return point
    if identity is not None:
        return identity
    if point is not None:
        return point
    return AssayCandidate(
        State.unknown("missing_condition:melt_composition"),
        source="none",
    )


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
                    AssayCandidate(
                        point.state,
                        source="point",
                        locator=point.locator,
                        inference=point.inference,
                    )
                )
            identity_composition = getattr(identity, "composition", None)
            if identity_composition is not None:
                packed.append(AssayCandidate(identity_composition, source="identity"))
            candidates = tuple(packed)
            # No explicit candidates: the engine and the qualification read
            # the same stored assay. Explicit candidates keep point-first order.
            chosen = choose_stored_run(candidates)
        else:
            declared_role, role_reason = read_composition_role_field(row, parent)
            chosen = _choose_assay(
                candidates,
                declared_role=declared_role,
                role_reason=role_reason,
            )
    else:
        declared_role, role_reason = read_composition_role_field(row, parent)
        chosen = _choose_assay(
            candidates,
            declared_role=declared_role,
            role_reason=role_reason,
        )
    composition = chosen.state
    arrived_proxy = is_initial_charge_composition(composition)
    role_contexts = (row, parent) if chosen.inherit_parent_role else (row,)
    role, original_reason = read_composition_role_field(*role_contexts)
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
        chosen.inference,
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
