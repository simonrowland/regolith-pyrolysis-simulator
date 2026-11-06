"""Pins for the d-349 vapor activity reference-state correction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engines.builtin import vapor_pressure as vapor_pressure_module
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.vapour_rail.batch import PressureValue
from simulator.vapour_rail.instrumentation import EffectivePressureSource
from tests.chemistry.conftest import _build_sim


def _lunar_mare_probe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": sim.atom_ledger.mol_by_account(
                    "process.cleaned_melt"
                )
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=0.0,
        control_inputs={"pO2_bar": 1.0, "intrinsic_fO2_log": 0.0},
    )
    return BuiltinVaporPressureProvider(vapor_pressure_data), request


def test_d349_lunar_mare_vapor_pressures_match_green_q79_probe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Pin green a6d75d8b4 values from q79's production provider probe."""

    provider, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = provider.dispatch(request)

    # Provenance: worker-report-q79.md records these source pressures from
    # green a6d75d8b4's BuiltinVaporPressureProvider path, using this same
    # production lunar feedstock account at 1773.15 K and pO2=1 bar.
    expected = {
        "Mg": 2.1010437e-7,
        "Ca": 1.0585356e-11,
        "MgO_gas": 9.1669376e-6,
        "CaO_gas": 1.2830165e-9,
    }
    pressures = result.diagnostic["vapor_pressures_Pa"]
    assert result.status == "ok"
    for species, pressure in expected.items():
        assert pressures[species] == pytest.approx(pressure, rel=1e-6)

    # All six Mg/Ca parent routes use crystalline K: monatomic metal, dimers,
    # and monatomic oxide gas. The producer activity handshake itself remains
    # liquid-referenced, as declared in the catalog.
    provenance = result.diagnostic["vapor_pressure_numerator_provenance"]
    routes = {
        "Mg": ("MgO", 0.90173974),
        "Mg2": ("MgO", 0.90173974),
        "MgO_gas": ("MgO", 0.90173974),
        "Ca": ("CaO", 1.03119892),
        "Ca2": ("CaO", 1.03119892),
        "CaO_gas": ("CaO", 1.03119892),
    }
    for species, (oxide, log10_offset) in routes.items():
        notice = provenance[species]["activity_standard_state_conversion"]
        assert notice["oxide"] == oxide
        assert notice["from_standard_state_id"] == (
            "single_cation_Raoultian_pure_liquid_reference"
        )
        assert notice["to_standard_state_id"] == (
            "single_cation_Raoultian_pure_crystalline_reference"
        )
        assert notice["temperature_K"] == pytest.approx(1773.15)
        assert notice["log10_activity_offset"] == pytest.approx(
            log10_offset, abs=1e-7
        )
        assert notice["authority"] == "bridge"
        assert notice["authority_level"] == "predict_and_flag"
        assert notice["reason"] == (
            "JANAF tabulates the liquid phase below its solid-liquid Gibbs "
            "crossing; this is JANAF's metastable (supercooled) liquid branch"
        )
        assert notice["delta_g_fus_kJ_per_mol"] == pytest.approx(
            30.6109325 if oxide == "MgO" else 35.005622,
            abs=1e-7,
        )
        assert notice["certified_band_K"] == pytest.approx(
            [298.15, 4000.0 if oxide == "MgO" else 3600.0]
        )
        assert provenance[species][
            "vapor_activity_for_crystalline_K_standard_state_id"
        ] == notice["to_standard_state_id"]
    assert result.diagnostic["activities_standard_state"]["phase"] == "liquid"


def test_d349_removing_vapor_conversion_restores_the_fusion_factor(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    monkeypatch,
):
    provider, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    converted = provider.dispatch(request).diagnostic["vapor_pressures_Pa"]["Mg"]

    monkeypatch.setattr(
        vapor_pressure_module,
        "_activity_for_crystalline_vapor_k",
        lambda parent_oxide, activity, temperature_K: (float(activity), None, None),
    )
    unconverted = provider.dispatch(request).diagnostic["vapor_pressures_Pa"]["Mg"]

    # 0.1253892371 is q79's independent JANAF Mg-008/Mg-009
    # exp(-DeltaG_fus/RT) factor at 1773.15 K.
    assert unconverted / converted == pytest.approx(0.1253892371, rel=1e-6)


