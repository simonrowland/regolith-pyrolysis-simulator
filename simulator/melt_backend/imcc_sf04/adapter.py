"""Simulator trust label on top of the openimcc dependency adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import openimcc
from openimcc import (
    ImccAdapterLabels as _OpenImccAdapterLabels,
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


@dataclass(frozen=True)
class ImccAdapterLabels(_OpenImccAdapterLabels):
    """Package labels plus this simulator's denylisted evidence class."""

    trust: str = "internal-analytical"


def evaluate(*args: Any, **kwargs: Any) -> ImccResult:
    """Call openimcc and attach the simulator-only trust vocabulary."""

    result = openimcc.evaluate(*args, **kwargs)
    labels = result.labels
    if labels is None:
        return result
    trust = canonical_backend_name("internal-analytical")
    if trust is None:  # pragma: no cover - fixed repository vocabulary
        raise RuntimeError("internal-analytical trust label is not registered")
    return replace(
        result,
        labels=ImccAdapterLabels(
            identity=labels.identity,
            coverage=labels.coverage,
            envelope_status=labels.envelope_status,
            flags=labels.flags,
            notices=labels.notices,
            acid_sink_ratio=labels.acid_sink_ratio,
            trust=trust,
        ),
    )
