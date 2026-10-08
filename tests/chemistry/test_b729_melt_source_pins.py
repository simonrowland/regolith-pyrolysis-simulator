"""Numeric pins for b-729's melt-source consumers, independent of live engines."""

import pytest
from pathlib import Path

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
PRESSURE_PINS = {
    ("lunar_mare_low_ti", 1800.0): ("0x1.1ea791992acbcp-3", "0x1.1ea791992acbcp-3", "0x1.839f19a7d90c6p-28", "0x1.015bf369919c8p+2"),
    ("lunar_mare_low_ti", 2400.0): ("0x1.80fea08b163d4p+6", "0x1.80fea08b163d4p+6", "0x1.d6d8238b4c8d1p-2", "0x1.39f659f3dab57p+9"),
    ("lunar_mare_high_ti", 1800.0): ("0x1.11899dbdf1e4ep-3", "0x1.11899dbdf1e4ep-3", "0x1.71e264836600dp-28", "0x1.eb2a5865e9dddp+1"),
    ("lunar_mare_high_ti", 2400.0): ("0x1.6f60b0f0d4e5bp+6", "0x1.6f60b0f0d4e5bp+6", "0x1.c14c89290665fp-2", "0x1.2b988217eb56ap+9"),
    ("lunar_highland", 1800.0): ("0x1.12c48275db689p-3", "0x1.12c48275db689p-3", "0x1.689d834cd46e1p-27", "0x1.dedb6c8db7f4bp+2"),
    ("lunar_highland", 2400.0): ("0x1.71079cc2344eap+6", "0x1.71079cc2344eap+6", "0x1.b60a3223e4290p-1", "0x1.24168b228fcf2p+10"),
    ("mars_basalt", 1800.0): ("0x1.27d90ccf5c470p-3", "0x1.27d90ccf5c470p-3", "0x1.3050506caa7fbp-28", "0x1.94184b34c6f22p+1"),
    ("mars_basalt", 2400.0): ("0x1.8d577f23b9e52p+6", "0x1.8d577f23b9e52p+6", "0x1.71a67739b919ep-2", "0x1.ecf887ae0e57ap+8"),
    ("s_type_asteroid_silicate", 1800.0): ("0x1.16603cbd4490ap-3", "0x1.16603cbd4490ap-3", "0x1.211f318bab4dep-30", "0x1.7febf901ec0c9p-1"),
    ("s_type_asteroid_silicate", 2400.0): ("0x1.75e03c32a6d45p+6", "0x1.75e03c32a6d45p+6", "0x1.5f32589d2b6c1p-4", "0x1.d45c608fa9552p+6"),
    ("ci_carbonaceous_chondrite", 1800.0): ("0x1.971fedb5a064ep-4", "0x1.971fedb5a064ep-4", "0x1.46a4f68c1ad80p-30", "0x1.b1bf5eaf7a240p-1"),
    ("ci_carbonaceous_chondrite", 2400.0): ("0x1.11658f3fb533ep+6", "0x1.11658f3fb533ep+6", "0x1.8cc67c74e4c0ap-4", "0x1.089296472c50dp+7"),
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