def test_d349_conversion_extrapolates_and_flags_outside_janaf_overlap():
    above_melting, above_notice, _warning = (
        vapor_pressure_module._activity_for_crystalline_vapor_k(
            "MgO", 0.5, 3200.0
        )
    )
    assert above_notice["delta_g_fus_kJ_per_mol"] < 0.0
    assert above_notice["log10_activity_offset"] < 0.0
    assert above_melting < 0.5

    extrapolated_activity, notice, warning = (
        vapor_pressure_module._activity_for_crystalline_vapor_k(
            "MgO", 0.5, 4001.0
        )
    )
    # JANAF d-144 owner value at 4001 K; independently pinned from the
    # production fusion resolver, including its terminal-chord extrapolation.
    assert extrapolated_activity == pytest.approx(0.24878736429300258, rel=1e-12)
    assert notice["authority"] == "extrapolated"
    assert notice["authority_level"] == "extrapolated"
    assert notice["temperature_K"] == pytest.approx(4001.0)
    assert notice["from_standard_state_id"] in warning
    assert notice["to_standard_state_id"] in warning
    assert "log10_activity_offset=" in warning
    assert "authority_level=extrapolated" in warning


def test_d349_live_evaporation_gateway_uses_crystalline_activity_basis(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider, request = _lunar_mare_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    provider_result = provider.dispatch(request)
    diagnostic = dict(provider_result.diagnostic)
    sim._last_vapor_pressure_diagnostic = diagnostic
    equilibrium = SimpleNamespace(
        temperature_C=1500.0,
        pO2_bar=1.0,
        fO2_log=0.0,
        diagnostics={},
        activity_coefficients=diagnostic["activities"],
        vapor_pressures_Pa=diagnostic["vapor_pressures_Pa"],
    )
    effective_source = EffectivePressureSource(
        "d349-live-gateway",
        diagnostic["vapor_pressures_Pa"],
    )
    batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=1773.15,
        effective_pressure_source=effective_source,
    )
    assert batch is not None

    for species in ("Mg", "Mg2", "MgO_gas", "Ca", "Ca2", "CaO_gas"):
        answer = batch.channel(species)
        assert isinstance(answer.pressure, PressureValue)
        assert answer.pressure.pa == pytest.approx(
            diagnostic["vapor_pressures_Pa"][species], rel=1e-5
        )
        source_activity = answer.source_reaction_activity
        assert source_activity is not None
        assert source_activity.standard_state.phase == "liquid"
        reported = source_activity.derivation[
            "reported_activity_provenance"
        ]
        conversion = reported["activity_standard_state_conversion"]
        assert reported["vapor_activity_for_crystalline_K_standard_state_id"] == (
            conversion["to_standard_state_id"]
        )
        assert conversion["to_standard_state_id"] == (
            "single_cation_Raoultian_pure_crystalline_reference"
        )

    # A crystalline-K route cannot fall back to the liquid handshake value if
    # its converted operand and standard-state ID are absent at the gateway.
    mutated_diagnostic = dict(diagnostic)
    mutated_provenance = {
        species: dict(provenance)
        for species, provenance in diagnostic[
            "vapor_pressure_numerator_provenance"
        ].items()
    }
    mutated_provenance["MgO_gas"].pop("activity_standard_state_conversion")
    mutated_provenance["MgO_gas"].pop("vapor_activity_for_crystalline_K")
    mutated_provenance["MgO_gas"].pop(
        "vapor_activity_for_crystalline_K_standard_state_id"
    )
    mutated_diagnostic["vapor_pressure_numerator_provenance"] = (
        mutated_provenance
    )
    sim._last_vapor_pressure_diagnostic = mutated_diagnostic
    refused_batch = sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=1773.15,
        effective_pressure_source=effective_source,
    )
    assert refused_batch is None
    assert sim._last_vapour_batch_resolve_error["reason"] == (
        "vapour_batch_resolve_failed"
    )
    assert "MgO_gas" in sim._last_vapour_batch_resolve_error["detail"]
