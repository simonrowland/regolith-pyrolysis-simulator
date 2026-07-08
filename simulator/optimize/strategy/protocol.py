"""Optimizer strategy protocol.

Strategies propose recipe candidates and record scored results. They do not run
evaluations, manage pools, or choose parallelism; the study loop owns execution.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from simulator.optimize.recipe import RecipePatch

if TYPE_CHECKING:
    from simulator.optimize.evaluate import ScoredResult

PROPOSAL_SOURCES = frozenset(
    {
        "sobol",
        "morris",
        "staged_child",
        "backward_resample",
        "joint_refine",
        "seed_recipe",
        "store_warm_start",
        "optuna_enqueued",
        "optuna_model",
    }
)


@dataclass(frozen=True)
class WarmStartSeed:
    """Prior or profile recipe proposed as a fresh optimizer candidate."""

    id: str
    patch: RecipePatch = field(compare=False)
    proposal_source: str
    origin: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)
    topology_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("seed id must be a non-empty string")
        if not isinstance(self.patch, RecipePatch):
            raise TypeError("seed patch must be a RecipePatch")
        if self.proposal_source not in PROPOSAL_SOURCES:
            raise ValueError(f"unknown proposal_source {self.proposal_source!r}")
        if not isinstance(self.origin, MappingABC):
            raise TypeError("seed origin must be a mapping")
        if self.topology_id is not None and not isinstance(self.topology_id, str):
            raise TypeError("seed topology_id must be a string or None")
        object.__setattr__(self, "origin", _deep_freeze(self.origin))

    def __reduce__(
        self,
    ) -> tuple[Any, tuple[str, RecipePatch, str, dict[str, Any], str | None]]:
        return (
            type(self),
            (
                self.id,
                self.patch,
                self.proposal_source,
                _deep_thaw(self.origin),
                self.topology_id,
            ),
        )


@dataclass(frozen=True)
class Candidate:
    """One strategy-proposed recipe patch.

    ``id`` is deterministic within a strategy run and is the value evaluation
    must pass through as ``ScoredResult.candidate_id``.
    """

    id: str
    patch: RecipePatch = field(compare=False)
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, MappingABC):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", _deep_freeze(self.metadata))

    def __reduce__(self) -> tuple[Any, tuple[str, RecipePatch, dict[str, Any]]]:
        return (type(self), (self.id, self.patch, _deep_thaw(self.metadata)))

    def __hash__(self) -> int:
        return hash(self.id)


@runtime_checkable
class Strategy(Protocol):
    """Ask/tell optimizer strategy boundary.

    ``ask(n)`` returns ``n`` unevaluated candidates. ``tell(results)`` receives
    pairs of the original ``Candidate`` and its ``ScoredResult``; each result's
    ``candidate_id`` must match ``Candidate.id``. Strategies only propose and
    learn from results. They never evaluate candidates or manage parallelism.
    """

    @property
    def name(self) -> str:
        """Stable strategy name."""

    @property
    def seed(self) -> int:
        """Deterministic strategy seed."""

    def ask(self, n: int) -> list[Candidate]:
        """Return ``n`` candidate recipe patches."""

    def tell(self, results: Sequence[tuple[Candidate, "ScoredResult"]]) -> None:
        """Record scored results for previously asked candidates."""


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        frozen_items = (_deep_freeze(item) for item in value)
        return tuple(sorted(frozen_items, key=repr))
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value
