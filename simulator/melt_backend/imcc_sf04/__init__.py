"""Public IMCC-SF04 adapter API.

Raw ``solve_*`` kernel entry points are intentionally excluded from public
exports so every caller passes through the adapter's structural trust mapping.
"""

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
    # Adapter API (chunk 3)
    "ImccAdapterLabels",
    "ImccCompositionOutsideValidatedEnvelopeError",
    "ImccLoadedDatapack",
    "ImccMalformedDatapackError",
    "ImccUnprovenDatapackError",
    "evaluate",
    "label_research_datapack",
    "load_datapack",
    # Kernel API (chunk 2)
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
