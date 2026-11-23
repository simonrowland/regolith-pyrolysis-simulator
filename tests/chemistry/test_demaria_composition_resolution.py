"""Keep DeMaria melt recipes on the sample analysis carried by each extract row."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery import migrate as battery_migrate
from simulator.diagnostic_helpers.extract_reproduction import (
    AdoptedObservation,
    _literature_pressure_points,
    _melt_recipe_mol,
    evaluate_observation,
    geometry_assumption_text,
    load_cached_safe_yaml,
    load_vapor_pressure_data,
    observation_admission_reason,
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


def test_na_12022_uses_located_charge_and_12065_stays_missing(monkeypatch) -> None:
    sample_12022 = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )
    points_12022, skip, drops = _literature_pressure_points(sample_12022)
    assert skip is None
    assert not drops
    assert points_12022 is not None and len(points_12022) == 6
    recipe, source, error = _melt_recipe_mol(
        sample_12022, point=points_12022[0]
    )
    assert error is None
    assert recipe is not None
    assert "point_conditions.composition" in source
    assert "initial_charge_only" in source
    composition_payload = points_12022[0]["point_conditions"]["composition"][
        "state"
    ]["value"]
    expected_recipe = battery_migrate._composition_from_plain(
        composition_payload
    ).as_map()
    assert set(recipe) == {
        "SiO2",
        "TiO2",
        "Al2O3",
        "FeO",
        "MnO",
        "MgO",
        "CaO",
        "Na2O",
        "K2O",
    }
    assert recipe == pytest.approx(
        {name: float(amount) for name, amount in expected_recipe.items()}
    )

    sample_12065 = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12065_digitized"
    )
    recipe, source, error = _melt_recipe_mol(sample_12065)
    assert recipe is None
    assert source == "extract composition_source_gap"
    assert error == "missing_condition:melt_composition"
    points_12065, skip, drops = _literature_pressure_points(sample_12065)
    assert skip is None
    assert not drops
    assert points_12065

    import simulator.diagnostic_helpers.extract_reproduction as er

    def _unexpected_engine_call(*args, **kwargs):
        pytest.fail("12065 without a bulk composition must not call the engine")

    monkeypatch.setattr(er, "_engine_melt_psat_pa", _unexpected_engine_call)
    evaluation = evaluate_observation(
        sample_12065, vapor_pressure_data=load_vapor_pressure_data()
    )
    assert evaluation.skip_reason == (
        "typed-refusal:missing_condition:melt_composition"
    )
    assert len(evaluation.records) == len(points_12065)
    assert all(record.actual_value is None for record in evaluation.records)


def test_digitized_12022_entry_point_emits_flagged_record_for_every_point(
    monkeypatch,
) -> None:
    import simulator.diagnostic_helpers.extract_reproduction as er

    obs = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )
    engine_calls: list[tuple[str, float, float, dict[str, float]]] = []

    def _engine_stub(species, temperature_K, pO2_bar, _vp_data, *, account_mol):
        engine_calls.append(
            (str(species), float(temperature_K), float(pO2_bar), dict(account_mol))
        )
        return 1.0, None, {"provider_status": "ok"}

    comparisons: list[tuple[bool, dict[str, object]]] = []
    real_compare_point = er._compare_point

    def _capture_comparison(**kwargs):
        comparisons.append((kwargs["assumed_input"], kwargs["runtime"]))
        return real_compare_point(**kwargs)

    monkeypatch.setattr(er, "_engine_melt_psat_pa", _engine_stub)
    monkeypatch.setattr(er, "_compare_point", _capture_comparison)

    evaluation = evaluate_observation(
        obs, vapor_pressure_data=load_vapor_pressure_data()
    )

    assert evaluation.skip_reason is not None
    assert evaluation.skip_reason.startswith("typed-refusal:")
    assert evaluation.skip_reason in evaluation.skip_reasons
    assert battery_migrate.INITIAL_CHARGE_ONLY_PROXY_FLAG in evaluation.skip_reason
    assert battery_migrate.INITIAL_CHARGE_ONLY_PROXY_NOTICE in evaluation.skip_reason
    assert "pO2_bar" in evaluation.skip_reason
    assert "contextual" in evaluation.skip_reason.lower()
    assert len(engine_calls) == 6
    assert {call[2] for call in engine_calls} == {5.613405e-9}
    assert len(engine_calls[0][3]) == 9
    assert all(call[3] == engine_calls[0][3] for call in engine_calls)
    assert len(evaluation.records) == 6
    assert all(record.status == "assumed-input" for record in evaluation.records)
    assert all(
        record.status != "unsupported-observable" for record in evaluation.records
    )
    expected_assumption = {
        "flag": battery_migrate.INITIAL_CHARGE_ONLY_PROXY_FLAG,
        "reason": battery_migrate.INITIAL_CHARGE_ONLY_PROXY_NOTICE,
    }
    assert len(comparisons) == 6
    assert all(assumed_input for assumed_input, _runtime in comparisons)
    assert all(
        runtime["input_assumption"]["flag"] == expected_assumption["flag"]
        and runtime["input_assumption"]["reason"] == expected_assumption["reason"]
        and runtime["input_assumption"]["pO2_bar"] == 5.613405e-9
        and "contextual" in runtime["input_assumption"]["pO2_reason"].lower()
        for _assumed_input, runtime in comparisons
    )


def test_initial_charge_role_reader_normalizes_extract_whitespace(monkeypatch) -> None:
    import simulator.diagnostic_helpers.extract_reproduction as er

    obs = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )
    values = deepcopy(obs.values)
    values["composition_role"] = {
        **values["composition_role"],
        "value": " initial_charge_only ",
    }
    obs = replace(obs, values=values)
    monkeypatch.setattr(
        er,
        "_engine_melt_psat_pa",
        lambda *_args, **_kwargs: (1.0, None, {"provider_status": "ok"}),
    )

    evaluation = evaluate_observation(
        obs, vapor_pressure_data=load_vapor_pressure_data()
    )

    assert len(evaluation.records) == 6
    assert {record.status for record in evaluation.records} == {"assumed-input"}


def test_figure_only_psat_without_numeric_points_still_refuses() -> None:
    obs = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )
    assert obs.values["method_class"] == "figure_only"
    assert observation_admission_reason(obs) is None

    no_digitized_values = replace(
        obs, values={**obs.values, "points": []}
    )
    assert observation_admission_reason(no_digitized_values) == (
        "unsupported_observable:figure_only_not_digitized"
    )


def test_located_partial_mass_percent_assay_refuses_like_migration_owner(
    monkeypatch,
) -> None:
    import simulator.diagnostic_helpers.extract_reproduction as er

    obs = _observation(
        "Na", "demaria_1971_na_psat_figure1_sample_12022_digitized"
    )
    points, skip, drops = _literature_pressure_points(obs)
    assert skip is None
    assert not drops
    assert points is not None
    mutated_point = deepcopy(points[0])
    composition = mutated_point["point_conditions"]["composition"]
    composition["state"]["value"]["components"] = [["Na2O", 0.23]]
    with pytest.raises(ValueError) as owner_refusal:
        battery_migrate._composition_from_plain(composition["state"]["value"])
    assert "partial analysis" in str(owner_refusal.value)

    mutated_observation = replace(
        obs, values={**obs.values, "points": [mutated_point]}
    )
    engine_calls: list[object] = []

    def _engine_spy(*args, **kwargs):
        engine_calls.append((args, kwargs))
        return 1.0, None, {"provider_status": "ok"}

    monkeypatch.setattr(er, "_engine_melt_psat_pa", _engine_spy)
    evaluation = evaluate_observation(
        mutated_observation, vapor_pressure_data=load_vapor_pressure_data()
    )

    assert engine_calls == []
    assert evaluation.skip_reason == f"typed-refusal:{owner_refusal.value}"
    assert len(evaluation.records) == 1
    assert evaluation.records[0].actual_value is None


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
    composition_payload = point_12022["point_conditions"]["composition"]["state"][
        "value"
    ]
    expected_12022 = battery_migrate._composition_from_plain(
        composition_payload
    ).as_map()
    assert recipe_12022 == pytest.approx(
        {name: float(amount) for name, amount in expected_12022.items()}
    )

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
    expected_12065 = battery_migrate.wt_pct_to_mole_fraction(
        {
            str(oxide): Decimal(str(wt))
            for oxide, wt in point_12065["sample_oxide_composition_wt_pct"].items()
        }
    ).as_map()
    assert recipe_12065 == pytest.approx(
        {name: float(amount) for name, amount in expected_12065.items()}
    )
