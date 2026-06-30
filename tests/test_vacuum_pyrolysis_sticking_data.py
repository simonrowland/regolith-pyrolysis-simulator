from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator import condensation
from simulator.condensation import CondensationModel
from simulator.state import (
    CondensationStage,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PipeSegment,
)


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


def test_sio_sticking_data_carries_cited_cold_wall_condensation_gate():
    data = _load_sticking_data()
    block = data["species"]["SiO"]["cold_wall_condensation"]

    assert block["value"] == pytest.approx(1.0)
    assert block["status"] == "CITED"
    assert block["uncertainty_envelope"] == pytest.approx([0.016, 1.0])
    assert "Pound 1972" in block["source"]
    assert "no_direct_cold_wall_SiO_measurement" in block["uncertainty_flag"]
    assert "rarely active" in block["uncertainty_flag"]


def test_sio_cold_wall_stage_alpha_uses_unity_condensation_gate():
    data = _load_sticking_data()
    valid_floor_K = data["species"]["SiO"]["value"]["valid_range_K"][0]
    stage = CondensationStage(
        stage_number=1,
        label="cold SiO guard",
        temp_range_C=(500.0, 600.0),
        target_species=["SiO"],
    )
    T_stage_K = condensation._stage_midpoint_temperature_K(stage)

    record = condensation._stage_alpha_record(stage, "SiO")

    assert T_stage_K < valid_floor_K
    assert record["alpha_s"] == pytest.approx(1.0)
    assert record["alpha_s_form"] == "cold_wall_condensation"
    assert record["alpha_s_raw_arrhenius"] == pytest.approx(
        0.52 * math.exp(-3685.0 / T_stage_K)
    )
    assert record["alpha_s_extrapolated"] is True
    assert record["alpha_s_temperature_below_valid_range"] is True
    assert record["alpha_s_condensation_regime"] == "cold_wall_high_supersaturation"
    assert record["alpha_s_cold_wall_validity_floor_K"] == pytest.approx(
        valid_floor_K
    )
    assert record["citation_status"] == "CITED"
    assert record["status"] == "sourced"
    assert record["source_class"] == "cited_high_supersaturation_condensation_limit"
    assert "Pound 1972" in record["source"]
    assert record["cold_wall_condensation"] is True


def test_sio_cold_wall_wall_alpha_uses_unity_condensation_gate():
    segment = PipeSegment(
        name="cold_spot",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=500.0,
        length_m=1.0,
        inner_diameter_m=0.05,
    )

    record = condensation._wall_alpha_record("SiO", segment=segment)

    assert record["alpha_s"] == pytest.approx(1.0)
    assert record["alpha_s_form"] == "cold_wall_condensation"
    assert record["alpha_s_cold_wall_condensation"] is True
    assert record["segment"] == "cold_spot"


def test_sio_pressure_isolated_stage_eval_uses_cold_wall_gate():
    stage = CondensationStage(
        stage_number=1,
        label="cold pressure stage",
        temp_range_C=(500.0, 600.0),
        target_species=["SiO"],
    )
    alpha_record: dict[str, object] = {}

    captured = condensation._pressure_isolated_capture_budget_kg(
        "SiO",
        1.0,
        [stage],
        {1: 1.0},
        temps={"SiO": 1000.0},
        alpha_record_out=alpha_record,
    )

    assert captured > 0.0
    stage_eval = alpha_record["alpha_s_stage_evaluations"]["1"]
    assert stage_eval["alpha_s"] == pytest.approx(1.0)
    assert stage_eval["alpha_s_form"] == "cold_wall_condensation"
    assert stage_eval["alpha_s_cold_wall_condensation"] is True


def test_sio_stage_band_flux_uses_cold_wall_gate(monkeypatch):
    valid_floor_K = _load_sticking_data()["species"]["SiO"]["value"][
        "valid_range_K"
    ][0]
    captured_below_floor_alphas: list[float] = []
    original_flux = condensation._series_resistance_deposition_flux_mol_m2_s

    def capture_flux(species, P_local_pa, T_surface_K, alpha_s, *args, **kwargs):
        if species == "SiO" and T_surface_K < valid_floor_K:
            captured_below_floor_alphas.append(float(alpha_s))
        return original_flux(
            species,
            P_local_pa,
            T_surface_K,
            alpha_s,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        capture_flux,
    )

    model = CondensationModel(CondensationTrain.create_default())
    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(),
    )

    assert route.condensed_for_species("SiO") > 0.0
    assert captured_below_floor_alphas
    assert captured_below_floor_alphas == pytest.approx(
        [1.0] * len(captured_below_floor_alphas)
    )


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

    assert grounded == pytest.approx(0.0012296595884093348, rel=1e-12)
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
