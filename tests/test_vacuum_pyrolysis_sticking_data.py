from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator import condensation
from simulator.condensation import CondensationModel
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


DATA_PATH = Path("data/literature/vacuum_pyrolysis_sticking.yaml")


def _load_sticking_data() -> dict:
    return yaml.safe_load(DATA_PATH.read_text(encoding="utf-8"))


def test_wall_sticking_coefficients_are_cited_or_uncertified():
    data = _load_sticking_data()
    assert data["schema_version"] == 1
    assert set(data["species"]) == {
        "Fe",
        "SiO",
        "CrO2",
        "Mg",
        "Na",
        "K",
        "Ca",
        "Mn",
        "Cr",
    }

    for species, row in data["species"].items():
        assert row["status"] in {"CITED", "UNCERTIFIED"}, species
        value = row["value"]
        if isinstance(value, dict):
            assert value["form"] == "arrhenius", species
            assert value["status"] in {"CITED", "UNCERTIFIED"}, species
            assert value["valid_range_K"] == [1000, 1800], species
            assert value["uncertainty_envelope"] == pytest.approx(
                [0.003, 0.067]
            )
            assert value["cite"], species
        else:
            assert 0.0 <= float(value) <= 1.0, species
        assert row["source"], species
        assert row["source_class"], species
        assert row["temperature_range_K"], species
        assert row["uncertainty_flag"], species
        if row["status"] == "UNCERTIFIED":
            assert row["output_status"] == "status_bearing", species
        else:
            assert row["source_url"], species


def test_sio_sticking_alpha_s_uses_grounded_arrhenius_form():
    expected = {
        1000.0: 0.52 * math.exp(-3685.0 / 1000.0),
        1500.0: 0.52 * math.exp(-3685.0 / 1500.0),
        1800.0: 0.52 * math.exp(-3685.0 / 1800.0),
    }

    values = []
    for T_K, expected_alpha in expected.items():
        context: dict[str, object] = {}
        value = condensation.alpha_s("SiO", T_K, context)
        values.append(value)
        assert value == pytest.approx(expected_alpha, rel=1e-12)
        evaluation = context["alpha_s_evaluation"]
        assert evaluation["alpha_s_form"] == "arrhenius"
        assert evaluation["alpha_s_extrapolated"] is False
        assert evaluation["alpha_s_valid_range_K"] == [1000.0, 1800.0]

    assert values == sorted(values)


def test_sio_sticking_alpha_s_records_cold_wall_extrapolation():
    context: dict[str, object] = {}

    value = condensation.alpha_s("SiO", 900.0, context)

    assert value == pytest.approx(0.52 * math.exp(-3685.0 / 900.0))
    assert context["alpha_s_evaluation"]["alpha_s_extrapolated"] is True


def test_non_sio_sticking_alpha_s_keeps_scalar_value():
    assert condensation.alpha_s("Fe", 1500.0, {}) == pytest.approx(0.02)


def test_malformed_sticking_coefficient_spec_fails_loud():
    with pytest.raises(ValueError, match="malformed"):
        condensation.alpha_s(
            "SiO",
            1500.0,
            {"coefficient_spec": {"form": "arrhenius", "A": 0.52}},
        )


@pytest.mark.parametrize("missing_field", ["valid_range_K", "uncertainty_envelope", "cite", "status"])
def test_arrhenius_sticking_coefficient_metadata_is_required(missing_field):
    spec = dict(_load_sticking_data()["species"]["SiO"]["value"])
    spec.pop(missing_field)

    with pytest.raises(ValueError, match="malformed"):
        condensation.alpha_s("SiO", 1500.0, {"coefficient_spec": spec})


def test_wall_deposit_reactivity_classes_are_explicit():
    data = _load_sticking_data()
    classes = data["reactivity_class_by_species"]

    assert set(classes) == {
        "SiO",
        "Na",
        "K",
        "Fe",
        "Mg",
        "Ca",
        "Mn",
        "Cr",
        "Al",
        "Ti",
        "CrO2",
    }
    assert classes["SiO"] == "reactive"
    for species in ("Na", "K", "Fe", "Mg", "Ca", "Mn", "Cr", "Al", "Ti"):
        assert classes[species] == "physisorbing"
    assert classes["CrO2"] == "physisorbing"


def test_material_defaults_reference_sticking_sidecar():
    materials = yaml.safe_load(Path("data/materials.yaml").read_text())
    defaults = materials["default_alpha_s_by_species"]
    for species, entry in defaults.items():
        assert set(entry) == {"value_ref"}
        assert entry["value_ref"] == (
            "data/literature/vacuum_pyrolysis_sticking.yaml::"
            f"species.{species}.value"
        )


def test_legacy_by_feel_sticking_constant_text_is_gone():
    source = Path("simulator/condensation.py").read_text(encoding="utf-8")
    materials = Path("data/materials.yaml").read_text(encoding="utf-8")

    assert "Ungrounded fitted/by-feel assumptions" not in source
    assert "legacy_species_default_proxy" not in materials
    assert "STICKING_COEFF/default alpha_s values are ungrounded" not in materials


def test_grounded_sio_alpha_drives_wall_deposit_direction(monkeypatch):
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0

    def route_sio() -> float:
        route = model.route(
            EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
            melt,
        )
        return float(route.wall_deposit_by_species["SiO"])

    grounded = route_sio()
    monkeypatch.setitem(
        condensation.STICKING_DATA["species"]["SiO"],
        "value",
        0.7,
    )
    legacy = route_sio()

    assert grounded == pytest.approx(0.00016629976155468562, rel=1e-12)
    assert legacy == pytest.approx(0.03398921856191324, rel=1e-12)
    assert grounded < legacy


def test_capture_budget_regularizer_is_marked_numerical_uncertified():
    data = _load_sticking_data()
    floor = data["capture_budget_regularizer_floor"]

    assert floor["value"] == pytest.approx(0.01)
    assert floor["status"] == "UNCERTIFIED"
    assert floor["source_class"] == "numerical_regularizer_not_literature_constant"
    assert floor["output_status"] == "uncertainty_only"
    assert condensation.CAPTURE_BUDGET_REGULARIZER_NOTICE[
        "citation_status"
    ] == "UNCERTIFIED"
