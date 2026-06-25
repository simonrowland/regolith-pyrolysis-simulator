from __future__ import annotations

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
        assert 0.0 <= float(row["value"]) <= 1.0, species
        assert row["source"], species
        assert row["source_class"], species
        assert row["temperature_range_K"], species
        assert row["uncertainty_flag"], species
        if row["status"] == "UNCERTIFIED":
            assert row["output_status"] == "status_bearing", species
        else:
            assert row["source_url"], species


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

    assert grounded == pytest.approx(0.000886103302219211, rel=1e-12)
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
