"""Keep DeMaria melt recipes on the sample analysis carried by each extract row."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from simulator.diagnostic_helpers.extract_reproduction import (
    DEFAULT_PO2_BAR,
    MOLAR_MASS,
    AdoptedObservation,
    _engine_melt_psat_pa,
    _literature_pressure_points,
    _melt_recipe_mol,
    external_sample_analysis_candidates_from_extracts,
    external_sample_analysis_source_ids_from_extracts,
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
EXTRACTS_DIR = EXTRACT_PATH.parent


def _observation(species: str, observation_id: str) -> AdoptedObservation:
    extract = load_cached_safe_yaml(EXTRACT_PATH.read_text(encoding="utf-8"))
    source_ids = external_sample_analysis_source_ids_from_extracts((extract,))
    companion_extracts = [
        load_cached_safe_yaml((EXTRACTS_DIR / f"{source_id}.yaml").read_text(encoding="utf-8"))
        for source_id in source_ids
    ]
    candidates = external_sample_analysis_candidates_from_extracts(
        companion_extracts
    )
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
        external_sample_analysis_candidates=candidates,
    )


def test_na_sample_composition_gaps_refuse_the_mare_proxy() -> None:
    obs = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )

    recipe, source, error = _melt_recipe_mol(obs)

    assert recipe is None
    assert source == "extract composition_source_gap"
    assert error == "missing_condition:melt_composition"

    evaluation = evaluate_observation(
        obs, vapor_pressure_data=load_vapor_pressure_data()
    )
    assert evaluation.skip_reason is not None
    assert evaluation.skip_reason.startswith("typed-refusal:")
    assert evaluation.records
    assert all(record.actual_value is None for record in evaluation.records)


def test_external_sample_analysis_enables_vapor_pressure_prediction() -> None:
    obs = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12065_digitized"
    )

    recipe, source, error = _melt_recipe_mol(obs)
    assert recipe is not None
    assert error is None
    assert source.startswith("extract sample_oxide_composition_wt_pct")
    assert "maxwell_wiik_1971_apollo12_sample_12065_84_gsf_bulk_composition_table1" in source

    same_sample_recipe, same_sample_source, same_sample_error = _melt_recipe_mol(
        obs, point={"sample": "12065"}
    )
    assert same_sample_recipe is not None
    assert same_sample_error is None
    assert same_sample_source == source

    unmatched_recipe, unmatched_source, unmatched_error = _melt_recipe_mol(
        obs, point={"sample": "12022"}
    )
    assert unmatched_recipe is None
    assert unmatched_source == "external_sample_analysis"
    assert unmatched_error is not None
    assert "do not match exactly" in unmatched_error

    pressure, refusal, runtime = _engine_melt_psat_pa(
        "Na",
        1673.0,
        DEFAULT_PO2_BAR,
        load_vapor_pressure_data(),
        account_mol=recipe,
    )
    assert pressure is not None, f"refusal={refusal!r}; runtime={runtime!r}"
    assert pressure > 0.0
    assert refusal is None
    assert runtime["provider_id"] == "builtin-vapor-pressure"
    assert runtime["provider_status"] == "ok"

    defaulted_values = dict(obs.values)
    defaulted_values.pop("pO2_bar", None)
    # This fixture isolates the missing-pO2 model default from the row's
    # existing figure_only admission marker; the direct engine assertion above
    # separately exercises the exact linked sample composition.
    defaulted_values["method_class"] = "measured_direct"
    defaulted_values["points"] = [
        {"T_K": 1673.0, "p_Pa": 1.0e-8, "sample": "12065"}
    ]
    defaulted_obs = replace(
        obs,
        T_range_K=(1673.0, 1673.0),
        values=defaulted_values,
    )
    evaluation = evaluate_observation(
        defaulted_obs, vapor_pressure_data=load_vapor_pressure_data()
    )
    assert evaluation.records
    predictions = [
        record for record in evaluation.records if record.actual_value is not None
    ]
    assert predictions, (
        f"skip={evaluation.skip_reason!r}; notes={evaluation.runtime_notes!r}"
    )
    prediction = predictions[0]
    assert prediction.status == "assumed-input"
    assert any(
        "psat diagnostic pO2 defaulted to 1e-08 bar (missing_condition:pO2_boundary)"
        in note
        for note in evaluation.runtime_notes
    )


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
            "composition_source_gap": "full suite reported elsewhere (Smales 1971)",
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


def test_pressure_points_use_exact_sample_analysis_oxide_suites() -> None:
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
    recipes = {}
    for (
        source_observation_id,
        al2o3_wt_pct,
        tio2_wt_pct,
        na2o_wt_pct,
        k2o_wt_pct,
    ) in (
        (
            "smales_1971_apollo_12022_bulk_composition_tables1_2_3",
            8.2,
            4.9,
            0.23,
            0.061,
        ),
        (
            "smales_1971_apollo_12065_bulk_composition_tables1_2_3",
            10.05,
            3.34,
            0.27,
            0.073,
        ),
    ):
        point = next(
            point
            for point in points
            if (
                point.get("composition_source_observation_id") == source_observation_id
                or point.get("composition_source_id")
                == "maxwell-wiik-1971-apollo12-major-composition"
            )
        )
        recipe, source, error = _melt_recipe_mol(obs, point=point)

        assert error is None
        assert source.startswith("extract sample_oxide_composition_wt_pct")
        if source_observation_id.startswith("smales") and "12022" in source_observation_id:
            assert source_observation_id in source
        else:
            assert "maxwell_wiik_1971_apollo12_sample_12065_84_gsf_bulk_composition_table1" in source
        # Convert the cited wt% values to oxide moles using each molar mass.
        assert recipe is not None
        assert recipe["Al2O3"] == pytest.approx(
            al2o3_wt_pct / MOLAR_MASS["Al2O3"]
        )
        assert recipe["TiO2"] == pytest.approx(
            tio2_wt_pct / MOLAR_MASS["TiO2"]
        )
        assert recipe["Na2O"] == pytest.approx(
            na2o_wt_pct / MOLAR_MASS["Na2O"]
        )
        assert recipe["K2O"] == pytest.approx(k2o_wt_pct / MOLAR_MASS["K2O"])
        recipes[source_observation_id] = recipe

    sample_12022 = recipes["smales_1971_apollo_12022_bulk_composition_tables1_2_3"]
    sample_12065 = recipes["smales_1971_apollo_12065_bulk_composition_tables1_2_3"]
    assert sample_12022["Al2O3"] < sample_12065["Al2O3"]
    assert sample_12022["TiO2"] > sample_12065["TiO2"]
