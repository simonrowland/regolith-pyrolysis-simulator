"""Numeric pins for b-729's melt-source consumers, independent of live engines."""

from pathlib import Path

import pytest

from simulator.accounting.formulas import resolve_species_formula
from simulator.config import load_config_bundle
from simulator.feedstock_composition import resolve_feedstock_composition
from simulator.chemistry.ellingham_graph import effective_equilibrium_pressure_Pa
from simulator.chemistry.melt_activity import MELT_OXIDE_CATIONS_PER_FORMULA, melt_oxide_activity
from simulator.melt_backend.alphamelts import AlphaMELTSBackend


FEEDSTOCKS = (
    "lunar_mare_low_ti", "lunar_mare_high_ti", "lunar_highland",
    "mars_basalt", "s_type_asteroid_silicate", "ci_carbonaceous_chondrite",
)
TEMPERATURES_K = (1800.0, 2400.0)
# b-729: 2400 K uses the catalog's flagged physical continuation.
PRESSURE_PINS = {
    ("lunar_mare_low_ti", 1800.0): ("0x1.4b6e943922b7cp-30", "0x1.4b6e943922b7cp-30", "0x1.839f19a7d90c9p-28", "0x1.839f19a7d90c9p-28"),
    ("lunar_mare_low_ti", 2400.0): ("0x1.388e1b674848fp+2", "0x1.388e1b674848fp+2", "0x1.d88a532b372f8p-2", "0x1.d88a532b372f8p-2"),
    ("lunar_mare_high_ti", 1800.0): ("0x1.3c44176d1eb1bp-30", "0x1.3c44176d1eb1bp-30", "0x1.71e264836600ep-28", "0x1.71e264836600ep-28"),
    ("lunar_mare_high_ti", 2400.0): ("0x1.2a40bf8c578c9p+2", "0x1.2a40bf8c578c9p+2", "0x1.c2eada9b686d7p-2", "0x1.c2eada9b686d7p-2"),
    ("lunar_highland", 1800.0): ("0x1.3db02c65a3bb9p-30", "0x1.3db02c65a3bb9p-30", "0x1.689d834cd46d8p-27", "0x1.689d834cd46d8p-27"),
    ("lunar_highland", 2400.0): ("0x1.2b9817fdcb3c6p+2", "0x1.2b9817fdcb3c6p+2", "0x1.b79e21a7e2805p-1", "0x1.b79e21a7e2805p-1"),
    ("mars_basalt", 1800.0): ("0x1.560fb0a41e131p-30", "0x1.560fb0a41e131p-30", "0x1.3050506caa7f2p-28", "0x1.3050506caa7f2p-28"),
    ("mars_basalt", 2400.0): ("0x1.42943c6dd2a07p+2", "0x1.42943c6dd2a07p+2", "0x1.72fb5619b4be6p-2", "0x1.72fb5619b4be6p-2"),
    ("s_type_asteroid_silicate", 1800.0): ("0x1.41dc318f2ee62p-30", "0x1.41dc318f2ee62p-30", "0x1.211f318bab4e4p-30", "0x1.211f318bab4e4p-30"),
    ("s_type_asteroid_silicate", 2400.0): ("0x1.2f8748cb485c8p+2", "0x1.2f8748cb485c8p+2", "0x1.6076332c23543p-4", "0x1.6076332c23543p-4"),
    ("ci_carbonaceous_chondrite", 1800.0): ("0x1.d6b8696bcefa3p-31", "0x1.d6b8696bcefa3p-31", "0x1.46a4f68c1ad78p-30", "0x1.46a4f68c1ad78p-30"),
    ("ci_carbonaceous_chondrite", 2400.0): ("0x1.bbe908834dffbp+1", "0x1.bbe908834dffbp+1", "0x1.8e345eb71df9ep-4", "0x1.8e345eb71df9ep-4"),
}


