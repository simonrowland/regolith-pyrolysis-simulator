import pytest

from simulator.state import (
    EnergyRecord,
    EvaporationFlux,
    MAX_STIR_FACTOR,
    clamp_stir_factor,
    clamp_stir_state,
)


def test_clamp_stir_factor_huge_integer_fails_closed() -> None:
    assert clamp_stir_factor(10**1000) == 0.0
    assert clamp_stir_factor(MAX_STIR_FACTOR) == MAX_STIR_FACTOR


def test_clamp_stir_state_mixed_unknown_keys_warns_without_type_error() -> None:
    with pytest.warns(UserWarning, match="ignoring unknown StirState keys"):
        state = clamp_stir_state({"axial": 2.0, "typo": 3.0, 7: 4.0})

    assert state.axial == 2.0
    assert state.radial == 1.0


def test_evaporation_rate_clears_stale_dominant_species() -> None:
    rate = EvaporationFlux(species_kg_hr={"Na": 2.0})
    rate.update_totals()
    assert rate.dominant_species == "Na"

    rate.species_kg_hr.clear()
    rate.update_totals()

    assert rate.total_kg_hr == 0.0
    assert rate.dominant_species == ""


def test_energy_record_recomputes_evaporation_total_after_component_change() -> None:
    record = EnergyRecord(latent_kWh=2.0, dissociation_kWh=3.0)
    record.sum_scoped_energy()
    assert record.evaporation_thermal_kWh == 5.0

    record.latent_kWh = 7.0
    record.dissociation_kWh = 11.0
    record.sum_scoped_energy()

    assert record.evaporation_thermal_kWh == 18.0
    assert record.electrical_plus_evaporation_kWh == 18.0
