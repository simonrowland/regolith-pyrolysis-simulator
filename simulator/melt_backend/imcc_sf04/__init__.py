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
    evaluate,
    load_datapack,
)

__all__ = [
    # Adapter API (chunk 3)
    "ImccAdapterLabels",
    "ImccCompositionOutsideValidatedEnvelopeError",
    "ImccLoadedDatapack",
    "ImccMalformedDatapackError",
    "evaluate",
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
