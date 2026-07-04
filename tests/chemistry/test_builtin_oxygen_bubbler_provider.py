from __future__ import annotations

import pytest

from engines.builtin.oxygen_bubbler import BuiltinOxygenBubblerProvider
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from tests.chemistry.conftest import _atom_check, _build_sim


def _request(sim, *, intent=ChemistryIntent.OXYGEN_BUBBLER, controls=None):
    return IntentRequest(
        intent=intent,
        account_view=ProviderAccountView(
            accounts={
                "reservoir.fo2_buffer": {"O2": 100.0},
                "process.overhead_gas": {},
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1500.0,
        pressure_bar=1.0e-6,
        fO2_log=-8.0,
        control_inputs=dict(controls or {}),
    )


def test_oxygen_bubbler_provider_declares_intent_and_accounts() -> None:
    provider = BuiltinOxygenBubblerProvider()
    profile = provider.capability_profile()

    assert profile.intents == frozenset({ChemistryIntent.OXYGEN_BUBBLER})
    assert profile.is_authoritative_for == frozenset({
        ChemistryIntent.OXYGEN_BUBBLER
    })
    assert profile.declared_accounts == frozenset({
        "reservoir.fo2_buffer",
        "process.overhead_gas",
    })


def test_oxygen_bubbler_provider_rejects_wrong_intent(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinOxygenBubblerProvider()

    result = provider.dispatch(
        _request(sim, intent=ChemistryIntent.OXYGEN_RESERVOIR_EXCHANGE)
    )

    assert result.status == "unsupported"
    assert result.transition is None


def test_oxygen_bubbler_provider_zero_passthrough_is_diagnostic_noop(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinOxygenBubblerProvider()

    result = provider.dispatch(
        _request(
            sim,
            controls={
                "injected_mol": 2.0,
                "absorbed_mol": 2.0,
                "passthrough_mol": 0.0,
                "source": "test",
            },
        )
    )

    assert result.status == "ok"
    assert result.transition is None
    assert result.diagnostic["transition"] == "none:zero_passthrough"
    assert result.diagnostic["absorbed_mol"] == pytest.approx(2.0)


def test_oxygen_bubbler_provider_passthrough_transition_conserves_o2(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
) -> None:
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider = BuiltinOxygenBubblerProvider()

    result = provider.dispatch(
        _request(
            sim,
            controls={
                "injected_mol": 3.0,
                "absorbed_mol": 1.25,
                "passthrough_mol": 1.75,
                "source": "test",
            },
        )
    )

    proposal = result.transition
    assert proposal is not None
    assert proposal.reason == "oxygen_bubbler_passthrough"
    assert proposal.debits["reservoir.fo2_buffer"]["O2"] == pytest.approx(1.75)
    assert proposal.credits["process.overhead_gas"]["O2"] == pytest.approx(1.75)
    assert _atom_check(
        proposal,
        sim.species_formula_registry,
        tol=1e-12,
    ) == pytest.approx({"O": 0.0})
