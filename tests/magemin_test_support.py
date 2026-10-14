from __future__ import annotations

from simulator.chemistry.kernel import ChemistryIntent
from simulator.core import PyrolysisSimulator


def close_test_owned_magemin_backend(sim: PyrolysisSimulator) -> None:
    """Synchronously close this simulator's test-owned gate fallback backend."""
    provider = sim._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    )
    if provider is None:
        return
    backend = getattr(provider, "_backend", None)
    if backend is None:
        return
    finalizer = getattr(provider, "_backend_finalizer", None)
    if finalizer is not None:
        finalizer.detach()
    backend.close()