def melt_source_pressures(feedstock, temperature_K, vapor_pressure_data):
    feedstocks = load_config_bundle(Path(__file__).resolve().parents[2] / "data").feedstocks
    composition = resolve_feedstock_composition(feedstocks[feedstock]).canonical_wt_pct
    oxide_mol = {
        oxide: wt / resolve_species_formula(oxide, None).molar_mass_kg_per_mol()
        for oxide, wt in composition.items()
        if oxide in MELT_OXIDE_CATIONS_PER_FORMULA
    }
    activities = {
        oxide: melt_oxide_activity(
            oxide, oxide_mol, temperature_K=temperature_K
        ).activity
        for oxide in ("SiO2", "Al2O3")
    }
    backend = AlphaMELTSBackend()
    fallback = backend._activities_times_antoine(
        temperature_K - 273.15, activities, composition,
        pO2_bar=1e-9,
    )
    return tuple(
        pressure
        for species, oxide in (("Si", "SiO2"), ("Al", "Al2O3"))
        for pressure in (
            effective_equilibrium_pressure_Pa(
                species, temperature_K, 1e-9, a_oxide=activities[oxide],
                vapor_pressure_data=vapor_pressure_data,
            ),
            fallback[species],
        )
    )


@pytest.mark.parametrize("feedstock", FEEDSTOCKS)
@pytest.mark.parametrize("temperature_K", TEMPERATURES_K)
def test_melt_source_pressure_pin(feedstock, temperature_K, vapor_pressure_data):
    # Order: Si graph/fallback, Al graph/fallback; captured from executable paths.
    actual = melt_source_pressures(feedstock, temperature_K, vapor_pressure_data)
    assert tuple(value.hex() for value in actual) == PRESSURE_PINS[(feedstock, temperature_K)]


@pytest.mark.parametrize("temperature_K", TEMPERATURES_K)
def test_melt_source_consumers_use_catalog_reaction(temperature_K, vapor_pressure_data):
    from simulator.vapour_rail.catalog import compiled_catalog_for

    catalog = compiled_catalog_for(vapor_pressure_data.catalog_payload)
    backend = AlphaMELTSBackend()
    fallback = backend._activities_times_antoine(
        temperature_K - 273.15, {"SiO2": 0.4, "Al2O3": 0.4}, {}, pO2_bar=1e-9,
    )
    for species in ("Si", "Al"):
        expected = catalog.evaluator_for(species).evaluate(
            temperature_K, source_activity=0.4, pO2_bar=1e-9,
        )
        graph = effective_equilibrium_pressure_Pa(
            species, temperature_K, 1e-9, a_oxide=0.4,
            vapor_pressure_data=vapor_pressure_data,
        )
        assert graph == expected.pressure_pa
        assert fallback[species] == expected.pressure_pa
        assert expected.out_of_range == (temperature_K == 2400.0)


def test_pure_sidecar_is_not_a_melt_source_input(monkeypatch, vapor_pressure_data):
    import copy

    backend = AlphaMELTSBackend()
    table = copy.deepcopy(backend._load_vapor_pressure_table())
    for species in ("Si", "Al"):
        table[species]["pure_component_antoine"] = {"A": 50.0, "B": 1.0, "C": 0.0}
    monkeypatch.setattr(backend, "_vapor_pressure_table", table)
    actual = backend._activities_times_antoine(
        1800.0 - 273.15, {"SiO2": 0.4, "Al2O3": 0.4}, {}, pO2_bar=1e-9,
    )
    for species in ("Si", "Al"):
        assert actual[species] == effective_equilibrium_pressure_Pa(
            species, 1800.0, 1e-9, a_oxide=0.4, vapor_pressure_data=vapor_pressure_data,
        )


def test_melt_source_fallback_refuses_missing_oxygen_input():
    backend = AlphaMELTSBackend()
    with pytest.raises(RuntimeError, match="without pO2_bar"):
        backend._activities_times_antoine(1800.0 - 273.15, {"SiO2": 0.4}, {})
