"""Keep DeMaria melt recipes on the sample analysis carried by each extract row."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from simulator.diagnostic_helpers.extract_reproduction import (
    MOLAR_MASS,
    AdoptedObservation,
    _literature_pressure_points,
    _melt_recipe_mol,
    evaluate_observation,
    geometry_assumption_text,
    load_cached_safe_yaml,
    load_vapor_pressure_data,
)


EXTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "literature"
    / "extracts"
    / "kems-022-demaria-1971.yaml"
)


def _observation(species: str, observation_id: str) -> AdoptedObservation:
    extract = load_cached_safe_yaml(EXTRACT_PATH.read_text(encoding="utf-8"))
    row = next(
        row
        for row in extract["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )
    return AdoptedObservation(
        species_id=species,
        source_id=extract["source_id"],
        observation_id=observation_id,
        obs_type=row["type"],
        review_status=None,
        phase=row.get("phase"),
        regime=row.get("regime"),
        standard_state=row.get("standard_state"),
        T_range_K=row.get("T_range_K"),
        units=row.get("units"),
        uncertainty=row.get("uncertainty"),
        locator=row.get("locator") or {},
        values=row.get("values") or {},
        equipment=row.get("equipment") or {},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
    )


def test_na_12022_uses_located_charge_and_12065_stays_missing() -> None:
    sample_12022 = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )
    points, skip, drops = _literature_pressure_points(sample_12022)
    assert skip is None
    assert not drops
    assert points is not None and len(points) == 6
    recipe, source, error = _melt_recipe_mol(sample_12022, point=points[0])
    assert error is None
    assert recipe is not None
    assert "point_conditions.composition" in source
    assert "initial_charge_only" in source
    assert recipe["SiO2"] == pytest.approx(42.3 / MOLAR_MASS["SiO2"])
    assert recipe["Na2O"] == pytest.approx(0.23 / MOLAR_MASS["Na2O"])

    sample_12065 = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12065_digitized"
    )
    recipe, source, error = _melt_recipe_mol(sample_12065)
    assert recipe is None
    assert source == "extract composition_source_gap"
    assert error == "missing_condition:melt_composition"

    evaluation = evaluate_observation(
        sample_12065, vapor_pressure_data=load_vapor_pressure_data()
    )
    assert evaluation.skip_reason is not None
    assert evaluation.skip_reason.startswith("typed-refusal:")
    assert evaluation.records
    assert all(record.actual_value is None for record in evaluation.records)


def test_admitted_psat_reports_missing_sample_composition() -> None:
    obs = AdoptedObservation(
        species_id="Na",
        source_id="kems-022-demaria-1971",
        observation_id="fixture_apollo_12_missing_bulk_suite",
        obs_type="psat_series",
        review_status=None,
        phase="lunar_basalt_melt",
        regime="kems_effusion",
        standard_state="partial pressure over multicomponent lunar basalt melt",
        T_range_K=(1400.0, 1400.0),
        units="Pa",
        uncertainty=None,
        locator={},
        values={
            "material": "Apollo_12_12022",
            "gas_species": "Na(g)",
            "admission_status": "admitted",
            "method_class": "measured_direct",
            "sample_Na2O_wt_pct": 0.36,
            "sample_K2O_wt_pct": 0.068,
            "points": [{"T_K": 1400.0, "p_Pa": 1.0e-2}],
        },
        equipment={},
        disagreement_dex=None,
        is_priority_winner=True,
        geometry_assumption=geometry_assumption_text(),
    )

    evaluation = evaluate_observation(
        obs, vapor_pressure_data=load_vapor_pressure_data()
    )

    assert evaluation.skip_reason == (
        "typed-refusal:missing_condition:melt_composition"
    )
    assert evaluation.records
    assert all(record.actual_value is None for record in evaluation.records)


def test_pressure_points_use_located_or_same_sample_oxide_suites() -> None:
    obs = _observation("Al2O", "demaria_1971_al2o_psat_table4")
    obs = replace(
        obs,
        values={
            **obs.values,
            "sample_Na2O_wt_pct": 99.0,
            "sample_K2O_wt_pct": 99.0,
        },
    )

    points, skip, drops = _literature_pressure_points(obs)

    assert skip is None
    assert not drops
    assert points is not None
    point_12022 = next(point for point in points if point["T_K"] == 2060.0)
    recipe_12022, source_12022, error = _melt_recipe_mol(obs, point=point_12022)
    assert error is None
    assert recipe_12022 is not None
    assert "point_conditions.composition" in source_12022
    assert "initial_charge_only" in source_12022
    assert recipe_12022["Al2O3"] == pytest.approx(8.2 / MOLAR_MASS["Al2O3"])
    assert recipe_12022["Na2O"] == pytest.approx(0.23 / MOLAR_MASS["Na2O"])

    point_12065 = next(
        point
        for point in points
        if point.get("composition_source_observation_id")
        == "smales_1971_apollo_12065_bulk_composition_tables1_2_3"
    )
    recipe_12065, source_12065, error = _melt_recipe_mol(obs, point=point_12065)
    assert error is None
    assert recipe_12065 is not None
    assert source_12065.startswith("extract sample_oxide_composition_wt_pct")
    assert "smales_1971_apollo_12065_bulk_composition_tables1_2_3" in source_12065
    assert recipe_12065["Al2O3"] == pytest.approx(9.8 / MOLAR_MASS["Al2O3"])
    assert recipe_12065["Na2O"] == pytest.approx(0.24 / MOLAR_MASS["Na2O"])
