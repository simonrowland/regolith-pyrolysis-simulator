from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from engines.builtin._common import resolve_transport_pO2_bar
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.environment import (
    ASTEROID_VACUUM_FLOOR_BAR,
    DEFAULT_VACUUM_FLOOR_BAR,
    MARS_DATUM_PRESSURE_BAR,
    MARS_OLYMPUS_PRESSURE_BAR,
    MOON_VACUUM_FLOOR_BAR,
    CoverGasCompositionUnavailable,
    cover_gas_partial_pressures,
    oxygen_numerical_floor_bar_for_body,
    vacuum_floor_bar_for_body,
    vacuum_floor_bar_for_environment,
)
from simulator.melt_backend.base import InternalAnalyticalBackend

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _load_yaml(name: str):
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8"))


def _request(control_inputs: dict[str, float | str]) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={},
            species_formula_registry={},
        ),
        temperature_C=1600.0,
        pressure_bar=0.0,
        fO2_log=None,
        control_inputs=control_inputs,
    )


def _fo2_request(fO2_log: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={},
            species_formula_registry={},
        ),
        temperature_C=1600.0,
        pressure_bar=0.0,
        fO2_log=fO2_log,
        control_inputs={},
    )


def _sim(feedstocks: dict | None = None) -> PyrolysisSimulator:
    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        _load_yaml("setpoints.yaml"),
        feedstocks or _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("moon", MOON_VACUUM_FLOOR_BAR),
        ("lunar", MOON_VACUUM_FLOOR_BAR),
        ("asteroid", ASTEROID_VACUUM_FLOOR_BAR),
        ("deep_space", ASTEROID_VACUUM_FLOOR_BAR),
        ("mars", MARS_DATUM_PRESSURE_BAR),
        ("unknown", DEFAULT_VACUUM_FLOOR_BAR),
        ("", DEFAULT_VACUUM_FLOOR_BAR),
    ],
)
def test_body_vacuum_floor_mapping(body: str, expected: float) -> None:
    assert vacuum_floor_bar_for_body(body) == pytest.approx(expected)


def test_mars_environment_floor_uses_explicit_ambient_pressure() -> None:
    assert vacuum_floor_bar_for_environment(
        body="mars",
        ambient_pressure_bar=MARS_OLYMPUS_PRESSURE_BAR,
    ) == pytest.approx(MARS_OLYMPUS_PRESSURE_BAR)


def test_resolve_transport_po2_accepts_lunar_nanotorr_floor() -> None:
    request = _request(
        {
            "pO2_bar": MOON_VACUUM_FLOOR_BAR,
            "vacuum_floor_bar": MOON_VACUUM_FLOOR_BAR,
        }
    )

    assert resolve_transport_pO2_bar(request) == pytest.approx(
        MOON_VACUUM_FLOOR_BAR
    )


def test_default_unknown_body_preserves_existing_1e_9_transport_floor() -> None:
    request = _request({"pO2_bar": DEFAULT_VACUUM_FLOOR_BAR})

    assert resolve_transport_pO2_bar(request) == pytest.approx(
        DEFAULT_VACUUM_FLOOR_BAR
    )

    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    assert sim.melt.body == ""
    assert sim._vacuum_floor_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)
    assert sim._commanded_pO2_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)


def test_resolve_transport_po2_wraps_fo2_overflow_as_value_error() -> None:
    with pytest.raises(ValueError, match="fO2_log=309.*finite pO2_bar range"):
        resolve_transport_pO2_bar(_fo2_request(309.0))


def test_explicit_feedstock_body_sets_run_vacuum_floor() -> None:
    feedstocks = copy.deepcopy(_load_yaml("feedstocks.yaml"))
    feedstocks["lunar_mare_low_ti"]["body"] = "moon"
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    assert sim.melt.body == "moon"
    assert sim._vacuum_floor_bar() == pytest.approx(MOON_VACUUM_FLOOR_BAR)
    assert sim._commanded_pO2_bar() == pytest.approx(MOON_VACUUM_FLOOR_BAR)


