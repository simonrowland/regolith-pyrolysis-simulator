"""Dependency-free generic typed absence states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Generic, Mapping, TypeVar

if TYPE_CHECKING:
    from simulator.battery.enums import PolymorphRejectionKind


class StateTag(StrEnum):
    VALUE = "value"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


T = TypeVar("T")


@dataclass(frozen=True)
class GasControlVolume:
    """Read-only mol inventory view of an existing finite ledger volume."""

    volume_id: str
    account_id: str
    volume_m3: float
    temperature_K: float
    species_mol: Mapping[str, float]


@dataclass(frozen=True)
class GasStreamPlane:
    """Zero-storage observation section; absent pressure is not vacuum."""

    plane_id: str
    partial_pressure_Pa: State[Mapping[str, float]]
    volume_m3: float = 0.0


@dataclass(frozen=True)
class State(Generic[T]):
    """Three-valued absence: value(T) | unknown(reason) | not_applicable(reason)."""

    tag: StateTag
    value: T | None = None
    reason: str | None = None
    rejection_kind: PolymorphRejectionKind | None = None

    def __post_init__(self) -> None:
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError(f"State.{self.tag} reason must be a string")
        if self.rejection_kind is not None and not isinstance(
            self.rejection_kind, StrEnum
        ):
            raise ValueError("State.rejection_kind must be a string enum")
        if self.tag is StateTag.VALUE:
            if self.value is None:
                raise ValueError("State.value requires a value")
            if self.rejection_kind is not None:
                raise ValueError("State.value cannot carry a rejection kind")
        else:
            if self.value is not None:
                raise ValueError(f"State.{self.tag} cannot carry a value")
            if not self.reason or not self.reason.strip():
                raise ValueError(f"State.{self.tag} requires a reason")
            if self.rejection_kind is not None and self.tag is not StateTag.UNKNOWN:
                raise ValueError("Only State.unknown can carry a rejection kind")

    @classmethod
    def of(cls, value: T) -> "State[T]":
        return cls(StateTag.VALUE, value=value)

    @classmethod
    def unknown(
        cls, reason: str, *, rejection_kind: PolymorphRejectionKind | None = None
    ) -> "State[T]":
        return cls(StateTag.UNKNOWN, reason=reason, rejection_kind=rejection_kind)

    @classmethod
    def not_applicable(cls, reason: str) -> "State[T]":
        return cls(StateTag.NOT_APPLICABLE, reason=reason)

    @property
    def is_value(self) -> bool:
        return self.tag is StateTag.VALUE

    @property
    def is_unknown(self) -> bool:
        return self.tag is StateTag.UNKNOWN

    @property
    def is_not_applicable(self) -> bool:
        return self.tag is StateTag.NOT_APPLICABLE
