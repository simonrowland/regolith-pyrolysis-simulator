"""b-724: constructed h167 incident, without a 170-hour campaign."""

import math
from types import MethodType, SimpleNamespace

import pytest

from simulator.chemistry.kernel import ChemistryIntent, IntentResult
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.core import PyrolysisSimulator
from simulator.fe_redox import (
    _published_intrinsic_melt_fO2,
    feo_iw_log10_fO2_bar,
    holzheid_stoich_feo_gamma_band,
)
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from tests.test_fe_redox_split_diagnostic import _load_yaml, _make_sim


T_K = 2473.15
RAW_LOG_FO2 = -19.5773
INCIDENT_ACTIVITIES = {"Al2": 0.16376, "Ca2": 0.07190}
# Captured on green cb13f9f92 with the incident's logged activities and fO2.
RAW_PRESSURES_PA = {"Al2": 194792347086.33865, "Ca2": 583360.5517652846}


@pytest.fixture
def incident(monkeypatch):
    sim = _make_sim("lunar_mare_low_ti", temperature_C=T_K - 273.15)
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = RAW_LOG_FO2
    catalog = compile_vapour_rail_catalog(_load_yaml("vapor_pressures.yaml"))
    calls = []

    def dispatch(intent, **kwargs):
        assert intent == ChemistryIntent.VAPOR_PRESSURE
        calls.append(kwargs)
        oxygen = 10.0 ** kwargs["control_inputs"]["intrinsic_fO2_log"]
        pressures = {
            species: catalog.evaluator_for(species).evaluate(
                T_K, source_activity=activity, pO2_bar=oxygen,
            ).pressure_pa
            for species, activity in INCIDENT_ACTIVITIES.items()
        }
        return IntentResult(
            intent=intent, status="ok",
            diagnostic={"vapor_pressures_Pa": pressures},
        )

    # Keep the actual publication path; isolate only the downstream dispatch
    # so the reported Al/Ca activities remain the measured incident inputs.
    monkeypatch.setattr(sim, "_dispatch_only", dispatch)
    result = SimpleNamespace(
        vapor_pressures_Pa={}, vapor_pressures_source={}, liquid_fraction=1.0,
        fO2_log=RAW_LOG_FO2, activity_coefficients={}, warnings=[],
    )
    return sim, catalog, calls, result


def test_h167_raw_catalog_pressure_pin(incident):
    _, catalog, _, _ = incident
    for species, activity in INCIDENT_ACTIVITIES.items():
        evaluation = catalog.evaluator_for(species).evaluate(
            T_K, source_activity=activity, pO2_bar=10.0 ** RAW_LOG_FO2,
        )
        assert evaluation.out_of_range is True
        assert evaluation.pressure_pa == pytest.approx(RAW_PRESSURES_PA[species])


def test_h167_intrinsic_publication_uses_feo_buffer(incident):
    sim, _, calls, result = incident
    before = sim.atom_ledger.mol_by_account()
    sim._refresh_vapor_pressures_from_kernel(result)
    activity = melt_oxide_activity("FeO", before["process.cleaned_melt"], temperature_K=T_K)
    divisor = holzheid_stoich_feo_gamma_band(T_K)['conversion_multiplier']
    floor = feo_iw_log10_fO2_bar(T_K, a_feo=activity.activity / divisor)
    assert calls[0]["control_inputs"]["intrinsic_fO2_log"] == floor
    assert calls[0]["fO2_log"] == floor
    publication = sim._last_vapor_pressure_diagnostic["intrinsic_fO2_publication"]
    assert publication["raw_reservoir_fO2_log"] == RAW_LOG_FO2
    assert publication["buffer_oxide"] == "FeO"
    assert publication["floor_binds"] is True
    assert "buffer published" in result.warnings[0]
    for species in INCIDENT_ACTIVITIES:
        # At fixed T/activity, mass action is monotone in fO2 for both dimers.
        assert 0.0 < result.vapor_pressures_Pa[species] < 1.0
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == RAW_LOG_FO2
    assert sim.atom_ledger.mol_by_account() == before


