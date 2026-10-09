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

# Production normalizer after the trace-oxide bridge. The pre-bridge digests
# are the parent commit of this change.
_LEDGER_KG_PINS = {
    "lunar_mare_low_ti": "5354d9293e9c2928181053c2f6bcac4bf4f5997659a8e8ac33b9089a90ef7eac",
    "lunar_mare_high_ti": "d259c944ed17e25a1b9ab331c4613ae5efacdd3a40f5a078914ba4fcb080c26a",
    "lunar_highland": "d2cb29d3f32242ccd9842887455e776a71139bb6fe66d9b59836bb3ac5b771a2",
    "lunar_pkt_kreep_average": "28c1713258e2c65ed1fde9d625f5fbc691914d5edca1f50d73bfdfff156b9dce",
    "lunar_spa_kreep_influenced": "0646c9fb3d5e6938fccd5f1533f378473c4f9c8ef27bad6e8625eebe28798db4",
    "targeted_super_kreep_ore": "40a9df616e55df2a750d79d464c4c5656448a5b5452eda0f0bb320581d98f36d",
}

# Same component keys on every B1 feedstock. Oxide-parent traces are ledger oxides.
_LEDGER_KEYS = (
    "Ag",
    "Al2O3",
    "As",
    "Au",
    "B2O3",
    "Ba",
    "Bi",
    "Br",
    "CaO",
    "Cd",
    "Ce2O3",
    "Cl",
    "Co",
    "Cr2O3",
    "Cs2O",
    "Cu2O",
    "Dy2O3",
    "Er2O3",
    "Eu2O3",
    "F",
    "FeO",
    "Ga2O3",
    "Gd2O3",
    "GeO2",
    "HfO2",
    "Ho2O3",
    "I",
    "In2O3",
    "Ir",
    "K2O",
    "La2O3",
    "Li2O",
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
    "PbO",
    "Pr2O3",
    "Pt",
    "Rb2O",
    "S",
    "Sb",
    "Sc2O3",
    "Se",
    "SiO2",
    "Sm2O3",
    "SnO",
    "Sr",
    "Tb2O3",
    "Te",
    "ThO2",
    "TiO2",
    "Tm2O3",
    "UO2",
    "V2O3",
    "W",
    "Y2O3",
    "Yb2O3",
    "Zn",
    "ZrO2",
)

# sha256 of the Build A live pin tables. Re-evaluated below against the catalog.
_LIVE_CHANNEL_PIN_DIGEST = (
    "4aed3badd9aec666e8a56b2ff6e26a3f754fedd686db91131532d05b9352d564"
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
    assert len(production_catalog.species) == 247


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
