import numbers

import pytest

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
import web.events as events


def _sim():
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Payload contract",
                "composition_wt_pct": {"SiO2": 60.0, "FeO": 40.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    return sim


def _required_event_helper(name):
    assert hasattr(events, name), f"web.events.{name} is required"
    return getattr(events, name)


def _assert_numeric_leaf_values(value, path="payload"):
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_numeric_leaf_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_numeric_leaf_values(item, f"{path}[{index}]")
    else:
        assert isinstance(value, numbers.Real), f"{path} must be numeric"


def test_socket_start_payload_keeps_existing_kg_shape():
    sim = _sim()
    start_payload = _required_event_helper("_start_payload")

    payload = start_payload(
        sim=sim,
        feedstock_key="oxide",
        mass_kg=1000.0,
        backend_requested="stub",
        backend_active="StubBackend",
        backend_message="Using StubBackend",
    )

    assert payload["status"] == "started"
    assert payload["mass_kg"] == pytest.approx(1000.0)
    assert isinstance(payload["mass_kg"], numbers.Real)


def test_tick_payload_keeps_existing_kg_keys():
    sim = _sim()
    tick_payload = _required_event_helper("_tick_payload")
    snapshot = sim._make_snapshot()

    payload = tick_payload(
        sim=sim,
        snapshot=snapshot,
        backend_message="Using StubBackend",
    )

    expected_keys = {
        "melt_mass_kg",
        "raw_inventory_kg",
        "residual_inventory_kg",
        "stage0_products_kg",
        "drain_tap_kg",
        "stage0_mass_balance_delta_kg",
        "process_buckets_kg",
        "evap_total_kg_hr",
        "oxygen_kg",
        "mass_balance_error_pct",
        "O2_vented_kg_hr",
        "O2_vented_cumulative_kg",
        "O2_stored_kg",
        "melt_offgas_O2_stored_kg",
        "melt_offgas_O2_vented_kg",
        "mre_anode_O2_stored_kg",
        "shuttle_injected_kg_hr",
        "shuttle_reduced_kg_hr",
        "shuttle_metal_produced_kg_hr",
        "shuttle_K_inventory_kg",
        "shuttle_Na_inventory_kg",
    }
    assert expected_keys <= payload.keys()
    for key in expected_keys:
        _assert_numeric_leaf_values(payload[key], key)


def test_completion_payload_keeps_existing_mass_keys():
    sim = _sim()

    payload = events._completion_payload(sim)

    expected_keys = {
        "oxygen_kg",
        "oxygen_stored_kg",
        "oxygen_vented_kg",
        "mass_in_kg",
        "mass_out_kg",
        "mass_balance_error_pct",
        "residual_inventory_kg",
        "stage0_mass_balance_delta_kg",
        "products",
        "terminal_slag_kg",
    }
    assert expected_keys <= payload.keys()
    for key in expected_keys:
        _assert_numeric_leaf_values(payload[key], key)