def test_feo_exhaustion_uses_silica_not_implied_ferrous_inventory():
    from simulator.chemistry.ellingham_graph import dissociation_pO2_threshold

    # Ferric-only ledger: an implied Kress split may contain FeO, but no
    # actual FeO remains to buffer the published potential.
    account = {"Fe2O3": 1.0, "SiO2": 4.0, "Al2O3": 1.0, "CaO": 1.0}
    publication = _published_intrinsic_melt_fO2(RAW_LOG_FO2, account, T_K)
    silica = melt_oxide_activity("SiO2", account, temperature_K=T_K)
    expected = math.log10(dissociation_pO2_threshold("Si", T_K, a_oxide=silica.activity))
    assert publication["buffer_oxide"] == "SiO2"
    assert publication["published_intrinsic_fO2_log"] == expected
    assert "FeO" not in publication["candidates"]
    assert publication["inventory_basis"] == "ledger_oxide_inventory_not_kress_implied_split"


@pytest.mark.parametrize("raw", [-3.0, 0.0])
def test_oxidizing_intrinsic_potential_is_unchanged(raw):
    publication = _published_intrinsic_melt_fO2(raw, {"FeO": 1.0, "SiO2": 4.0}, T_K)
    assert publication["published_intrinsic_fO2_log"] == raw
    assert publication["floor_binds"] is False
    assert publication["notice"] == ""


def test_reduction_buffer_hands_over_before_last_feo_disappears():
    account = {"SiO2": 4.0, "Al2O3": 1.0, "FeO": 1e-20}
    trace = _published_intrinsic_melt_fO2(RAW_LOG_FO2, account, T_K)
    del account["FeO"]
    exhausted = _published_intrinsic_melt_fO2(RAW_LOG_FO2, account, T_K)
    assert trace["buffer_oxide"] == exhausted["buffer_oxide"] == "SiO2"
    assert trace["published_intrinsic_fO2_log"] == pytest.approx(exhausted["published_intrinsic_fO2_log"])


def test_silica_exhaustion_uses_next_present_condensed_buffer():
    from simulator.chemistry.ellingham_graph import dissociation_pO2_threshold

    publication = _published_intrinsic_melt_fO2(RAW_LOG_FO2, {"Al2O3": 1.0}, T_K)
    assert publication["buffer_oxide"] == "Al2O3"
    assert publication["published_intrinsic_fO2_log"] == math.log10(
        dissociation_pO2_threshold("Al", T_K, a_oxide=1.0)
    )


def test_real_kernel_retains_published_source_oxygen_and_raw_reservoir(incident, monkeypatch):
    sim, _, _, result = incident
    monkeypatch.setattr(sim, "_dispatch_only", MethodType(PyrolysisSimulator._dispatch_only, sim))
    sim._high_t_melt_activity = "constant_gamma"
    before = sim.atom_ledger.mol_by_account()
    sim._refresh_vapor_pressures_from_kernel(result)
    diagnostic = sim._last_vapor_pressure_diagnostic
    activity = melt_oxide_activity("FeO", before["process.cleaned_melt"], temperature_K=T_K)
    divisor = holzheid_stoich_feo_gamma_band(T_K)['conversion_multiplier']
    expected = feo_iw_log10_fO2_bar(T_K, a_feo=activity.activity / divisor)
    assert diagnostic["source_reaction_fO2_log10"] == expected
    published = diagnostic["intrinsic_fO2_publication"]["published_intrinsic_fO2_log"]
    assert published > RAW_LOG_FO2
    assert diagnostic["source_reaction_fO2_log10"] == published
    assert diagnostic["source_reaction_fO2_bar"] == 10.0 ** published
    assert all(0.0 < result.vapor_pressures_Pa[species] < 1.0 for species in INCIDENT_ACTIVITIES)
    assert sim.atom_ledger.mol_by_account() == before
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == RAW_LOG_FO2
