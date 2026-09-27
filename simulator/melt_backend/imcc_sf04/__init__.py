"""Simulator backend glue with a compatibility facade for openimcc."""

from simulator.melt_backend.imcc_sf04.adapter import (
    ImccAdapterLabels,
    ImccComponentOutsideDomainError,
    ImccCompositionOutsideValidatedEnvelopeError,
    ImccCompositionIncompleteError,
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
    evaluate,
    label_research_datapack,
    load_datapack,
)

from simulator.melt_backend.imcc_sf04.backend import (
    ImccSf04Backend,
    ImccSf04ExtBackend,
)

__all__ = [
    # MeltBackend glue (simulator-facing)
    "ImccSf04Backend",
    "ImccSf04ExtBackend",
    # Simulator adapter API backed by openimcc
    "ImccAdapterLabels",
    "ImccCompositionOutsideValidatedEnvelopeError",
    "ImccLoadedDatapack",
    "ImccMalformedDatapackError",
    "ImccUnprovenDatapackError",
    "evaluate",
    "label_research_datapack",
    "load_datapack",
    # Dependency kernel types
    "ImccDatapack",
    "ImccResult",
    "ImccRefusal",
    "ImccTOutsideDatapackDomainError",
    "ImccCompositionIncompleteError",
    "ImccFerricInputUnsupportedError",
    "ImccComponentOutsideDomainError",
    "ImccSPComponentRequiresExtensionError",
    "ImccNonconvergenceError",
]