def test_intrinsic_melt_fo2_clamp_lowers_to_vacuum_body_floor() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    default_clamped = sim._compute_intrinsic_melt_fO2(temperature_K=1000.0)
    sim.melt.body = "moon"
    lunar_clamped = sim._compute_intrinsic_melt_fO2(temperature_K=1000.0)

    assert default_clamped == pytest.approx(math.log10(DEFAULT_VACUUM_FLOOR_BAR))
    assert lunar_clamped == pytest.approx(math.log10(MOON_VACUUM_FLOOR_BAR))
    assert lunar_clamped < default_clamped


def _mars_sim():
    feedstocks = copy.deepcopy(_load_yaml("feedstocks.yaml"))
    # Reuse a fully declared silicate input to isolate the environment owner.
    feedstocks['lunar_mare_low_ti'].update({
        'body': 'mars',
        'environment': {'surface_pressure_mbar': 6.1, 'atmosphere': 'CO2'},
    })
    sim = _sim(feedstocks)
    sim.load_batch('lunar_mare_low_ti', mass_kg=1000.0)
    from simulator.state import Atmosphere
    sim.melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
    return sim


def test_mars_total_pressure_is_not_oxygen_floor_or_intrinsic_seed():
    sim = _mars_sim()
    assert sim._vacuum_floor_bar() == pytest.approx(6.1e-3)
    assert sim._oxygen_numerical_floor_bar() == DEFAULT_VACUUM_FLOOR_BAR
    assert sim.melt.melt_fO2_log == -9.0
    sim.melt.temperature_C = 1800.0 - 273.15
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas',
        {'O2': sim._headspace_o2_mol_for_pO2_bar(3.7e-7)},
        source='D2 audit ledger oxygen', material_origin='feedstock',
    )
    state = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert state.headspace_ledger_pO2_bar == pytest.approx(3.7e-7)
    assert 7.1e-5 < state.headspace_transport_pO2_bar < 7.4e-5
    # Actual builtin request admission must not reinstate the total CO2 floor.
    assert resolve_transport_pO2_bar(_request({
        'body': 'mars', 'ambient_pressure_bar': 6.1e-3,
        'pO2_bar': state.headspace_transport_pO2_bar,
    })) == state.headspace_transport_pO2_bar
    sim._get_equilibrium()
    assert sim._last_vapor_pressure_diagnostic['pO2_bar'] == pytest.approx(
        state.headspace_transport_pO2_bar
    )
    assert sim._last_vapor_pressure_diagnostic['co2_dissociation']['notices']


def test_transport_uses_the_existing_headspace_gas_temperature_owner():
    sim = _mars_sim()
    sim.melt.temperature_C = 1800.0 - 273.15
    hot = sim._headspace_transport_pO2_bar_from_ledger(0.0)
    sim._overhead_headspace_config['temperature_offset_K'] = -300.0
    cooler = sim._headspace_transport_pO2_bar_from_ledger(0.0)
    assert sim._headspace_temperature_K() == 1500.0
    assert cooler < hot / 5.0
    assert sim._headspace_co2_dissociation_diagnostic['notices'][0]['temperature_K'] == 1500.0


@pytest.mark.parametrize('body', ['moon', 'asteroid', 'earth', 'unknown', ''])
def test_airless_numerical_oxygen_guards_preserve_existing_resolution(body):
    assert oxygen_numerical_floor_bar_for_body(body) == vacuum_floor_bar_for_body(body)


def test_earth_air_oxygen_is_a_mole_fraction_not_total_pressure():
    assert cover_gas_partial_pressures('air', 1.0)['O2'] == 0.21
    assert cover_gas_partial_pressures('96% CO2', 6.0) == {'CO2': 5.76}
    sim = _mars_sim()
    sim.melt.body = 'earth'
    sim.melt.ambient_atmosphere = 'air'
    sim.melt.ambient_pressure_mbar = 1000.0
    sim.melt.p_total_mbar = 1000.0
    assert sim._headspace_transport_pO2_bar_from_ledger(0.01) == pytest.approx(0.22)


@pytest.mark.parametrize('species', ['Ar', 'Kr', 'He', 'N2', 'O2', 'CO2'])
def test_declared_pure_cover_gas_preserves_canonical_species(species):
    assert cover_gas_partial_pressures(species, 0.1) == {species: 0.1}


