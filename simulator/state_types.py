"""Dependency-free generic typed absence states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar


class StateTag(StrEnum):
    VALUE = "value"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


T = TypeVar("T")


@dataclass(frozen=True)
class State(Generic[T]):
    """Three-valued absence: value(T) | unknown(reason) | not_applicable(reason)."""

    tag: StateTag
    value: T | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError(f"State.{self.tag} reason must be a string")
        if self.tag is StateTag.VALUE:
            if self.value is None:
                raise ValueError("State.value requires a value")
        else:
            if self.value is not None:
                raise ValueError(f"State.{self.tag} cannot carry a value")
            if not self.reason or not self.reason.strip():
                raise ValueError(f"State.{self.tag} requires a reason")

    @classmethod
    def of(cls, value: T) -> "State[T]":
        return cls(StateTag.VALUE, value=value)

    @classmethod
    def unknown(cls, reason: str) -> "State[T]":
        return cls(StateTag.UNKNOWN, reason=reason)

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
