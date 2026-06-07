import pytest

from simulator.backends import (
    BackendSelectionPolicy,
    BackendUnavailableError,
    backend_resolution_status,
    resolve_backend,
)
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.melt_backend.base import StubBackend


def test_backend_honesty_stub_resolution_surfaces_unavailable_status():
    backend = resolve_backend("stub", BackendSelectionPolicy.RUNNER_STRICT)

    status = backend_resolution_status(backend)

    assert isinstance(backend, StubBackend)
    assert status.backend_status == "unavailable"
    assert status.authoritative is False
    assert backend.backend_status == "unavailable"
    assert backend.backend_authoritative is False


def test_backend_honesty_stub_rejected_for_real_liquid_fraction_intent():
    with pytest.raises(
        BackendUnavailableError,
        match="gate_liquid_fraction",
    ):
        resolve_backend(
            "stub",
            BackendSelectionPolicy.RUNNER_STRICT,
            required_intents=[ChemistryIntent.GATE_LIQUID_FRACTION],
        )


def test_backend_honesty_stub_equilibrate_does_not_claim_liquid_fraction():
    result = StubBackend().equilibrate(temperature_C=1500.0)

    assert result.status == "unavailable"
    assert result.liquid_fraction is None
    assert result.phase_assemblage_available is False
