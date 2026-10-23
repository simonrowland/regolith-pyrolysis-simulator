"""b-724: constructed h167 incident, without a 170-hour campaign."""

from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel import ChemistryIntent, IntentResult
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


def test_h167_intrinsic_publication_pin(incident):
    sim, _, calls, result = incident
    before = sim.atom_ledger.mol_by_account()
    sim._refresh_vapor_pressures_from_kernel(result)
    assert calls[0]["control_inputs"]["intrinsic_fO2_log"] == RAW_LOG_FO2
    assert calls[0]["fO2_log"] == RAW_LOG_FO2
    assert result.vapor_pressures_Pa == pytest.approx(RAW_PRESSURES_PA)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == RAW_LOG_FO2
    assert sim.atom_ledger.mol_by_account() == before
