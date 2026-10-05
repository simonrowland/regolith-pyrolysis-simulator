"""t-1139 Build B1 pins taken before the trace-oxide ledger bridge.

The six feedstock digests are ``normalized_feedstock_component_masses_kg``
on a 1000 kg batch. Live channel pins are the Build A pressure, stoich,
and dormant-row tables, re-evaluated from the compiled catalog.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.feedstock_composition import normalized_feedstock_component_masses_kg
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
from simulator.yaml_cache import load_cached_safe_yaml
from tests.test_t1139_build_a_pins import (
    FIRST_BATCH_EXISTING,
    PIN_PO2_BAR,
    PIN_TEMPERATURES_K,
    PRESSURE_PINS,
    STOICH_PINS,
)


ROOT = Path(__file__).resolve().parents[1]
FEEDSTOCKS = yaml.safe_load((ROOT / "data" / "feedstocks.yaml").read_text())
MASS_KG = 1000.0

B1_FEEDSTOCKS = (
    "lunar_mare_low_ti",
    "lunar_mare_high_ti",
    "lunar_highland",
    "lunar_pkt_kreep_average",
    "lunar_spa_kreep_influenced",
    "targeted_super_kreep_ore",
)

# Captured from the production normalizer before the trace-oxide bridge.
_LEDGER_KG_PINS = {
    "lunar_mare_low_ti": "8564b6c17a8da2ab4b2144b3f60f8bc13c52f70eff093a320eff0b9a08c48b8e",
    "lunar_mare_high_ti": "5fb2357b11624c00baee20b149cbf4e766feaf8531c132d904cde8a7940acfc4",
    "lunar_highland": "aaf6a5a3de7b8eeafa0f5c4cdc7c26a0264608fb26c66338f2477cb3dbdcd42b",
    "lunar_pkt_kreep_average": "62fd7298b60c327df77387e0026988a7fb31eabebb22879791ff679bfa6ef146",
    "lunar_spa_kreep_influenced": "e6d74afc1d342b481c6dd60909c806a48db289ca65515ec85d0558ecefbf9776",
    "targeted_super_kreep_ore": "2829c35bd650d5240876cea9873b9317b1bb334e69f9c2883340fadedad94208",
}

# Same component keys on every B1 feedstock. Trace metals are still element keys.
_LEDGER_KEYS = (
    "Ag",
    "Al2O3",
    "As",
    "Au",
    "B",
    "Ba",
    "Bi",
    "Br",
    "CaO",
    "Cd",
    "Ce2O3",
    "Cl",
    "Co",
    "Cr2O3",
    "Cs",
    "Cu",
    "Dy2O3",
    "Er2O3",
    "Eu2O3",
    "F",
    "FeO",
    "Ga",
    "Gd2O3",
    "Ge",
    "HfO2",
    "Ho2O3",
    "I",
    "In",
    "Ir",
    "K2O",
    "La2O3",
    "Li",
    "Lu2O3",
    "MgO",
    "MnO",
    "Mo",
    "Na2O",
    "Nb",
    "Nd2O3",
    "Ni",
    "Os",
    "P2O5",
    "Pb",
    "Pr2O3",
    "Pt",
    "Rb",
    "S",
    "Sb",
    "Sc2O3",
    "Se",
    "SiO2",
    "Sm2O3",
    "Sn",
    "Sr",
    "Tb2O3",
    "Te",
    "ThO2",
    "TiO2",
    "Tm2O3",
    "UO2",
    "V",
    "W",
    "Y2O3",
    "Yb2O3",
    "Zn",
    "ZrO2",
)

# sha256 of the Build A live pin tables. Re-evaluated below against the catalog.
_LIVE_CHANNEL_PIN_DIGEST = (
    "46c991fb21610b0a504a62c18f04bbee48ab4ffbc51894915fe54d783edaf153"
)


def _float_hex_tree(value: Any) -> Any:
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, dict):
        return {key: _float_hex_tree(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_float_hex_tree(item) for item in value]
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(
        _float_hex_tree(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_b1_feedstock_ledger_composition_is_pinned() -> None:
    assert set(B1_FEEDSTOCKS) <= set(FEEDSTOCKS)
    for key in B1_FEEDSTOCKS:
        masses = normalized_feedstock_component_masses_kg(
            FEEDSTOCKS[key], MASS_KG
        )
        assert _digest(masses) == _LEDGER_KG_PINS[key], key
        assert tuple(sorted(masses)) == _LEDGER_KEYS, key
        assert sum(masses.values()) == pytest.approx(MASS_KG, abs=1e-9), key


def test_live_channel_pin_tables_are_unchanged() -> None:
    payload = {
        "pressure": {key: list(value) for key, value in sorted(PRESSURE_PINS.items())},
        "stoich": {key: list(value) for key, value in sorted(STOICH_PINS.items())},
        "dormant": list(FIRST_BATCH_EXISTING),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    assert hashlib.sha256(encoded).hexdigest() == _LIVE_CHANNEL_PIN_DIGEST


@pytest.fixture(scope="module")
def production_catalog():
    payload = load_cached_safe_yaml(
        (ROOT / "data" / "vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    return compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_live_catalog_species_count_stays_pinned(production_catalog) -> None:
    assert len(production_catalog.species) == 231


@pytest.mark.parametrize("species_id", sorted(PRESSURE_PINS))
def test_live_channel_pressures_match_the_build_a_pins(
    production_catalog, species_id: str
) -> None:
    compiled = production_catalog.species[species_id]
    evaluator = compiled.evaluator
    assert evaluator is not None
    kwargs: dict[str, float] = {"pO2_bar": PIN_PO2_BAR}
    if evaluator.activity_exponent:
        kwargs["source_activity"] = 1.0
    for temperature_K, hex_value in zip(
        PIN_TEMPERATURES_K, PRESSURE_PINS[species_id]
    ):
        result = evaluator.evaluate(temperature_K, **kwargs)
        assert result.pressure_pa.hex() == hex_value, species_id
