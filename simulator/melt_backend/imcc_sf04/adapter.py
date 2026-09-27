"""Simulator trust label on top of the openimcc dependency adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

import openimcc
from openimcc import (
    ImccComponentOutsideDomainError,
    ImccCompositionIncompleteError,
    ImccCompositionOutsideValidatedEnvelopeError,
    ImccDatapack,
    ImccFerricInputUnsupportedError,
    ImccLoadedDatapack,
    ImccMalformedDatapackError,
    ImccNonconvergenceError,
    ImccRefusal,
    ImccResult,
    ImccSPComponentRequiresExtensionError,
    ImccTOutsideDatapackDomainError,
    ImccUnprovenDatapackError,
    label_research_datapack,
    load_datapack,
)
from simulator.backend_names import canonical_backend_name


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
        trust: str = "internal-analytical",
        envelope_status: str = "inside",
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


def evaluate(*args: Any, **kwargs: Any) -> ImccResult:
    """Call openimcc and attach the simulator-only trust vocabulary."""
    allow_out_of_envelope = bool(kwargs.get("allow_out_of_envelope", False))
    package_kwargs = dict(kwargs)
    package_kwargs["allow_out_of_envelope"] = True
    result = openimcc.evaluate(*args, **package_kwargs)
    labels = result.labels
    if labels is None:
        return result
    parent_mol = dict(zip(result.parent_oxides, result.parent_mol, strict=True))
    canonical_oxide_mol = sum(
        float(parent_mol[name])
        for name in ("SiO2", "MgO", "FeO", "CaO", "Al2O3", "TiO2", "Na2O", "K2O")
    )
    x_me2o = (
        float(parent_mol["Na2O"]) + float(parent_mol["K2O"])
    ) / canonical_oxide_mol
    outside_validated_envelope = x_me2o > 0.5
    if outside_validated_envelope and not allow_out_of_envelope:
        raise ImccCompositionOutsideValidatedEnvelopeError(
            f"X_Me2O={x_me2o:.12g} exceeds validated bound 0.5"
        )
    trust = canonical_backend_name("internal-analytical")
    if trust is None:  # pragma: no cover - fixed repository vocabulary
        raise RuntimeError("internal-analytical trust label is not registered")
    return replace(
        result,
        labels=ImccAdapterLabels(
            identity=labels.identity,
            coverage=labels.coverage,
            envelope_status=(
                "outside_validated" if outside_validated_envelope else "inside"
            ),
            trust=trust,
            flags=labels.flags,
            notices=labels.notices,
            acid_sink_ratio=labels.acid_sink_ratio,
        ),
    )
