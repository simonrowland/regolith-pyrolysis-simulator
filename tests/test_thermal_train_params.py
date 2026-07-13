from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.thermal_train import (
    THERMAL_TRAIN_SCHEMA_VERSION,
    load_thermal_train_parameters,
    normalize_thermal_train_parameters,
    thermal_train_parameters_from_mapping,
)


PARAMS_PATH = Path("data/thermal_train_params.yaml")


def _raw() -> dict:
    return yaml.safe_load(PARAMS_PATH.read_text(encoding="utf-8"))


def test_default_thermal_train_parameters_are_closed_tagged_and_frozen() -> None:
    normalized = load_thermal_train_parameters()
    params = thermal_train_parameters_from_mapping(_raw())
    assert thermal_train_parameters_from_mapping(normalized) == params
    assert normalized["schema_version"] == THERMAL_TRAIN_SCHEMA_VERSION
    assert params.eta_2ndlaw == 0.30
    assert params.knudsen_locations["duct"]["Kn_threshold"] == 0.01
    assert params.display_prices.radiator_cost_per_m2 == 500.0
    assert params.display_prices.o2_storage_keeping_cost_per_kg == {
        "warm_overburden": 0.5,
        "psr": 0.0,
    }
    with pytest.raises(TypeError):
        params.knudsen_locations["duct"]["L_m"] = 1.0


def test_parameter_schema_rejects_unknown_and_missing_keys() -> None:
    payload = _raw()
    payload["parameters"]["orphan"] = {"value": 1, "units": "x", "source_tag": "assumption"}
    with pytest.raises(ValueError, match="exactly"):
        normalize_thermal_train_parameters(payload, source="test")
    payload = _raw()
    del payload["display_prices"]["cryo_cost_per_W"]
    with pytest.raises(ValueError, match="exactly"):
        normalize_thermal_train_parameters(payload, source="test")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, True])
def test_parameter_schema_rejects_nonfinite_negative_and_bool_values(bad) -> None:
    payload = _raw()
    payload["parameters"]["eta_isen"]["value"] = bad
    with pytest.raises((TypeError, ValueError)):
        normalize_thermal_train_parameters(payload, source="test")


def test_assumption_and_ratification_source_tags_are_required() -> None:
    payload = _raw()
    payload["parameters"]["emissivity"]["source_tag"] = "NIST"
    with pytest.raises(ValueError, match="assumption"):
        normalize_thermal_train_parameters(payload, source="test")
    payload = _raw()
    payload["display_prices"]["radiator_cost_per_m2"]["source_tag"] = "assumption"
    with pytest.raises(ValueError, match="owner-ratified"):
        normalize_thermal_train_parameters(payload, source="test")


def test_parameter_range_relationships_fail_closed() -> None:
    payload = _raw()
    payload["parameters"]["P_discharge_Pa"]["value"] = 50.0
    with pytest.raises(ValueError, match="must exceed"):
        thermal_train_parameters_from_mapping(payload)
    payload = _raw()
    payload["parameters"]["T_frost_K"]["value"] = 60.0
    with pytest.raises(ValueError, match="triple point"):
        thermal_train_parameters_from_mapping(payload)