@pytest.mark.parametrize('declaration', ['', 'unknown gas', 'not_reported'])
def test_positive_ambient_without_composition_refuses(declaration):
    sim = _mars_sim()
    sim.melt.ambient_atmosphere = declaration
    with pytest.raises(CoverGasCompositionUnavailable, match='cover-gas composition.*atmosphere'):
        sim._headspace_transport_pO2_bar_from_ledger(3.7e-7)


def test_controlled_lab_co2_uses_existing_residual_background_pressure_owner():
    from simulator.chemistry.offgas_fo2 import co2_dissociation_partials_bar
    from simulator.state import Atmosphere

    sim = _mars_sim()
    sim.melt.temperature_C = 1800.0 - 273.15
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.p_total_mbar = 1000.0
    sim.melt.pO2_mbar = 900.0
    sim.melt.background_gas_species = 'CO2'
    sim.melt.background_gas_mole_fraction = 1.0
    expected = co2_dissociation_partials_bar(1800.0, 0.1, 0.9, total_pressure_bar=1.0)
    assert sim._headspace_transport_pO2_bar_from_ledger(0.0) == expected['p_O2_bar']
    assert sim._headspace_co2_dissociation_diagnostic['p_CO2_bar'] == expected['p_CO2_bar']


def test_unknown_controlled_background_is_not_assumed_inert():
    from simulator.state import Atmosphere

    sim = _mars_sim()
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.p_total_mbar = 1000.0
    sim.melt.pO2_mbar = 900.0
    sim.melt.background_gas_species = 'unknown gas'
    sim.melt.background_gas_mole_fraction = 1.0
    with pytest.raises(CoverGasCompositionUnavailable, match='cover-gas composition'):
        sim._headspace_transport_pO2_bar_from_ledger(0.0)


def test_cold_mars_ambient_co2_keeps_raw_physical_po2_below_guard():
    sim = _mars_sim()
    physical = sim._headspace_transport_pO2_bar_from_ledger(0.0)
    assert 0.0 < physical < sim._oxygen_numerical_floor_bar()
    sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert sim.melt.oxygen_reservoir.headspace_transport_pO2_bar == physical
    assert sim._vapor_pressure_dispatch_pO2_bar() == sim._oxygen_numerical_floor_bar()
    result = sim._headspace_co2_dissociation_diagnostic
    assert sum(result[k] for k in ("p_CO2_bar", "p_CO_bar", "p_O2_bar", "p_inert_bar")) == pytest.approx(0.0061)


def test_isolated_mars_chamber_does_not_inherit_ambient_co2():
    from simulator.state import Atmosphere
    sim = _mars_sim()
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.p_total_mbar = 0.001
    assert sim._headspace_transport_pO2_bar_from_ledger(0.0) == 0.0


def test_mars_ambient_cleanup_with_co_requires_a_reactive_boundary():
    from simulator.campaigns import CampaignPressureSetpointRefusal
    sim = _mars_sim()
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"CO": 1.0},
                                    source="actual cleanup gas", material_origin="feedstock")
    with pytest.raises(CampaignPressureSetpointRefusal, match="reactive CO/CO2"):
        sim._headspace_transport_pO2_bar_from_ledger(0.0)


def test_controlled_co2_keeps_unselected_background_as_inert():
    from simulator.chemistry.offgas_fo2 import co2_dissociation_partials_bar
    from simulator.state import Atmosphere
    sim = _mars_sim()
    sim.melt.temperature_C = 1800.0 - 273.15
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.p_total_mbar = 1000.0
    sim.melt.pO2_mbar = 900.0
    sim.melt.background_gas_species = "CO2"
    sim.melt.background_gas_mole_fraction = 0.5
    expected = co2_dissociation_partials_bar(1800.0, 0.05, 0.9,
        total_pressure_bar=1.0, p_inert_initial_bar=0.05)
    assert sim._headspace_transport_pO2_bar_from_ledger(0.0) == expected["p_O2_bar"]
    assert sim._headspace_co2_dissociation_diagnostic["p_inert_bar"] == expected["p_inert_bar"]
