"""Simulator trust labels layered on top of the openimcc package result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, init=False)
class ImccAdapterLabels:
    """Package labels with the simulator's positional trust contract."""

    identity: Mapping[str, str]
    coverage: Mapping[str, str]
    trust: str
    envelope_status: str
    flags: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()
    acid_sink_ratio: float | None = None

    def __init__(
        self,
        identity: Mapping[str, str],
        coverage: Mapping[str, str],
        trust: str,
        envelope_status: str,
        flags: tuple[str, ...] = (),
        notices: tuple[str, ...] = (),
        acid_sink_ratio: float | None = None,
    ) -> None:
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "trust", trust)
        object.__setattr__(self, "envelope_status", envelope_status)
        object.__setattr__(self, "flags", flags)
        object.__setattr__(self, "notices", notices)
        object.__setattr__(self, "acid_sink_ratio", acid_sink_ratio)
