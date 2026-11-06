"""Pins for the d-349 vapor activity reference-state correction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engines.builtin import vapor_pressure as vapor_pressure_module
from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry import ellingham_graph
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.chemistry.melt_activity import (
    MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE,
)
from simulator.diagnostic_helpers.alphamelts_volatility import (
    _analytical_vapor_pressures_from_activities,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.vapour_rail.batch import PressureValue
from simulator.vapour_rail.instrumentation import EffectivePressureSource
from simulator.vapour_rail import catalog as catalog_module
from tests.chemistry.conftest import _build_sim

_D349_VAPOR_ROUTES = ("Mg", "Mg2", "MgO_gas", "Ca", "Ca2", "CaO_gas")
# Same T, pO2, and activity. These measured metal-route ratios preserve the
# legacy provider's rounded simulator.state R against the catalog's CODATA R.
_D349_GATEWAY_TO_PROVIDER_PRESSURE_RATIO = {
    "Mg": 1.00000782778,
    "Mg2": 1.0,
    "MgO_gas": 1.0,
    "Ca": 1.00000951030,
    "Ca2": 1.0,
    "CaO_gas": 1.0,
}


def _lunar_mare_probe(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    sim=None,
):
    if sim is None:
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


def _live_gateway_probe(vapor_pressure_data, feedstocks_data, setpoints_data):
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
        sim=sim,
    )
    diagnostic = dict(provider.dispatch(request).diagnostic)
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
    return sim, diagnostic, equilibrium, effective_source


def _diagnostic_with_route_provenance(
    diagnostic,
    species,
    *,
    remove=(),
    **updates,
):
    mutated = dict(diagnostic)
    provenance = {
        route: dict(route_provenance)
        for route, route_provenance in diagnostic[
            "vapor_pressure_numerator_provenance"
        ].items()
    }
    route_provenance = provenance[species]
    for key in remove:
        route_provenance.pop(key, None)
    route_provenance.update(updates)
    mutated["vapor_pressure_numerator_provenance"] = provenance
    return mutated


def _resolve_live_gateway_batch(sim, diagnostic, equilibrium, effective_source):
    sim._last_vapor_pressure_diagnostic = diagnostic
    return sim._resolve_evaporation_vapour_batch(
        equilibrium,
        temperature_K=1773.15,
        effective_pressure_source=effective_source,
    )


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
        assert pressures[species] == pytest.approx(
            pressure,
            rel=1e-6,
            abs=0,
        )

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
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    batch = _resolve_live_gateway_batch(
        sim,
        diagnostic,
        equilibrium,
        effective_source,
    )
    assert batch is not None

    for species in _D349_VAPOR_ROUTES:
        answer = batch.channel(species)
        assert isinstance(answer.pressure, PressureValue)
        gateway_provider_ratio = (
            answer.pressure.pa
            / diagnostic["vapor_pressures_Pa"][species]
        )
        assert gateway_provider_ratio == pytest.approx(
            _D349_GATEWAY_TO_PROVIDER_PRESSURE_RATIO[species],
            rel=1e-9,
            abs=0,
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

def test_d349_live_gateway_requires_the_crystalline_id_on_every_route(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    required_fields = (
        "activity_standard_state_conversion",
        "vapor_activity_for_crystalline_K",
        "vapor_activity_for_crystalline_K_standard_state_id",
    )
    for species in _D349_VAPOR_ROUTES:
        mutated = _diagnostic_with_route_provenance(
            diagnostic,
            species,
            remove=required_fields,
        )
        refused_batch = _resolve_live_gateway_batch(
            sim,
            mutated,
            equilibrium,
            effective_source,
        )
        assert refused_batch is None, species
        assert sim._last_vapour_batch_resolve_error["reason"] == (
            "vapour_batch_resolve_failed"
        )
        assert species in sim._last_vapour_batch_resolve_error["detail"]


def test_d349_live_gateway_raw_liquid_assignment_fails_every_route(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim, diagnostic, equilibrium, effective_source = _live_gateway_probe(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    for species in _D349_VAPOR_ROUTES:
        mutated = _diagnostic_with_route_provenance(
            diagnostic,
            species,
            vapor_activity_for_crystalline_K=diagnostic["activities"][species],
        )
        batch = _resolve_live_gateway_batch(
            sim,
            mutated,
            equilibrium,
            effective_source,
        )
        assert batch is not None, species
        answer = batch.channel(species)
        assert isinstance(answer.pressure, PressureValue), species
        correct_pressure = (
            diagnostic["vapor_pressures_Pa"][species]
            * _D349_GATEWAY_TO_PROVIDER_PRESSURE_RATIO[species]
        )
        assert answer.pressure.pa != pytest.approx(
            correct_pressure,
            rel=1e-9,
            abs=0,
        ), species


def test_d349_alphamelts_fallback_skips_undeclared_mgo_cao_standards(
    monkeypatch,
):
    backend = AlphaMELTSBackend()

    def unexpected_catalog_k(*args, **kwargs):
        raise AssertionError("untyped AlphaMELTS activity reached crystalline K")

    monkeypatch.setattr(
        ellingham_graph,
        "effective_equilibrium_pressure_Pa",
        unexpected_catalog_k,
    )
    projection = backend._activities_times_antoine_or_fail(
        1500.0,
        {"MgO": 0.4, "CaO": 0.3},
        {"MgO": 10.0, "CaO": 10.0},
        pO2_bar=1.0,
        context="d-349 standard-state guard test",
    )

    assert projection.reason == "activity_standard_state_undeclared"
    for species, parent_oxide in (("Mg", "MgO"), ("Ca", "CaO")):
        notice = backend._antoine_activity_standard_state_notices[species]
        assert notice["reason"] == "activity_standard_state_undeclared"
        assert notice["parent_oxide"] == parent_oxide
        assert notice["source_standard_state_id"] is None
        assert notice["required_standard_state_id"] == (
            MELT_OXIDE_CRYSTALLINE_ACTIVITY_REFERENCE_STATE
        )
    diagnostics = backend._vapor_pressure_diagnostics(
        {}, {}, projection
    )
    assert set(
        diagnostics["vapor_activity_standard_state_notices_by_species"]
    ) == {"Mg", "Ca"}
    assert diagnostics["vapor_pressure_backend_status_reason"] == (
        "activity_standard_state_undeclared"
    )


def test_d349_alphamelts_volatility_diagnostic_skips_untyped_k_activity(
    vapor_pressure_data,
    monkeypatch,
):
    class CatalogThatMustNotEvaluate:
        def evaluator_for(self, species):
            if species in {"Mg", "Ca"}:
                raise AssertionError(
                    f"{species} reached crystalline K with undeclared activity"
                )
            return object()

    monkeypatch.setattr(
        catalog_module,
        "compiled_catalog_for",
        lambda _payload: CatalogThatMustNotEvaluate(),
    )
    result = _analytical_vapor_pressures_from_activities(
        vapor_pressure_data=vapor_pressure_data,
        temperature_C=1600.0,
        pO2_bar=1.0e-9,
        melt_oxide_activities={"MgO": 0.4, "CaO": 0.3},
        composition_wt_pct={"MgO": 10.0, "CaO": 10.0},
    )

    assert "Mg" not in result["species"]
    assert "Ca" not in result["species"]
    for species in ("Mg", "Ca"):
        assert any(
            f"species={species} " in notice
            and "reason=activity_standard_state_undeclared" in notice
            and "required_standard_state_id="
            "single_cation_Raoultian_pure_crystalline_reference" in notice
            for notice in result["warnings"]
        )
