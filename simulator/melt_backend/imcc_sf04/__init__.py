"""IMCC-SF04 independent melt-activity shadow engine (r2.1)."""

from simulator.melt_backend.imcc_sf04.adapter import (
    ImccAdapterLabels,
    ImccLoadedDatapack,
    ImccMalformedDatapackError,
    evaluate,
    load_datapack,
)
from simulator.melt_backend.imcc_sf04.kernel import (
    ImccComponentOutsideDomainError,
    ImccCompositionIncompleteError,
    ImccDatapack,
    ImccFerricInputUnsupportedError,
    ImccNonconvergenceError,
    ImccRefusal,
    ImccResult,
    ImccTOutsideDatapackDomainError,
    solve_imcc_sf04,
)

__all__ = [
    # Adapter API (chunk 3)
    "ImccAdapterLabels",
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
    "ImccNonconvergenceError",
    "solve_imcc_sf04",
]
